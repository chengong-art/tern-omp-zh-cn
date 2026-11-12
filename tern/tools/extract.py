"""Resolve every string reference to (address, length, how to rewrite it).

A reference is only kept when the length operand is located with a structural
rule, so that patch.py can repoint it to a translated copy:

  data  ptr,len pair in a data section            -> rewrite both words
  A     adrp/add xR ; movz w(R+1), #len           -> argument registers
  B     adrp/add xR ; str xR,[b,#o] ; movz wM,#len ; str xM,[b,#o+8]
  C     adrp/add xR ; movz wM,#len ; stp xR,xM,[b,#o]
  D     adrp/add xR ; csel xD,xR,.. ; movz wM,#len ; csel x(D+1),xM,..
  table a run of bare pointers in data whose lengths sit in a parallel u64 table
        in __TEXT,__const (LLVM's lookup tables for `match` returning &str)

Output: strings.pkl  {addr: {len: [ref, ...]}}  and stats on stdout.
"""
import pickle
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402

FWD, BACK = 12, 8


def is_branch(v):
    return ((v & 0x7C000000) == 0x14000000      # b / bl
            or (v & 0xFF000010) == 0x54000000   # b.cond
            or (v & 0x7E000000) == 0x34000000   # cbz / cbnz
            or (v & 0x7E000000) == 0x36000000   # tbz / tbnz
            or (v & 0xFE000000) == 0xD6000000)  # br / blr / ret


def movz(v):
    if (v & 0x7FE00000) == 0x52800000:
        return v & 31, (v >> 5) & 0xFFFF
    return None


def str_x(v):
    """str xT, [xN, #off] (unsigned offset form) -> (rt, rn, off)"""
    if (v & 0xFFC00000) == 0xF9000000:
        return v & 31, (v >> 5) & 31, ((v >> 10) & 0xFFF) * 8
    return None


def stp_x(v):
    """stp xT, xT2, [xN, #off] (signed offset form) -> (rt, rt2, rn, off)"""
    if (v & 0xFFC00000) == 0xA9000000:
        imm = (v >> 15) & 0x7F
        if imm & 0x40:
            imm -= 0x80
        return v & 31, (v >> 10) & 31, (v >> 5) & 31, imm * 8
    return None


def writes_reg(v, r):
    """Conservative: does this instruction look like it writes general register r?"""
    if movz(v) and (v & 31) == r:
        return True
    if (v & 0x9F000000) == 0x90000000 and (v & 31) == r:  # adrp
        return True
    if (v & 0x7F000000) in (0x11000000, 0x2A000000, 0x0A000000, 0x4A000000) and (v & 31) == r:
        return True  # add/orr/and/eor forms (mov alias)
    if (v & 0x3B000000) == 0x39000000 and (v & 0x00400000) and (v & 31) == r:  # ldr
        return True
    return False


def resolve(ins, n, i, R):
    """Return (rule, movz insn index, len) for the adrp at i (add at i+1), or None."""
    # forward window, stop at the first branch (inclusive of instructions before it)
    fwd = []
    for j in range(i + 2, min(n, i + 2 + FWD)):
        v = int(ins[j])
        fwd.append((j, v))
        if is_branch(v):
            break
    back = []
    for j in range(i - 1, max(-1, i - 1 - BACK), -1):
        v = int(ins[j])
        if is_branch(v):
            break
        back.append((j, v))

    # Rule B: ptr stored, len stored 8 bytes above through a movz (either order)
    for k, (j, v) in enumerate(fwd):
        s = str_x(v)
        if s and s[0] == R:
            _, base, off = s
            for k2 in range(len(fwd)):
                s2 = str_x(fwd[k2][1])
                if k2 != k and s2 and s2[1] == base and s2[2] == off + 8:
                    M = s2[0]
                    for k3 in range(k2 - 1, -1, -1):  # nearest movz to M before its store
                        mz = movz(fwd[k3][1])
                        if mz and mz[0] == M:
                            return "B", fwd[k3][0], mz[1]
                        if writes_reg(fwd[k3][1], M):
                            break
                    break
            break
        if writes_reg(v, R):
            break

    # Rule C: stp xR, xM
    for k, (j, v) in enumerate(fwd):
        s = stp_x(v)
        if s and s[0] == R:
            M = s[1]
            seq = [x for x in reversed(fwd[:k])] + back
            for (j3, v3) in seq:
                mz = movz(v3)
                if mz and mz[0] == M:
                    return "C", j3, mz[1]
                if writes_reg(v3, M):
                    break
            break
        if writes_reg(v, R):
            break

    # Rule D: csel xD, xR, .. paired with csel x(D+1), xM, .. under the same condition
    def csel(v):
        if (v & 0xFFE00C00) == 0x9A800000:
            return v & 31, (v >> 5) & 31, (v >> 16) & 31, (v >> 12) & 15
        return None

    for k, (j, v) in enumerate(fwd):
        c = csel(v)
        if c and R in (c[1], c[2]):
            pos = 1 if c[1] == R else 2
            if c[1] == c[2]:
                break
            seq = sorted(back + fwd)
            for k2, (j2, v2) in enumerate(seq):
                c2 = csel(v2)
                if c2 and j2 != j and c2[0] == c[0] + 1 and c2[3] == c[3]:
                    M = c2[pos]
                    for k3 in range(k2 - 1, -1, -1):
                        if seq[k3][0] in (i, i + 1):
                            continue
                        mz = movz(seq[k3][1])
                        if mz and mz[0] == M:
                            return "D", seq[k3][0], mz[1]
                        if writes_reg(seq[k3][1], M):
                            break
                    break
            break
        if writes_reg(v, R):
            break

    # Rule A: argument register pair xR, w(R+1)
    if R <= 6:
        M = R + 1
        for (j, v) in fwd:
            mz = movz(v)
            if mz and mz[0] == M:
                return "A", j, mz[1]
            if writes_reg(v, M):
                return None
        for (j, v) in back:
            mz = movz(v)
            if mz and mz[0] == M:
                return "A", j, mz[1]
            if writes_reg(v, M):
                return None
    return None


