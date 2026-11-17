"""Locate the hand-specified sites of translations/zh-CN.sites.json in this binary.

The table names each site by what it is, not where it is, so it survives a new
Tern build. This turns it into addresses for mkplan.py (addr.json) and patch.py
(sites.json), and lists every entry it could not place (unresolved.json).

  context  [en, zh, [neighbours]]   one copy of a bare word: the resolved string equal to
                                    `en` whose neighbours in the string pool include most
                                    of the listed ones. Translated wherever it is referenced.
                                    When no copy stands apart it is looked up as `data`.
  data     [en, zh, [neighbours]]   a (pointer, length) pair in a data section that points
                                    at `en`, next to pairs pointing at the listed strings
                                    (every copy of the table that matches equally well).
  ptr      [en, zh]                 code that takes the address of `en` and gets its length
                                    some way extract.py cannot follow. Pointer only, so
                                    `zh` is padded with spaces to the byte length of `en`.
  imm      [en, zh]                 String::from(en) expanded into immediates:
                                        movz w0,#N ; movz w1,#1 ; bl alloc ; cbz x0
                                        movz/movk wR ; str wR,[x0,#o] ...
                                    The immediates, store widths and every `movz #N` that
                                    follows are rewritten; `zh` must fit in the bytes the
                                    stores can carry.

usage: resolve.py BIN refs.pkl strings.pkl all_strings.json SITES.json OUT_DIR
"""
import bisect
import json
import pickle
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402
from inline import SCAN, is_branch, store  # noqa: E402

NEAR_ROWS, NEAR_DATA, LEN_SCAN = 6, 0x48, 14
NICHE = 0x8000000000000000  # the tag word LLVM folds next to a String's capacity


