"""Find every reference to string data in the Tern binary.

Rust string literals are (pointer, length) pairs with no terminator, so a
string's extent is only known from its references:

  * data refs: an 8-byte pointer into __TEXT,__const followed by an 8-byte length
    (or a bare pointer table, whose lengths extract.py finds through the code)
  * code refs: `adrp xN, page ; add xN, xN, #off` with a `mov wM, #len` nearby

Output: a pickle with both reference lists, consumed by extract.py / patch.py.
"""
import pickle
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402


def main(path, out):
    data = open(path, "rb").read()
    m = MachO(data)
    text = m.sect("__TEXT", "__text")
    tconst = m.sect("__TEXT", "__const")
    lits = m.sect("__TEXT", "__literals")
    # __const, __cstring and __literals are contiguous; short literals land in __literals
    lo, hi = tconst.addr, lits.addr + lits.size

    # ---- data refs -------------------------------------------------------
    data_refs = []  # (file offset of ptr, target, len)
    for key in (("__DATA_CONST", "__const"), ("__DATA", "__data")):
        s = m.sect(*key)
        n = s.size // 8
        arr = np.frombuffer(data, dtype="<u8", count=n, offset=s.offset)
        idx = np.nonzero((arr[:-1] >= lo) & (arr[:-1] < hi))[0]
        for i in idx:
            ln = int(arr[i + 1])
            data_refs.append((s.offset + int(i) * 8, int(arr[i]), ln))
    print("data ptrs into __TEXT,__const:", len(data_refs))

    # ---- code refs -------------------------------------------------------
    n = text.size // 4
    ins = np.frombuffer(data, dtype="<u4", count=n, offset=text.offset)
    is_adrp = (ins & 0x9F000000) == 0x90000000
    is_add = (ins & 0xFFC00000) == 0x91000000
    rd = ins & 31
    rn = (ins >> 5) & 31
    # adjacent adrp+add writing the same register: the page register dies at once
    pair = is_adrp[:-1] & is_add[1:] & (rd[:-1] == rd[1:]) & (rn[1:] == rd[:-1])
    pidx = np.nonzero(pair)[0]
    a = ins[pidx].astype(np.int64)
    immlo = (a >> 29) & 3
    immhi = (a >> 5) & 0x7FFFF
    imm = (immhi << 2) | immlo
    imm = np.where(imm & (1 << 20), imm - (1 << 21), imm)
    pc = text.addr + pidx.astype(np.int64) * 4
    page = (pc & ~0xFFF) + (imm << 12)
    target = page + ((ins[pidx + 1].astype(np.int64) >> 10) & 0xFFF)
    dc = m.sect("__DATA_CONST", "__const")
    in_dc = (target >= dc.addr) & (target < dc.addr + dc.size)
    dc_refs = list(zip(pidx[in_dc].tolist(), target[in_dc].tolist()))
    keep = (target >= lo) & (target < hi)
    pidx, target = pidx[keep], target[keep]
    print("adjacent adrp+add into __TEXT,__const:", len(pidx))

    # loose adrp (not followed by its own add) that point into the const pages: unpatchable refs
    loose = is_adrp.copy()
    loose[np.nonzero(pair)[0]] = False
    print("other adrp total:", int(loose.sum()))

    # movz wN/xN, #imm16 (hw=0) candidates around each pair
    is_movz = (ins & 0x7FE00000) == 0x52800000
    code_refs = []  # (insn index of adrp, target, reg, [(insn index, reg, imm)])
    W_BEFORE, W_AFTER = 6, 8
    for i, t in zip(pidx.tolist(), target.tolist()):
        cands = []
        for j in range(max(0, i - W_BEFORE), min(n, i + 2 + W_AFTER)):
            if is_movz[j]:
                v = int(ins[j])
                cands.append((j, v & 31, (v >> 5) & 0xFFFF))
        code_refs.append((i, t, int(ins[i]) & 31, cands))

    pickle.dump(dict(data_refs=data_refs, code_refs=code_refs, dc_refs=dc_refs, lo=lo, hi=hi, text_off=text.offset, text_addr=text.addr,
                     const_addr=tconst.addr, const_off=tconst.offset, const_size=tconst.size), open(out, "wb"))
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
