"""Find compiled `format_args!` templates and how they are referenced.

Recent rustc stores a format string as one NUL-terminated byte program, and code
or data holds only a pointer to its start (no length):

    1..0x7f  that many literal bytes follow
    0x80     u16 length, then that many literal bytes
    0xc0     the next argument, default formatting
    0x00     end

Anything else (explicit width, {:?}, positional arguments) is left alone.
Shown as text with `{}` for each argument; a translation must keep the
arguments in the same order.

usage: templates.py BIN refs.pkl templates.json     -> [[text, [ref, ...], address], ...]
       ref = ["tcode", adrp_index] | ["tdata", fileoff]
"""
import json
import pickle
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402


def parse(data, o, end):
    out, args = [], 0
    while o < end:
        b = data[o]
        o += 1
        if b == 0:
            return ("".join(out), args) if args and out else None
        if b == 0xC0:
            out.append("{}")
            args += 1
            continue
        if b == 0x80:
            n = data[o] | data[o + 1] << 8
            o += 2
        elif b < 0x80:
            n = b
        else:
            return None
        try:
            lit = data[o:o + n].decode("utf-8")
        except UnicodeDecodeError:
            return None
        if "\0" in lit or "{}" in lit or len(lit.encode()) != n:
            return None
        out.append(lit)
        o += n
    return None


def encode(text):
    """Inverse of parse()."""
    out = bytearray()
    parts = text.split("{}")
    for k, lit in enumerate(parts):
        raw = lit.encode("utf-8")
        if raw:
            out += bytes([len(raw)]) if len(raw) < 0x80 else b"\x80" + len(raw).to_bytes(2, "little")
            out += raw
        if k < len(parts) - 1:
            out.append(0xC0)
    return bytes(out) + b"\0"


def main(binpath, refs, outp):
    data = open(binpath, "rb").read()
    m = MachO(data)
    R = pickle.load(open(refs, "rb"))
    base, hi = m.seg("__TEXT")["vmaddr"], R["hi"]
    found = defaultdict(list)
    cache, addr = {}, {}

    def at(t):
        if t not in cache:
            p = parse(data, t - base, hi - base)
            cache[t] = p[0] if p and encode(p[0]) == data[t - base:t - base + len(encode(p[0]))] else None
        return cache[t]

    for i, t, _, _ in R["code_refs"]:
        s = at(t)
        if s:
            addr.setdefault(s, t)
            found[s].append(["tcode", i])
    for off, t, _ in R["data_refs"]:
        s = at(t)
        if s:
            addr.setdefault(s, t)
            found[s].append(["tdata", off])
    json.dump([[s, r, addr[s]] for s, r in sorted(found.items())], open(outp, "w"), ensure_ascii=False)
    print(len(found), "templates,", sum(len(v) for v in found.values()), "refs")


if __name__ == "__main__":
    main(*sys.argv[1:4])