def main(binpath, refs, out):
    data = open(binpath, "rb").read()
    m = MachO(data)
    R = pickle.load(open(refs, "rb"))
    text = m.sect("__TEXT", "__text")
    lo, hi = R["lo"], R["hi"]
    base = m.seg("__TEXT")["vmaddr"]  # __TEXT starts the file: offset = address - base
    n = text.size // 4
    ins = np.frombuffer(data, dtype="<u4", count=n, offset=text.offset)

    strings = defaultdict(lambda: defaultdict(list))
    stats = defaultdict(int)

    def text_ok(t, ln):
        if not (0 < ln <= 20000) or t + ln > hi:
            return False
        try:
            data[t - base: t - base + ln].decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    for off, t, ln in R["data_refs"]:
        if text_ok(t, ln):
            strings[t][ln].append(("data", off))
            stats["data"] += 1
        else:
            stats["data-rejected"] += 1

    # ---- pointer tables with a parallel length table ----------------------
    # LLVM turns `match x { A => "..", B => ".." }` into a table of pointers and a
    # table of lengths (u8/u16/u32/u64). The code names both, a few instructions apart.
    import bisect
    import struct
    targets = sorted({t for _, t, _ in R["data_refs"]} | {t for _, t, _, _ in R["code_refs"]})
    dc = m.sect("__DATA_CONST", "__const")
    bases = sorted({t for _, t in R["dc_refs"]})
    base_set = set(bases)
    code_by_idx = sorted((i, t) for i, t, _, _ in R["code_refs"])
    code_idx = [i for i, _ in code_by_idx]

    def ptr_at(addr):
        if not dc.contains(addr):
            return None
        v = struct.unpack_from("<Q", data, addr - dc.addr + dc.offset)[0]
        return v if lo <= v < hi else None

    def ends_clean(t, ln):
        nxt = bisect.bisect_right(targets, t)
        gap = (targets[nxt] if nxt < len(targets) else hi) - t
        if ln > gap:
            return None
        return ln == gap or data[t + ln - base] == 0

    tables = {}
    for i, B in R["dc_refs"]:
        if ptr_at(B) is None:
            continue
        a = bisect.bisect_left(code_idx, i - 48)
        z = bisect.bisect_right(code_idx, i + 48)
        best = None
        for L in {t for _, t in code_by_idx[a:z]}:
            # a row may end where another row of the same table starts ("All" + "Hosts")
            sib, j = set(), 0
            while j < 256 and (not j or B + 8 * j not in base_set):
                v = ptr_at(B + 8 * j)
                if v is None:
                    break
                sib.add(v)
                j += 1
            for w, fmt in ((1, "<B"), (2, "<H"), (4, "<I"), (8, "<Q")):
                rows, k = [], 0
                while True:
                    pa = B + 8 * k
                    if k and pa in base_set:
                        break
                    t = ptr_at(pa)
                    la = L + w * k
                    if t is None or la + w > hi:
                        break
                    ln = struct.unpack_from(fmt, data, la - base)[0]
                    if not text_ok(t, ln):
                        break
                    e = ends_clean(t, ln)
                    if e is None:
                        break
                    e = e or (t + ln) in sib
                    rows.append((pa - dc.addr + dc.offset, la - base, w, t, ln, e))
                    k += 1
                if len(rows) >= 2 and all(r[5] for r in rows):
                    if best is None or len(rows) > len(best):
                        best = rows
                    elif len(rows) == len(best) and rows != best:
                        best = "ambiguous"
        if best == "ambiguous":
            stats["table-ambiguous"] += 1
        elif best:
            prev = tables.get(B)
            if prev is None or len(best) > len(prev):
                tables[B] = best
    # one length table may serve several pointer tables; that is only safe to
    # rewrite when they are copies of the same match (same strings, row for row)
    len_users = defaultdict(set)
    for rows in tables.values():
        len_users[rows[0][1]].add(tuple(data[r[3] - base: r[3] - base + r[4]] for r in rows))
    for rows in tables.values():
        if len(len_users[rows[0][1]]) > 1:
            stats["table-sharedlens"] += 1
            continue
        for po, lo_, w, t, ln, _ in rows:
            strings[t][ln].append(("table", po, lo_, w))
            stats["table"] += 1

    claimed = defaultdict(list)
    resolved = []
    for i, t, reg, _ in R["code_refs"]:
        r = resolve(ins, n, i, reg)
        if r is None:
            stats["code-unresolved"] += 1
            continue
        rule, j, ln = r
        if not text_ok(t, ln):
            stats["code-badtext"] += 1
            continue
        claimed[j].append(i)
        resolved.append((i, t, rule, j, ln))
    for i, t, rule, j, ln in resolved:
        if len(claimed[j]) > 1:
            stats["code-shared-mov"] += 1
            continue
        strings[t][ln].append(("code", i, j))
        stats["code-" + rule] += 1

    # every ref target, resolved or not, so patch.py can tell which strings keep English users
    all_code = defaultdict(int)
    for i, t, _, _ in R["code_refs"]:
        all_code[t] += 1

    for k in sorted(stats):
        print(f"{k:18} {stats[k]}")
    print("unique (addr,len):", sum(len(v) for v in strings.values()))
    pickle.dump(dict(strings={t: dict(v) for t, v in strings.items()}, all_code=dict(all_code)), open(out, "wb"))


if __name__ == "__main__":
    main(*sys.argv[1:4])
