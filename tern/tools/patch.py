"""Apply a translation plan to the Tern binary.

Translated strings go into a new read-only segment (__ZHCN) placed between
__DATA and __LINKEDIT; each selected reference is repointed at its copy and its
length operand rewritten. The original English bytes are never modified, so a
reference that is not rewritten keeps working exactly as before.

usage: patch.py IN OUT PLAN.json
plan:  [[zh_text, [ref, ...]], ...]   ref = ["data", fileoff] | ["table", ptr_fileoff, len_fileoff, len_width] | ["code", adrp_index, movz_index]
                                            | ["inline", store_index, [dropped store indices], N, [movz indices] or null]
                                            | ["tcode", adrp_index] | ["tdata", fileoff]   (zh_text is a format template)

An "inline" ref is a String::from() that the compiler expanded into fixed-size
copies (see inline.py). Its first store becomes `bl` to a copy routine emitted
after the strings; the segment is therefore mapped r-x.
"""
import json
import struct
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO, LC_SEGMENT_64  # noqa: E402
from templates import encode as encode_template  # noqa: E402

PAGE = 0x4000
LC_SYMTAB, LC_DYSYMTAB, LC_DYLD_INFO_ONLY = 0x2, 0xB, 0x80000022
LC_CODE_SIGNATURE, LC_FUNCTION_STARTS, LC_DATA_IN_CODE = 0x1D, 0x26, 0x29
SEGNAME = b"__ZHCN"


