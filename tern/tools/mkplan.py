"""Build a patch plan from the translation table.

Sentences (anything with a space or a non-ASCII character) are UI text wherever
they appear, so every resolved reference is rewritten. A bare word ("Light",
"Cancelled") may equally be a serde variant, a Debug name or a protocol token,
so a reference to one is only rewritten when its surroundings show UI text:

  code   the next call is a function that also receives translated sentences in
         the same argument register, or another translated sentence is
         referenced within NEAR instructions
  data   a translated sentence is referenced within 64 bytes
  table  every row of the table has a translation
  inline as code (see inline.py); such a site keeps its byte length unless the
         length operand is known, so the translation must fit: the shorter
         wording from SHORT.json is used when the normal one does not

  template  compiled format strings (see templates.py): pointer-only references

usage: mkplan.py BIN strings.pkl all_strings.json translations.json plan.json
                 [inline.json SHORT.json [templates.json TEMPLATES-zh.json]]
"""
import bisect
import json
import pickle
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from macho import MachO  # noqa: E402

NEAR, SINK_MIN = 40, 3

binpath, strings, rows, trs, out = sys.argv[1:6]
data = open(binpath, "rb").read()
text = MachO(data).sect("__TEXT", "__text")
ins = np.frombuffer(data, dtype="<u4", count=text.size // 4, offset=text.offset)
S = pickle.load(open(strings, "rb"))["strings"]
tr = json.load(open(trs))
# per-address overrides: pin a translation to specific UI copies of a string,
# so a bare word can be translated at its settings-row address without touching
# the same bytes where they are a serde variant or protocol token.
_addr = trs.rsplit("/", 1)[0] + "/zh-CN.addr.json"
try:
    addr_zh = {int(k, 16): v for k, v in json.load(open(_addr)).items()}
except FileNotFoundError:
    addr_zh = {}


def near(sorted_list, x, d):
    k = bisect.bisect_left(sorted_list, x - d)
    return k < len(sorted_list) and sorted_list[k] <= x + d


def sentence(s):
    return " " in s.strip() or any(ord(c) > 127 for c in s)


def callee(i):
    """(target insn index of the first bl after the adrp/add at i, or None)"""
    for j in range(i + 2, min(len(ins), i + 18)):
        v = int(ins[j])
        if (v & 0xFC000000) == 0x94000000:
            d = v & 0x3FFFFFF
            return j + (d - (1 << 26) if d & (1 << 25) else d)
        if (v & 0xFC000000) == 0x14000000 or (v & 0xFFFFFC1F) == 0xD65F0000:
            return None
    return None


items = []  # (text, zh, refs, address)
_rows = json.load(open(rows))
for t, ln, s, n, nc in _rows:
    zh = tr.get(s)
    if zh and zh != s:
        items.append((s, zh, [list(r) for r in S[t][ln]], t))

# A capitalised word is UI text when the literals the compiler placed next to it
# are: a translated sentence, or a CSS class of the interface ("tn-pl-scope").
import re
_css = re.compile(r"[a-z]{2,3}-[a-z0-9-]+$")
_ui_addr = sorted(t for t, ln, s, n, nc in _rows
                  if (s in tr and tr[s] != s and sentence(s)) or _css.match(s))


# Identifier sinks: functions that receive snake_case names (serde field keys,
# protocol tokens). A bare word passed to one is data, never interface text.
_ident = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_idc, _uic = defaultdict(int), defaultdict(int)
for t, ln, s, n, nc in _rows:
    kind = 1 if _ident.match(s) else 2 if (s in tr and " " in s.strip()) else 0
    if kind:
        for r in S[t][ln]:
            if r[0] == "code":
                c = callee(r[1])
                if c is not None:
                    (_idc if kind == 1 else _uic)[(c, int(ins[r[1]]) & 31)] += 1
id_sinks = {k for k, v in _idc.items() if v >= 5 and not _uic[k]}


def to_ident(r):
    if r[0] != "code":
        return False
    c = callee(r[1])
    return c is not None and (c, int(ins[r[1]]) & 31) in id_sinks


def word_ui(s, t, refs):
    return s[:1].isupper() and near(_ui_addr, t, 64) and not any(to_ident(r) for r in refs)

inline = json.load(open(sys.argv[6])) if len(sys.argv) > 6 else []
short = json.load(open(sys.argv[7])) if len(sys.argv) > 7 else {}
text_addr = text.addr
inl_items, nofit = [], []
for s, n, q, off, drops, lens in inline:
    zh = tr.get(s)
    if not zh or zh == s:
        continue
    if text_addr + q * 4 < 0x10a20c000 - (1 << 27) + (1 << 20):
        continue  # too far below the new segment for a bl
    if not lens and len(zh.encode()) > n:
        zh = short.get(s)
        if not zh or len(zh.encode()) > n:
            nofit.append(s)
            continue
    inl_items.append((s, zh, ["inline", q, drops, n, lens]))

sinks = defaultdict(int)
sent_code, sent_data, table_rows = [], [], defaultdict(lambda: [0, 0])
for s, zh, refs, _t in items:
    for r in refs:
        if sentence(s):
            if r[0] == "code":
                sent_code.append(r[1])
                c = callee(r[1])
                if c is not None:
                    sinks[(c, int(ins[r[1]]) & 31)] += 1
            elif r[0] == "data":
                sent_data.append(r[1])
sent_code += [r[1] for s, _, r in inl_items if sentence(s)]
sent_code.sort()
sent_data.sort()
# table completeness: rows of one table share a length table, 1 pointer = 8 bytes
all_tables = defaultdict(set)
for t, d in S.items():
    for ln, refs in d.items():
        for r in refs:
            if r[0] == "table":
                all_tables[r[2] - (r[1] % 8)].add(r[1])  # coarse key, refined below
tbl_of = {}
ptrs = sorted(p for v in all_tables.values() for p in v)
run = 0
for k, p in enumerate(ptrs):
    if k and p != ptrs[k - 1] + 8:
        run += 1
    tbl_of[p] = run
tbl_total, tbl_tr = defaultdict(int), defaultdict(int)
for p, r_ in tbl_of.items():
    tbl_total[r_] += 1
for s, zh, refs, _t in items:
    for r in refs:
        if r[0] == "table":
            tbl_tr[tbl_of[r[1]]] += 1


plan, stats = [], defaultdict(int)
for s, zh, refs, t in items:
    keep = []
    wu = word_ui(s, t, refs)
    for r in refs:
        if sentence(s) or wu:
            ok = True
        elif to_ident(r):
            ok = False
        elif r[0] == "code":
            c = callee(r[1])
            ok = (c is not None and sinks[(c, int(ins[r[1]]) & 31)] >= SINK_MIN) or near(sent_code, r[1], NEAR)
        elif r[0] == "data":
            ok = near(sent_data, r[1], 64)
        else:
            ok = tbl_tr[tbl_of[r[1]]] == tbl_total[tbl_of[r[1]]]
        stats[("kept " if ok else "skipped ") + ("sentence" if sentence(s) else "word") + " " + r[0]] += 1
        if ok:
            keep.append(r)
    if keep:
        plan.append([zh, keep])
for s, zh, r in inl_items:
    ok = sentence(s) or near([x for x in sent_code if x != r[1]], r[1], NEAR)
    stats[("kept " if ok else "skipped ") + ("sentence" if sentence(s) else "word") + " inline"] += 1
    if ok:
        plan.append([zh, [r]])
if len(sys.argv) > 9:
    ttr = json.load(open(sys.argv[9]))
    used = {r[1] for _, refs in plan for r in refs if r[0] == "code"}
    for en, refs, _ in json.load(open(sys.argv[8])):
        zh = ttr.get(en)
        refs = [r for r in refs if not (r[0] == "tcode" and r[1] in used)]
        if zh and refs:
            assert en.count("{}") == zh.count("{}"), en
            plan.append([zh, refs])
            stats["kept template"] += len(refs)
for t, ln, s, n, nc in json.load(open(rows)):
    if t in addr_zh:
        refs = [list(r) for r in S[t][ln]]
        if refs:
            plan.append([addr_zh[t], refs])
            stats["kept addr"] += len(refs)
# A length-table slot can be shared by several pointer tables (copies of one match).
# Rewriting the length for one copy and leaving another copy's pointer on the
# English text would hand that copy a length that does not fit its string, so a
# slot is rewritten for every table that uses it, or for none.
slot_refs = defaultdict(list)
for t, d in S.items():
    for ln, refs in d.items():
        for r in refs:
            if r[0] == "table":
                slot_refs[(r[2], r[3])].append(list(r))
slot_zh, dropped = {}, set()
for zh, refs in plan:
    for r in refs:
        if r[0] == "table":
            k = (r[2], r[3])
            if slot_zh.setdefault(k, zh) != zh:
                dropped.add(k)  # two different translations claim one slot
done = {(r[1]) for zh, refs in plan for r in refs if r[0] == "table"}
for e in plan:
    e[1] = [r for r in e[1] if not (r[0] == "table" and (r[2], r[3]) in dropped)]
for k, zh in slot_zh.items():
    if k in dropped:
        stats["dropped table (conflict)"] += 1
        continue
    extra = [r for r in slot_refs[k] if r[1] not in done]
    if extra:
        plan.append([zh, extra])
        stats["kept table (shared length)"] += len(extra)
plan = [e for e in plan if e[1]]
json.dump(plan, open(out, "w"), ensure_ascii=False)
if nofit:
    json.dump(sorted(set(nofit)), open(out + ".nofit.json", "w"), ensure_ascii=False, indent=0)
    print(f"{len(nofit)} inline sites skipped: translation longer than the original ({len(set(nofit))} strings, see {out}.nofit.json)")
for k in sorted(stats):
    print(f"{k:28} {stats[k]}")
placed = {s for s, _, _, _ in items}
print(f"{len(plan)} strings in plan; {len(placed)} of {len(tr)} translations have a resolved reference")
