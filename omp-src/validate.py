#!/usr/bin/env python3
"""Validate omp translation artifacts before they are baked into a patch.

The rules here encode failures that are easy to miss in a large en→zh table:
positional placeholders, accidental extra quotes, escaped template interpolation,
and lowercase identifier literals that are commonly used as enum/action/wire values.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

CJK = r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
CJK_RE = re.compile(CJK)
LOWER_IDENT_RE = re.compile(r"^[a-z][\w.-]*$")
NAMED_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")
TEMPLATE_EXPR_RE = re.compile(r"(?<!\\)\$\{[^{}]*\}")
PRINTF_TOKEN_RE = re.compile(r"%(?:\d+\$)?[sdif]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# A Chinese string literal directly used as a comparison/case/member-test value is
# almost certainly a broken enum, union, action id, wire value, or parser literal.
CJK_STRING = r"([" + "'`\"]" + r")(?:(?!\1).)*" + CJK + r"(?:(?!\1).)*\1"
COMPARE_TO_CJK_RE = re.compile(r"(?:===|!==|==|!=)\s*" + CJK_STRING)
CJK_COMPARE_RE = re.compile(CJK_STRING + r"\s*(?:===|!==|==|!=)")
CASE_CJK_RE = re.compile(r"\bcase\s+" + CJK_STRING + r"\s*:")
MEMBER_CJK_RE = re.compile(r"\.(?:includes|indexOf|startsWith|endsWith)\(\s*" + CJK_STRING)
NESTED_LOCALE_RE = re.compile(r"([\"'])(['\"])[a-z]{2}-[A-Z]{2}\2\1")
TS_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".cjs"}


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path}: cannot load JSON: {exc}")


def fmt_entry(file, index):
    return f"{file}[{index}]" if index is not None else str(file)


def placeholders_ok(en, zh):
    problems = []
    en_template = TEMPLATE_EXPR_RE.findall(en)
    zh_template = TEMPLATE_EXPR_RE.findall(zh)
    if en_template != zh_template:
        problems.append(f"template expressions {en_template!r} != {zh_template!r}")
    if en.count("{}") != zh.count("{}"):
        problems.append(f"{{}} count {en.count('{}')} != {zh.count('{}')}")
    en_named = NAMED_PLACEHOLDER_RE.findall(en)
    zh_named = NAMED_PLACEHOLDER_RE.findall(zh)
    if en_named != zh_named:
        problems.append(f"{{{{name}}}} sequence {en_named!r} != {zh_named!r}")
    en_printf = PRINTF_TOKEN_RE.findall(en)
    zh_printf = PRINTF_TOKEN_RE.findall(zh)
    if en_printf != zh_printf:
        problems.append(f"printf tokens {en_printf!r} != {zh_printf!r}")
    if en.count("\\${") != zh.count("\\${"):
        problems.append(f"escaped interpolation count {en.count(chr(92) + '${')} != {zh.count(chr(92) + '${')}")
    return problems


def outer_quote_problem(en, zh):
    if len(zh) >= 2 and zh[0] == zh[-1] and zh[0] in "'\"":
        if not (len(en) >= 2 and en[0] == en[-1] == zh[0]):
            return f"zh adds outer {zh[0]} quotes not present in en"
    return None


def validate_source_dict(path, source_root=None):
    data = load_json(path)
    errors, warnings = [], []
    if not isinstance(data, dict):
        return [f"{path}: top level must be an object mapping files to entries"], warnings

    total = 0
    for rel, entries in data.items():
        if not isinstance(rel, str) or not rel:
            errors.append(f"{path}: file keys must be non-empty strings")
            continue
        if source_root and not (Path(source_root) / rel).is_file():
            errors.append(f"{rel}: file does not exist in source root")
        if not isinstance(entries, list):
            errors.append(f"{rel}: value must be a list of {{en, zh}} entries")
            continue
        seen = {}
        for index, entry in enumerate(entries):
            total += 1
            where = fmt_entry(rel, index)
            if not isinstance(entry, dict):
                errors.append(f"{where}: entry must be an object")
                continue
            en, zh = entry.get("en"), entry.get("zh")
            if not isinstance(en, str) or not en:
                errors.append(f"{where}: en must be a non-empty string")
                continue
            if not isinstance(zh, str) or not zh:
                errors.append(f"{where}: zh must be a non-empty string")
                continue
            if CONTROL_RE.search(en) or CONTROL_RE.search(zh):
                errors.append(f"{where}: control character in en/zh")
            if "line" in entry and not isinstance(entry["line"], int):
                errors.append(f"{where}: line must be an integer when present")

            key = (en, entry.get("line"))
            if key in seen:
                if seen[key] == zh:
                    warnings.append(f"{where}: duplicate entry for en={en!r}")
                else:
                    errors.append(f"{where}: conflicting duplicate translations for en={en!r}")
            else:
                seen[key] = zh

            for problem in placeholders_ok(en, zh):
                errors.append(f"{where}: placeholder mismatch: {problem}")
            quote_problem = outer_quote_problem(en, zh)
            if quote_problem:
                errors.append(f"{where}: {quote_problem}; en={en!r}, zh={zh!r}")
            if LOWER_IDENT_RE.fullmatch(en) and zh != en:
                errors.append(
                    f"{where}: lowercase identifier literal {en!r} must stay machine-stable "
                    f"(zh should be identical, got {zh!r})"
                )
    print(f"source dictionary: {total} entries across {len(data)} files")
    return errors, warnings


def validate_runtime_dict(path):
    data = load_json(path)
    errors, warnings = [], []
    if not isinstance(data, dict):
        return [f"{path}: top level must be an object"], warnings
    for section in ("descriptions", "tips", "messages"):
        values = data.get(section)
        if not isinstance(values, dict):
            errors.append(f"{path}: {section} must be an object")
            continue
        for en, zh in values.items():
            where = f"{path}:{section}:{en[:60]!r}"
            if not isinstance(en, str) or not isinstance(zh, str) or not en or not zh:
                errors.append(f"{where}: keys and values must be non-empty strings")
                continue
            en_template = TEMPLATE_EXPR_RE.findall(en)
            zh_template = TEMPLATE_EXPR_RE.findall(zh)
            if en_template != zh_template:
                errors.append(f"{where}: template expressions {en_template!r} != {zh_template!r}")
            if en.count("{}") != zh.count("{}"):
                errors.append(f"{where}: {{}} count mismatch")
            en_flags = re.findall(r"--[A-Za-z0-9][\w-]*", en)
            zh_flags = re.findall(r"--[A-Za-z0-9][\w-]*", zh)
            if en_flags != zh_flags:
                errors.append(f"{where}: flag sequence {en_flags!r} != {zh_flags!r}")
    literals = data.get("literals")
    if not isinstance(literals, list):
        errors.append(f"{path}: literals must be a list")
    else:
        for index, item in enumerate(literals):
            if not (isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) and x for x in item)):
                errors.append(f"{path}:literals[{index}]: must be [src, dst] non-empty strings")
                continue
            en_template = TEMPLATE_EXPR_RE.findall(item[0])
            zh_template = TEMPLATE_EXPR_RE.findall(item[1])
            if en_template != zh_template:
                errors.append(f"{path}:literals[{index}]: template expressions {en_template!r} != {zh_template!r}")
    print(
        "runtime dictionary: "
        + ", ".join(f"{section}={len(data.get(section, {}))}" for section in ("descriptions", "tips", "messages"))
        + f", literals={len(data.get('literals', []))}"
    )
    return errors, warnings


def iter_source_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "dist", "build"}]
        for name in filenames:
            if Path(name).suffix in TS_SUFFIXES:
                yield Path(dirpath) / name


def scan_patched_tree(root, audit_machine_usage=False):
    errors, warnings = [], []
    scanned = 0
    for path in iter_source_files(root):
        scanned += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root)
        for lineno, line in enumerate(lines, 1):
            if not CJK_RE.search(line) and not NESTED_LOCALE_RE.search(line):
                continue
            if NESTED_LOCALE_RE.search(line):
                errors.append(f"{rel}:{lineno}: nested quotes inside locale literal: {line.strip()[:160]}")
            if not CJK_RE.search(line) or not audit_machine_usage:
                continue
            if COMPARE_TO_CJK_RE.search(line) or CJK_COMPARE_RE.search(line):
                warnings.append(f"{rel}:{lineno}: Chinese literal used in comparison: {line.strip()[:160]}")
            elif CASE_CJK_RE.search(line):
                warnings.append(f"{rel}:{lineno}: Chinese literal used as switch case: {line.strip()[:160]}")
            elif MEMBER_CJK_RE.search(line):
                warnings.append(f"{rel}:{lineno}: Chinese literal used in string membership/search call: {line.strip()[:160]}")
    print(f"patched tree: scanned {scanned} source files")
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dict", default="omp-src/zh-CN-src.json")
    parser.add_argument("--runtime-dict", default="omp/zh-CN.json")
    parser.add_argument("--source-root", help="pristine oh-my-pi checkout used to verify dictionary file paths")
    parser.add_argument("--patched-root", help="patched oh-my-pi checkout to scan for machine-string leaks")
    parser.add_argument(
        "--audit-machine-usage",
        action="store_true",
        help="also report heuristic Chinese comparison/case/search usages as warnings",
    )
    parser.add_argument("--warnings-as-errors", action="store_true")
    args = parser.parse_args()

    errors, warnings = [], []
    if not Path(args.source_dict).is_file():
        errors.append(f"{args.source_dict}: file does not exist")
    else:
        part_errors, part_warnings = validate_source_dict(args.source_dict, args.source_root)
        errors += part_errors
        warnings += part_warnings
    if not Path(args.runtime_dict).is_file():
        errors.append(f"{args.runtime_dict}: file does not exist")
    else:
        part_errors, part_warnings = validate_runtime_dict(args.runtime_dict)
        errors += part_errors
        warnings += part_warnings
    if args.patched_root:
        part_errors, part_warnings = scan_patched_tree(Path(args.patched_root), args.audit_machine_usage)
        errors += part_errors
        warnings += part_warnings

    for item in warnings:
        print(f"WARNING {item}")
    for item in errors:
        print(f"ERROR {item}")
    print(f"validation: {len(errors)} errors, {len(warnings)} warnings")
    if errors or (args.warnings_as_errors and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
