"""List text that is new in this build and may need a translation.

usage: candidates.py SCRATCH_DIR [--mark]

Reads all_strings.json and templates.json written by build.sh and prints, as JSON,
the UI-looking strings and templates that have no translation and were not in an
earlier build. translations/seen.txt holds a short hash of every string already
looked at, so each release only shows what it added; --mark records the current
build there once its new text has been translated or judged not to be UI.
"""
import hashlib
import json
import re
import sys

here = __file__.rsplit("/", 2)[0]
SEEN = here + "/translations/seen.txt"
NOT_UI = re.compile(r"[a-z][A-Z][a-z]|::|[a-z]_[a-z]|\\|^[A-Z_ ]+$|[=<>\[\]|;@#$^*`\x00-\x1f\x7f]")


def ui(s, template=False):
    s = s.replace("{}", "") if template else s
    return bool(re.match(r"[A-Z0-9“\"']", s.strip() or "-") and " " in s.strip() and 3 <= len(s) <= 300
                and re.search(r"[a-z]{2}", s) and not NOT_UI.search(s) and "{" not in s)


def h(kind, s):
    return hashlib.sha1(f"{kind}\0{s}".encode()).hexdigest()[:10]


def main(scratch, mark=False):
    tr = {}
    for f in ("zh-CN.json", "zh-CN.short.json", "zh-CN.templates.json"):
        tr.update(json.load(open(f"{here}/translations/{f}")))
    for v in json.load(open(here + "/translations/zh-CN.sites.json")).values():
        tr.update({e[0]: e[1] for e in v if isinstance(e, list)})
    strings = {r[2] for r in json.load(open(scratch + "/all_strings.json"))}
    templates = {r[0] for r in json.load(open(scratch + "/templates.json"))}
    cur = {h("s", s): s for s in strings if ui(s)}
    cur.update({h("t", s): s for s in templates if ui(s, True)})
    try:
        seen = set(open(SEEN).read().split())
    except FileNotFoundError:
        seen = set()
    new = {k: s for k, s in cur.items() if k not in seen and s not in tr}
    out = {"strings": sorted(s for k, s in new.items() if s in strings and h("s", s) == k),
           "templates": sorted(s for k, s in new.items() if s in templates and h("t", s) == k)}
    if mark:
        open(SEEN, "w").write("\n".join(sorted(seen | set(cur))) + "\n")
        print(f"marked {len(set(cur) - seen)} new strings as seen ({len(seen | set(cur))} in all)", file=sys.stderr)
    else:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
        print(f"\n{len(out['strings'])} strings and {len(out['templates'])} templates are new and untranslated",
              file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], "--mark" in sys.argv[2:])