def main(inp, outp, planpath, sitespath=None):
    data = bytearray(open(inp, "rb").read())
    m = MachO(bytes(data))
    assert not any(s["name"] == SEGNAME.decode() for s in m.segments), "already patched"
    plan = json.load(open(planpath))
    text = m.sect("__TEXT", "__text")
    link = m.seg("__LINKEDIT")
    assert link["fileoff"] + link["filesize"] == len(data), "__LINKEDIT must end the file"

    # ---- the blob --------------------------------------------------------
    blob = bytearray()
    where = {}
    nrefs = 0

    def place(zh):
        if zh not in where:
            where[zh] = len(blob)
            blob.extend(zh.encode("utf-8"))
        return where[zh]

    seg_addr, seg_off = link["vmaddr"], link["fileoff"]
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]
    inl = {}  # (text, buffer register) -> [call sites]
    tpl = {}
    for zh, refs in plan:
        if refs and refs[0][0] in ("tcode", "tdata"):
            tpl[zh] = len(blob)
            blob.extend(encode_template(zh))
            continue
        place(zh)
        for ref in refs:
            if ref[0] != "inline":
                continue
            _, q, drops, n, lens = ref
            ln = len(zh.encode("utf-8"))
            if lens:
                for k in lens:
                    o = text.offset + k * 4
                    v = u32(o)
                    assert (v & 0x7FE00000) == 0x52800000 and (v >> 5) & 0xFFFF == n
                    struct.pack_into("<I", data, o, (v & 0xFFE0001F) | (ln << 5))
                body = zh
            else:
                assert ln <= n, f"{zh!r} does not fit in {n} bytes"
                body = zh + " " * (n - ln)
            place(body)
            reg = (u32(text.offset + q * 4) >> 5) & 31
            assert reg not in (15, 16, 17, 31)
            inl.setdefault((body, reg), []).append(q)
            for k in drops:
                struct.pack_into("<I", data, text.offset + k * 4, 0xD503201F)  # nop
    blob.extend(b"\0" * (-len(blob) % 4))
    for (body, reg), sites in inl.items():
        pc = seg_addr + len(blob)
        src, ln = seg_addr + where[body], len(body.encode("utf-8"))
        assert ln < 4096
        pages = ((src >> 12) - ((pc + 8) >> 12)) & ((1 << 21) - 1)
        code = [
            0xA9BE43EF,                                              # stp  x15, x16, [sp, #-32]!
            0xF9000BF1,                                              # str  x17, [sp, #16]
            0x90000010 | ((pages & 3) << 29) | ((pages >> 2) << 5),  # adrp x16, src
            0x91000210 | ((src & 0xFFF) << 10),                      # add  x16, x16, #lo
            0xD2800011,                                              # mov  x17, #0
            0x38716A0F,                                              # ldrb w15, [x16, x17]
            0x3831680F | (reg << 5),                                 # strb w15, [xREG, x17]
            0x91000631,                                              # add  x17, x17, #1
            0xD100022F | (ln << 10),                                 # sub  x15, x17, #ln
            0xB5FFFF8F,                                              # cbnz x15, -4
            0xF9400BF1,                                              # ldr  x17, [sp, #16]
            0xA8C243EF,                                              # ldp  x15, x16, [sp], #32
            0xD65F03C0,                                              # ret
        ]
        blob.extend(struct.pack(f"<{len(code)}I", *code))
        for q in sites:
            d = (pc - (text.addr + q * 4)) >> 2
            assert -(1 << 25) <= d < (1 << 25), "copy routine out of bl range"
            struct.pack_into("<I", data, text.offset + q * 4, 0x94000000 | (d & 0x3FFFFFF))
    # ---- hand-located sites (see translations/zh-CN.sites.json) ----------
    sites = json.load(open(sitespath)) if sitespath else {}
    raw = lambda t: bytes.fromhex(t[4:]) if t.startswith("hex:") else t.encode("utf-8")
    at = lambda a: m.addr_to_off(int(a, 16))
    for a, old, new in sites.get("poke", []):
        o = at(a)
        assert u32(o) == int(old, 16), f"poke {a}: found {u32(o):08x}, expected {old}"
        struct.pack_into("<I", data, o, int(new, 16))
        nrefs += 1
    for a, en, zh in sites.get("ptr", []):
        o, pc = at(a), int(a, 16)
        en, zh = raw(en), raw(zh)
        assert len(zh) <= len(en), f"ptr {a}: replacement is longer than the original"
        zh += b" " * (len(en) - len(zh))
        adrp, nxt, nx2 = u32(o), u32(o + 4), u32(o + 8)
        assert (adrp & 0x9F000000) == 0x90000000, f"ptr {a}: not an adrp"
        rd = adrp & 31
        imm = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
        page = (pc & ~0xFFF) + ((imm - (1 << 21) if imm & (1 << 20) else imm) << 12)
        is_add = lambda v: (v & 0xFFC003E0) == 0x91000000 | rd << 5 and (v & 31) == rd
        is_ldrq = lambda v: (v & 0xFFC003E0) == 0x3DC00000 | rd << 5
        if is_add(nxt):
            tgt, mode = page + ((nxt >> 10) & 0xFFF), "add"
        elif is_ldrq(nxt):
            tgt, mode = page + ((nxt >> 10) & 0xFFF) * 16, "ldrq"
        else:
            assert nxt == 0xD503201F and is_ldrq(nx2), f"ptr {a}: unsupported instruction pair"
            tgt, mode = page + ((nx2 >> 10) & 0xFFF) * 16, "nop-ldrq"
        t = m.addr_to_off(tgt)
        assert bytes(data[t:t + len(en)]) == en, f"ptr {a}: original bytes differ"
        blob.extend(b"\0" * (-len(blob) % 16))
        new = seg_addr + len(blob)
        blob.extend(zh)
        pages = ((new >> 12) - (pc >> 12)) & ((1 << 21) - 1)
        struct.pack_into("<I", data, o, (adrp & 0x9F00001F) | ((pages & 3) << 29) | ((pages >> 2) << 5))
        lo = new & 0xFFF
        if mode == "add":
            struct.pack_into("<I", data, o + 4, (nxt & 0xFFC003FF) | (lo << 10))
        elif mode == "ldrq":
            struct.pack_into("<I", data, o + 4, (nxt & 0xFFC003FF) | ((lo >> 4) << 10))
        else:
            struct.pack_into("<I", data, o + 4, 0x91000000 | (lo << 10) | (rd << 5) | rd)
            struct.pack_into("<I", data, o + 8, nx2 & 0xFFC003FF)
        nrefs += 1
    for a, en, zh in sites.get("data", []):
        o = at(a)
        p, ln = struct.unpack_from("<QQ", data, o)
        en, zh = raw(en), raw(zh)
        assert ln == len(en) and bytes(data[m.addr_to_off(p):m.addr_to_off(p) + ln]) == en, f"data {a}: original differs"
        blob.extend(b"\0" * (-len(blob) % 16))
        struct.pack_into("<QQ", data, o, seg_addr + len(blob), len(zh))
        blob.extend(zh)
        nrefs += 1
    seg_size = max(PAGE, (len(blob) + PAGE - 1) // PAGE * PAGE)
    blob.extend(b"\0" * (seg_size - len(blob)))

    # ---- references ------------------------------------------------------
    for zh, refs in plan:
        if zh in tpl and refs[0][0] in ("tcode", "tdata"):
            new = seg_addr + tpl[zh]
            for ref in refs:
                if ref[0] == "tdata":
                    struct.pack_into("<Q", data, ref[1], new)
                else:
                    i = ref[1]
                    o_adrp, o_add = text.offset + i * 4, text.offset + (i + 1) * 4
                    adrp, add = u32(o_adrp), u32(o_add)
                    assert (adrp & 0x9F000000) == 0x90000000 and (add & 0xFFC00000) == 0x91000000
                    pages = ((new >> 12) - ((text.addr + i * 4) >> 12)) & ((1 << 21) - 1)
                    struct.pack_into("<I", data, o_adrp, (adrp & 0x9F00001F) | ((pages & 3) << 29) | ((pages >> 2) << 5))
                    struct.pack_into("<I", data, o_add, (add & 0xFFC003FF) | ((new & 0xFFF) << 10))
                nrefs += 1
            continue
        new = seg_addr + where[zh]
        ln = len(zh.encode("utf-8"))
        assert 0 < ln < 0x10000
        for ref in refs:
            if ref[0] == "inline":
                pass  # done above
            elif ref[0] == "data":
                off = ref[1]
                struct.pack_into("<QQ", data, off, new, ln)
            elif ref[0] == "table":
                width = ref[3]
                assert ln < 1 << (8 * width), f"{zh!r} is too long for its {width}-byte length table"
                struct.pack_into("<Q", data, ref[1], new)
                data[ref[2]:ref[2] + width] = ln.to_bytes(width, "little")
            else:
                _, i, j = ref
                o_adrp, o_add, o_mov = text.offset + i * 4, text.offset + (i + 1) * 4, text.offset + j * 4
                adrp, add, mov = (struct.unpack_from("<I", data, o)[0] for o in (o_adrp, o_add, o_mov))
                assert (adrp & 0x9F000000) == 0x90000000 and (add & 0xFFC00000) == 0x91000000
                assert (mov & 0x7FE00000) == 0x52800000
                pc = text.addr + i * 4
                pages = (new >> 12) - (pc >> 12)
                assert -(1 << 20) <= pages < (1 << 20)
                pages &= (1 << 21) - 1
                adrp = (adrp & 0x9F00001F) | ((pages & 3) << 29) | ((pages >> 2) << 5)
                add = (add & 0xFFC003FF) | ((new & 0xFFF) << 10)
                mov = (mov & 0xFFE0001F) | (ln << 5)
                for o, v in ((o_adrp, adrp), (o_add, add), (o_mov, mov)):
                    struct.pack_into("<I", data, o, v)
            nrefs += 1

    # ---- load commands ---------------------------------------------------
    first_sect = min(s.offset for s in m.sections.values() if s.offset)
    cmds = []
    for off, cmd, size in m.cmds:
        raw = bytearray(data[off:off + size])
        if cmd == LC_DATA_IN_CODE:
            assert struct.unpack_from("<I", raw, 12)[0] == 0, "data-in-code is not empty"
            continue  # dropped to make room for the new segment command

        def bump(*fields):
            for f in fields:
                v = struct.unpack_from("<I", raw, f)[0]
                if v:
                    struct.pack_into("<I", raw, f, v + seg_size)

        if cmd == LC_SEGMENT_64 and raw[8:24].rstrip(b"\0") == b"__LINKEDIT":
            new_seg = struct.pack("<II16sQQQQiiII", LC_SEGMENT_64, 72, SEGNAME, seg_addr, seg_size,
                                  seg_off, seg_size, 5, 5, 0, 0)  # r-x: strings and copy routines
            cmds.append(new_seg)
            vmaddr, vmsize, fileoff = struct.unpack_from("<QQQ", raw, 24)
            struct.pack_into("<QQQ", raw, 24, vmaddr + seg_size, vmsize, fileoff + seg_size)
        elif cmd == LC_SYMTAB:
            bump(8, 16)
        elif cmd == LC_DYSYMTAB:
            bump(32, 40, 48, 56, 64, 72)
        elif cmd == LC_DYLD_INFO_ONLY:
            bump(8, 16, 24, 32, 40)
        elif cmd in (LC_CODE_SIGNATURE, LC_FUNCTION_STARTS):
            bump(8)
        cmds.append(bytes(raw))
    lc = b"".join(cmds)
    assert 32 + len(lc) <= first_sect, "no room for the new load command"
    struct.pack_into("<II", data, 16, len(cmds), len(lc))
    data[32:first_sect] = lc + b"\0" * (first_sect - 32 - len(lc))

    out = data[:seg_off] + blob + data[seg_off:]
    open(outp, "wb").write(out)
    print(f"patched {nrefs} refs, {len(where)} strings, segment {seg_size:#x} bytes at {seg_addr:#x}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
