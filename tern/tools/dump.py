"""Dump every resolved string as JSON: [addr, len, text, refs, code refs]."""
import json
import pickle
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402

binpath, strings, out = sys.argv[1:4]
d = open(binpath, "rb").read()
base = MachO(d).seg("__TEXT")["vmaddr"]
S = pickle.load(open(strings, "rb"))
rows = []
for t, v in S["strings"].items():
    o = t - base
    for ln, refs in v.items():
        rows.append((t, ln, d[o:o + ln].decode(), len(refs), sum(1 for r in refs if r[0] == "code")))
rows.sort()
json.dump(rows, open(out, "w"), ensure_ascii=False)
print(len(rows), "strings")
