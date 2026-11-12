"""Find `String::from("literal")` sites that LLVM inlined as alloc + fixed-size copies.

    movz w0, #N ; movz w1, #1 ; bl __rust_alloc ; cbz x0, ..
    adrp xR ; add xR          ; ldr q0, [xR] ; str q0, [x0] ; (tail bytes as immediates)

The literal's bytes are partly code, so such a site cannot be repointed. It is
recorded as (N, destination stores) when the stores into the new buffer cover
exactly [0, N): patch.py then replaces the first store with a call to a small
copy routine and drops the others.

The String's length is a separate `movz wX, #N`. It is only rewritten when the
site is understood completely: xX is stored as cap and len around the buffer
pointer ({cap, ptr, len} at o, o+8, o+16), or passed as the length argument
right after the pointer. Otherwise N stays and a translation must fit in N
bytes (it is padded with spaces).

usage: inline.py BIN refs.pkl inline.json
"""
import json
import pickle
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402

SCAN = 20


def store(v):
    """Decode a store instruction -> (base reg, offset, size) or None."""
    rn = (v >> 5) & 31
    if (v & 0x3B000000) == 0x39000000 and not (v >> 22) & 3:  # str (unsigned offset): b/h/w/x and SIMD b..d
        size = 1 << (v >> 30)
        return rn, ((v >> 10) & 0xFFF) * size, size
    if (v & 0xFFC00000) == 0x3D800000:  # str q
        return rn, ((v >> 10) & 0xFFF) * 16, 16
    if (v & 0x3B200C00) == 0x38000000 and not (v >> 22) & 3:  # stur
        off = (v >> 12) & 0x1FF
        return rn, off - 512 if off & 256 else off, 1 << (v >> 30)
    if (v & 0xFFE00C00) == 0x3C800000:  # stur q
        off = (v >> 12) & 0x1FF
        return rn, off - 512 if off & 256 else off, 16
    if (v & 0x3FC00000) in (0x29000000, 0x2D000000):  # stp (signed offset), gpr / simd
        opc = v >> 30
        simd = (v >> 26) & 1
        size = (4 << opc) if simd else (8 if opc == 2 else 4)
        if (not simd and opc not in (0, 2)) or opc == 3:
            return None
        off = (v >> 15) & 0x7F
        off = (off - 128 if off & 64 else off) * size
        return rn, off, size * 2
    return None


def is_branch(v):
    return ((v & 0x7C000000) == 0x14000000 or (v & 0xFF000010) == 0x54000000 or (v & 0x7E000000) == 0x34000000
            or (v & 0x7E000000) == 0x36000000 or (v & 0xFE000000) == 0xD6000000)


def length_ops(ins, j, n, end, alias):
    """Instructions holding N for the site whose alloc call is at j, or None."""
    mz = lambda v: (v & 0x7FE00000) == 0x52800000 and ((v >> 5) & 0xFFFF) == n
    w0 = [k for k in (j - 1, j - 2) if mz(int(ins[k])) and (int(ins[k]) & 31) == 0]
    if len(w0) != 1 or any(mz(int(ins[k])) for k in range(j - 10, j) if k != w0[0]):
        return None  # N also lives in another register set earlier
    alias = set(alias)
    slots, x, xi = {}, None, None
    for q in range(end + 1, end + 15):
        v = int(ins[q])
        if is_branch(v):
            break
        if mz(v):
            if x is not None:
                return None
            x, xi = v & 31, q
            continue
        if (v & 0xFFE0FFE0) == 0xAA0003E0 and ((v >> 16) & 31) in alias:
            alias.add(v & 31)
            if x is not None and (v & 31) <= 6 and x == (v & 31) + 1:
                return [w0[0], xi]
            continue
        if (v & 0xFFC00000) == 0xF9000000:
            slots[((v >> 5) & 31, ((v >> 10) & 0xFFF) * 8)] = v & 31
        elif (v & 0xFFC00000) == 0xA9000000:
            off = (v >> 15) & 0x7F
            off = (off - 128 if off & 64 else off) * 8
            slots[((v >> 5) & 31, off)] = v & 31
            slots[((v >> 5) & 31, off + 8)] = (v >> 10) & 31
    if x is None:
        return None
    if x <= 7 and (x - 1) in alias and x - 1 <= 6:
        return [w0[0], xi]
    for (b, o), r in slots.items():
        if r == x and slots.get((b, o + 8)) in alias and slots.get((b, o + 16)) == x and x not in alias:
            return [w0[0], xi]
    return None


def main(binpath, refs, out):
    data = open(binpath, "rb").read()
    m = MachO(data)
    text = m.sect("__TEXT", "__text")
    ins = np.frombuffer(data, dtype="<u4", count=text.size // 4, offset=text.offset)
    R = pickle.load(open(refs, "rb"))
    base = m.seg("__TEXT")["vmaddr"]
    target_of = {i: t for i, t, _, _ in R["code_refs"]}

    # __rust_alloc: the most common callee of `movz w1,#1 ; bl`
    from collections import Counter
    bls = np.nonzero(((ins & 0xFC000000) == 0x94000000)[1:] & (ins[:-1] == 0x52800021))[0] + 1
    d = (ins[bls] & 0x3FFFFFF).astype(np.int64)
    d = np.where(d & (1 << 25), d - (1 << 26), d)
    alloc, _ = Counter((bls + d).tolist()).most_common(1)[0]
    sites, why = [], Counter()
    for j in bls[(bls + d) == alloc].tolist():
        n = None
        for k in (j - 1, j - 2):
            v = int(ins[k])
            if (v & 0xFFE0001F) == 0x52800000:
                n = (v >> 5) & 0xFFFF
        if not n or n < 3:
            continue
        if (int(ins[j + 1]) & 0xFF00001F) != 0xB4000000:  # cbz x0
            continue
        alias, stores, src, cover = {0}, [], None, set()
        for q in range(j + 2, j + 2 + SCAN):
            v = int(ins[q])
            if is_branch(v):
                break
            if q in target_of and src is None:
                src = q
            if (v & 0xFFE0FFE0) == 0xAA0003E0 and ((v >> 16) & 31) in alias:  # mov xK, x(alias)
                alias.add(v & 31)
                continue
            s = store(v)
            if s and s[0] in alias and 0 <= s[1] and s[1] + s[2] <= n:
                stores.append((q, s[0], s[1]))
                cover.update(range(s[1], s[1] + s[2]))
            elif s is None and (v & 31) in alias and (v & 0x1F000000) in (0x11000000, 0x0A000000, 0x12000000, 0x10000000):
                alias.discard(v & 31)  # the register is rewritten
            if len(cover) == n:
                break
        if src is None:
            why["no literal"] += 1
            continue
        if len(cover) != n or not stores:
            why["partial copy"] += 1
            continue
        t = target_of[src]
        raw = data[t - base:t - base + n]
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            why["not text"] += 1
            continue
        first = min(stores)  # earliest store: its base register is the buffer there
        lens = length_ops(ins, j, n, max(stores)[0], alias)
        why["length known" if lens else "length fixed"] += 1
        sites.append([s, n, first[0], first[1], [q for q, _, _ in stores if q != first[0]], lens])
        why["ok"] += 1
    print(dict(why), "alloc at insn", alloc)
    json.dump(sites, open(out, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main(*sys.argv[1:4])
