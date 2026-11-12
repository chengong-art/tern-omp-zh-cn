"""Localise omp's JS bundle (cli.js) into Simplified Chinese, in place.

usage: omp_zh.py [CLI_JS [BACKUP [TRANSLATIONS.json]]]     apply (or re-apply)
       omp_zh.py --restore [CLI_JS [BACKUP]]               put the original back

Defaults: CLI_JS is what `omp` on PATH resolves to, BACKUP is CLI_JS + ".en.bak",
TRANSLATIONS is zh-CN.json next to this script.

Only interface text is touched: slash-command descriptions (a `description:"…"`
that sits next to a command handler), the tips list, transient status/warning/error
notices, and a few exact literals. Command names, usage syntax, and the prompts and
tool descriptions sent to the model stay in English.

The bundle is always rebuilt from BACKUP (created from CLI_JS on first run), so
reruns are safe. `omp update` replaces cli.js; the stale backup is then detected
and refreshed from the new file.
"""
import bisect
import json
import re
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
restore = "--restore" in args
args = [a for a in args if a != "--restore"]
if args:
    cli = Path(args[0])
else:
    found = shutil.which("omp")
    if not found:
        sys.exit("omp not found on PATH: pass the path to cli.js")
    cli = Path(found).resolve()
bak = Path(args[1]) if len(args) > 1 else cli.with_name(cli.name + ".en.bak")
trs = Path(args[2]) if len(args) > 2 else Path(__file__).with_name("zh-CN.json")
MARK = "/*omp-zh*/"
cur = cli.read_text(encoding="utf-8")
if not cur.startswith("#!") or "// @bun" not in cur[:200]:
    sys.exit(f"{cli} is not omp's JS bundle (the compiled omp binary is not supported)")
patched = cur.endswith(MARK + "\n")
if restore:
    if not patched:
        sys.exit("cli.js is not patched: nothing to restore")
    if not bak.exists():
        sys.exit(f"backup {bak} is missing: reinstall omp to get the original back")
    shutil.copy2(bak, cli)
    print(f"restored {cli}")
    sys.exit(0)
if not patched:  # fresh install or updated omp: this file is the new original
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cli, bak)
elif not bak.exists():
    sys.exit(f"cli.js is already patched but backup {bak} is missing: reinstall omp, then rerun")
s = bak.read_text(encoding="utf-8")
assert not s.endswith(MARK + "\n"), "the backup itself is a patched copy"
T = json.loads(trs.read_text(encoding="utf-8"))


def js(text):  # JSON string literal body, ASCII only, \uXXXX in upper case as the bundler writes it
    return re.sub(r"\\u[0-9a-f]{4}", lambda m: "\\u" + m.group(0)[2:].upper(), json.dumps(text, ensure_ascii=True)[1:-1])


def esc(text):  # raw text inside an existing literal: only non-ASCII is escaped
    return "".join(c if ord(c) < 128 else js(c) for c in text)


stats = {"description": 0, "tip": 0, "literal": 0, "message": 0}
missing = []
handlers = [m.start() for m in re.finditer(r"handleTui:|handle:|subcommands:\[", s)]
edits = []  # (start, end, replacement)
for en, zh in T["descriptions"].items():
    lit = 'description:"' + js(en) + '"'
    hit = 0
    for m in re.finditer(re.escape(lit), s):
        k = bisect.bisect_left(handlers, m.start())
        if min(abs(handlers[x] - m.start()) for x in (k - 1, k) if 0 <= x < len(handlers)) <= 1500:
            edits.append((m.start(), m.end(), 'description:"' + js(zh) + '"'))
            hit += 1
    stats["description"] += hit
    if not hit:
        missing.append(en)
for en, zh in T["tips"].items():
    lit = js(en)
    p = s.find(lit)
    if p < 0 or s.count(lit) != 1:
        missing.append(en)
        continue
    edits.append((p, p + len(lit), js(zh)))
    stats["tip"] += 1
# transient notices: the literal first argument of showStatus/showWarning/showError
# (keys are the JS literals as written in the bundle, quotes and escapes included)
for m in re.finditer(r'\.(?:showStatus|showWarning|showError)\(("(?:[^"\\]|\\.)*")(?=[,)])', s):
    zh = T.get("messages", {}).get(m.group(1))
    if zh:
        edits.append((m.start(1), m.end(1), '"' + js(zh) + '"'))
        stats["message"] += 1
for src, dst in T["literals"]:
    if s.count(src) != 1:
        missing.append(src)
        continue
    p = s.find(src)
    edits.append((p, p + len(src), esc(dst)))
    stats["literal"] += 1
edits.sort()
assert all(a[1] <= b[0] for a, b in zip(edits, edits[1:])), "overlapping edits"
out, last = [], 0
for a, b, r in edits:
    out += [s[last:a], r]
    last = b
out.append(s[last:])
res = "".join(out)
if not res.endswith("\n"):
    res += "\n"
cli.write_text(res + MARK + "\n", encoding="utf-8")
print(stats, f"{len(missing)} not applied")
for m in missing:
    print("  skipped:", m[:90])