def main(binpath, refs, strings, rows, sitespath, outdir):
    data = open(binpath, "rb").read()
    m = MachO(data)
    text = m.sect("__TEXT", "__text")
    ins = np.frombuffer(data, dtype="<u4", count=text.size // 4, offset=text.offset)
    R = pickle.load(open(refs, "rb"))
    S = pickle.load(open(strings, "rb"))["strings"]
    rows = json.load(open(rows))
    T = json.load(open(sitespath))
    base = m.seg("__TEXT")["vmaddr"]
    lo, hi = R["lo"], R["hi"]
    pc = lambda i: text.addr + i * 4
    raw = lambda t, n: data[t - base:t - base + n]
    unresolved = []

    def miss(kind, en, why):
        unresolved.append([kind, en, why])

    # ---- (pointer, length) pairs in data ---------------------------------
    out = {"ptr": [], "data": [], "poke": []}
    pairs = sorted((off, t, ln) for off, t, ln in R["data_refs"] if 0 < ln < 256 and t + ln <= hi)
    offs = [p[0] for p in pairs]
    off_to_addr = {}
    for s in m.sections.values():
        if s.offset:
            off_to_addr[s.offset] = s
    sect_at = sorted(off_to_addr)

    def vaddr(off):
        s = off_to_addr[sect_at[bisect.bisect_right(sect_at, off) - 1]]
        return s.addr + off - s.offset

    def data_sites(en, zh, near):
        """Pairs pointing at `en` with the most of `near` beside them: (sites, why not)."""
        b, want, best = en.encode(), set(near), []
        for k, (off, t, ln) in enumerate(pairs):
            if ln != len(b) or raw(t, ln) != b:
                continue
            a, z = bisect.bisect_left(offs, off - NEAR_DATA), bisect.bisect_right(offs, off + NEAR_DATA)
            around = {raw(pairs[j][1], pairs[j][2]).decode("utf-8", "replace") for j in range(a, z) if j != k}
            best.append((len(want & around), off))
        best.sort(reverse=True)
        if not best:
            return [], "no (pointer, length) pair points at this text"
        if not best[0][0]:
            return [], "none of the neighbours is next to any pair"
        # copies of one table are all rewritten
        return [[f"{vaddr(off):#x}", en, zh] for score, off in sorted(best) if score == best[0][0]], None

    # ---- context ---------------------------------------------------------
    addr = {}
    by_text = defaultdict(list)
    for k, r in enumerate(rows):
        by_text[r[2]].append(k)
    for en, zh, near in T.get("context", []):
        want, best = set(near), []
        for k in by_text.get(en, []):
            around = {rows[j][2] for j in range(max(0, k - NEAR_ROWS), min(len(rows), k + NEAR_ROWS + 1)) if j != k}
            best.append((len(want & around), k))
        best.sort(reverse=True)
        if best and (best[0][0] or not want) and (len(best) == 1 or best[0][0] > best[1][0]):
            t, ln = rows[best[0][1]][:2]
            addr[f"{t:#x}:{ln}"] = zh
            continue
        # The linker may have merged this copy with an identical one elsewhere, which
        # leaves only the table row that points at it to tell the UI use apart.
        sites, _ = data_sites(en, zh, near)
        if sites:
            out["data"] += sites
        elif not best:
            miss("context", en, "no resolved string with this text")
        elif want and not best[0][0]:
            miss("context", en, "none of the neighbours is near any copy")
        else:
            miss("context", en, f"{sum(1 for b in best if b[0] == best[0][0])} copies match equally well")

    # ---- data ------------------------------------------------------------
    for en, zh, near in T.get("data", []):
        sites, why = data_sites(en, zh, near)
        out["data"] += sites
        if why:
            miss("data", en, why)

    # ---- alloc sites (shared by ptr and imm) -----------------------------
    bls = np.nonzero(((ins & 0xFC000000) == 0x94000000)[1:] & (ins[:-1] == 0x52800021))[0] + 1
    d = (ins[bls] & 0x3FFFFFF).astype(np.int64)
    d = np.where(d & (1 << 25), d - (1 << 26), d)
    alloc, _ = Counter((bls + d).tolist()).most_common(1)[0]
    allocs = [j for j in bls[(bls + d) == alloc].tolist() if (int(ins[j + 1]) & 0xFF00001F) == 0xB4000000]

    # ---- ptr -------------------------------------------------------------
    # adrp+add pairs come from analyze.py; a 16-byte literal is also loaded whole with
    # adrp ; [nop ;] ldr qN,[xR,#o]
    targets = sorted({t for _, t, _ in R["data_refs"]} | {t for _, t, _, _ in R["code_refs"]})
    resolved = {r[1] for v in S.values() for refs in v.values() for r in refs if r[0] == "code"}
    copied = set()  # adrp+add that feed an inlined String::from: the literal is partly code
    for j in allocs:
        for q in range(j + 2, j + 2 + SCAN):
            if is_branch(int(ins[q])):
                break
            copied.add(q)
    is_adrp = (ins & 0x9F000000) == 0x90000000
    ldrq = lambda v, r: (v & 0xFFC003E0) == 0x3DC00000 | r << 5

    def adrp_page(i):
        v = int(ins[i])
        imm = ((v >> 5) & 0x7FFFF) << 2 | (v >> 29) & 3
        return (pc(i) & ~0xFFF) + ((imm - (1 << 21) if imm & (1 << 20) else imm) << 12)

    q_refs = defaultdict(list)  # target -> [adrp index]
    for i in np.nonzero(is_adrp[:-2])[0].tolist():
        r = int(ins[i]) & 31
        for k in (1, 2):
            v = int(ins[i + k])
            if ldrq(v, r):
                q_refs[adrp_page(i) + ((v >> 10) & 0xFFF) * 16].append(i)
                break
            if v != 0xD503201F:
                break

    def whole(t, n):
        """Is the n bytes at t a complete literal (the next one starts right after)?"""
        k = bisect.bisect_right(targets, t)
        gap = (targets[k] if k < len(targets) else hi) - t
        return n == gap or (n < gap and data[t + n - base] == 0)

    for en, zh in T.get("ptr", []):
        b = en.encode()
        if len(zh.encode()) > len(b):
            miss("ptr", en, "the translation is longer than the original")
            continue
        found = [i for i, t, _, _ in R["code_refs"]
                 if i not in resolved and i not in copied and raw(t, len(b)) == b and whole(t, len(b))]
        if len(b) == 16:
            found += [i for t, sites in q_refs.items() if lo <= t < hi and raw(t, 16) == b for i in sites]
        if not found:
            miss("ptr", en, "no pointer-only reference to this text")
        out["ptr"] += [[f"{pc(i):#x}", en, zh] for i in sorted(found)]

    # ---- imm -------------------------------------------------------------
    want = defaultdict(list)
    for en, zh in T.get("imm", []):
        want[len(en.encode())].append((en, zh))
    hits = Counter()
    for j in allocs:
        n = w0 = None
        for k in (j - 1, j - 2):
            v = int(ins[k])
            if (v & 0xFFE0001F) == 0x52800000:
                n, w0 = (v >> 5) & 0xFFFF, k
        if n not in want:
            continue
        reg, buf, slots, end = {}, {}, [], None  # reg: r -> (value, [imm insn])
        for q in range(j + 2, j + 2 + SCAN):
            v = int(ins[q])
            if is_branch(v):
                break
            r = v & 31
            if (v & 0x7F800000) == 0x52800000:  # movz
                reg[r] = (((v >> 5) & 0xFFFF) << 16 * ((v >> 21) & 3), [q])
            elif (v & 0x7F800000) == 0x72800000 and r in reg:  # movk
                sh = 16 * ((v >> 21) & 3)
                reg[r] = (reg[r][0] & ~(0xFFFF << sh) | ((v >> 5) & 0xFFFF) << sh, reg[r][1] + [q])
            else:
                s = store(v)
                if s and s[0] == 0 and r in reg and s[2] <= 8 and 0 <= s[1] and s[1] + s[2] <= n:
                    for k, c in enumerate(reg[r][0].to_bytes(8, "little")[:s[2]]):
                        buf[s[1] + k] = c
                    slots.append((s[1], q, reg[r][1], bool(int(ins[reg[r][1][0]]) >> 31)))
                elif s is None:
                    reg.pop(r, None)
            if len(buf) == n:
                end = q
                break
        if end is None:
            continue
        got = bytes(buf[k] for k in range(n))
        for en, zh in want[n]:
            if got != en.encode():
                continue
            hits[en] += 1
            z = zh.encode()
            pokes, pos, ok = [], 0, True
            for off, q, imms, wide in sorted(slots):
                if len({x for x in imms}) != len(imms) or any(q2 in {y for _, _, i2, _ in slots if i2 is not imms for y in i2} for q2 in imms):
                    ok = False  # one immediate feeds two stores
                    break
                cap = min(2 * len(imms), 8 if wide else 4, len(z))
                size = 1 << (cap.bit_length() - 1)
                o = min(pos, len(z) - size)
                pos = max(pos, o + size)
                chunk = z[o:o + size] + b"\0" * (2 * len(imms) - size)
                for h, k in enumerate(imms):
                    v = int(ins[k])
                    op = 0x52800000 if h == 0 else 0x72800000
                    new = (v & 0x80000000) | op | h << 21 | int.from_bytes(chunk[2 * h:2 * h + 2], "little") << 5 | (v & 31)
                    pokes.append((k, v, new))
                v = int(ins[q])
                sz = size.bit_length() - 1
                if o % size == 0:
                    new = sz << 30 | 0x39000000 | (o // size) << 10 | (v & 0x3FF)
                else:
                    new = sz << 30 | 0x38000000 | o << 12 | (v & 0x3FF)
                pokes.append((q, v, new))
            if not ok or pos < len(z):
                miss("imm", en, f"site {pc(j):#x}: the stores cannot carry {len(z)} bytes")
                continue
            if len(z) != n:
                v = int(ins[w0])
                pokes.append((w0, v, (v & 0xFFE0001F) | len(z) << 5))
                lens = []  # where the String's len/capacity is set, as an immediate or a constant pair
                for q in range(end + 1, end + 1 + LEN_SCAN):
                    v = int(ins[q])
                    if is_branch(v):
                        break
                    if (v & 0x7FE00000) == 0x52800000 and (v >> 5) & 0xFFFF == n:
                        pokes.append((q, v, (v & 0xFFE0001F) | len(z) << 5))
                        lens.append(q)
                    elif is_adrp[q]:
                        for t, sites in q_refs.items():
                            if q in sites and struct.unpack_from("<QQ", data, m.addr_to_off(t)) == (NICHE, n):
                                lens.append([f"{pc(q):#x}", "hex:" + struct.pack("<QQ", NICHE, n).hex(),
                                             "hex:" + struct.pack("<QQ", NICHE, len(z)).hex()])
                if not lens:  # resizing the buffer alone would leave the old length in the String
                    miss("imm", en, f"site {pc(j):#x}: the length is not set right after the text")
                    continue
                out["ptr"] += [x for x in lens if isinstance(x, list)]
            out["poke"] += [[f"{pc(k):#x}", f"{old:08x}", f"{new:08x}"] for k, old, new in sorted(set(pokes)) if old != new]
    for en, zh in T.get("imm", []):
        if not hits[en]:
            miss("imm", en, "no String::from site builds this text from immediates")

    json.dump(addr, open(outdir + "/addr.json", "w"), ensure_ascii=False, indent=0)
    json.dump(out, open(outdir + "/sites.json", "w"), ensure_ascii=False, indent=0)
    json.dump(unresolved, open(outdir + "/unresolved.json", "w"), ensure_ascii=False, indent=0)
    n = sum(len(T.get(k, [])) for k in ("context", "data", "ptr", "imm"))
    print(f"sites: {len(addr)} context, {len(out['data'])} data, {len(out['ptr'])} ptr, {len(out['poke'])} pokes;"
          f" {len(unresolved)} of {n} entries not placed")
    if len(unresolved) < n:  # a helper with none of this UI in it has nothing to report
        for kind, en, why in unresolved:
            print(f"  not placed  {kind:8} {en!r}: {why}")


if __name__ == "__main__":
    main(*sys.argv[1:7])
