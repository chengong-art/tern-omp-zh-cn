#!/usr/bin/env bun
// File-scoped application incl. exact template-part matching.
import ts from '/Users/devin/omp-i18n-tool/node_modules/typescript/lib/typescript.js';
import { readFileSync, writeFileSync } from 'fs';
const dictPath = process.argv[2];
const repo = process.argv[3] || '/Users/devin/oh-my-pi';
const dict = JSON.parse(readFileSync(dictPath, 'utf8'));
const escPlain = (zh, q) => {
  let s = zh.replace(/\\/g, '\\\\');
  s = q === "'" ? s.replace(/'/g, "\\'") : s.replace(/"/g, '\\"');
  return s.replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
};
const escTpl = zh => zh.replace(/\\/g,'\\\\').replace(/`/g,'\\`').replace(/\$\{/g,'\\${');
let total = 0, missedTotal = 0;
for (const [rel, entries] of Object.entries(dict)) {
  const path = `${repo}/${rel}`;
  let text;
  try { text = readFileSync(path, 'utf8'); } catch { console.log('SKIP missing', rel); continue; }
  const map = new Map();
  for (const e of entries) if (e.zh) map.set(e.en, e.zh);
  const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true,
    rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const edits = [];
  function visit(n) {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      if (map.has(n.text)) edits.push({ start: n.getStart(sf), end: n.getEnd(), kind: 'plain', en: n.text });
    } else if (ts.isTemplateExpression(n)) {
      for (const p of [n.head, ...n.templateSpans.map(s => s.literal)]) {
        if (map.has(p.text)) edits.push({ start: p.getStart(sf), end: p.getEnd(), kind: 'part', en: p.text });
      }
    }
    ts.forEachChild(n, visit);
  }
  visit(sf);
  edits.sort((a, b) => a.start - b.start);
  const clean = []; let lastEnd = -1;
  for (const e of edits) if (e.start >= lastEnd) { clean.push(e); lastEnd = e.end; }
  const done = new Set();
  for (const e of clean.reverse()) {
    const raw = text.slice(e.start, e.end);
    const zh = map.get(e.en);
    if (e.kind === 'plain') {
      text = text.slice(0, e.start) + raw[0] + (raw[0] === '`' ? escTpl(zh) : escPlain(zh, raw[0])) + raw[0] + text.slice(e.end);
    } else {
      // template part: strip head/tail delimiters
      const headLen = raw.startsWith('`') || raw.startsWith('}') ? 1 : 0;
      const tailLen = raw.endsWith('`') || raw.endsWith('${') ? (raw.endsWith('${') ? 2 : 1) : 0;
      text = text.slice(0, e.start + headLen) + escTpl(zh) + text.slice(e.end - tailLen);
    }
    done.add(e.en);
  }
  const missed = [...map.keys()].filter(k => !done.has(k));
  if (clean.length) writeFileSync(path, text);
  total += clean.length; missedTotal += missed.length;
  if (missed.length) console.log(`${rel}: ${clean.length} applied, MISSED ${missed.length}: ${missed.map(m=>m.slice(0,30)).join(' | ')}`);
}
console.log(`TOTAL applied=${total} missed=${missedTotal}`);
