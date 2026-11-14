#!/usr/bin/env bun
// Apply {file: {en: zh}} translations onto source literals via TypeScript AST.
// en for templates uses "${}" as placeholder marker (one per interpolation);
// zh must use "${}" the same number of times — segments are mapped in order.
import ts from 'typescript';
import { readFileSync, writeFileSync } from 'fs';

const dictPath = process.argv[2];
const repo = process.argv[3] || '/Users/devin/oh-my-pi';
const dict = JSON.parse(readFileSync(dictPath, 'utf8'));

const escPlain = (zh, q) => {
  let s = zh.replace(/\\/g, '\\\\');
  s = q === "'" ? s.replace(/'/g, "\\'") : s.replace(/"/g, '\\"');
  return s.replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
};
const escTpl = zh => zh.replace(/`/g, '\\`');

let totalApplied = 0;
const totalMissed = [];
for (const [rel, entries] of Object.entries(dict)) {
  const path = `${repo}/${rel}`;
  let text;
  try { text = readFileSync(path, 'utf8'); } catch { console.log('SKIP missing', rel); continue; }
  const map = new Map();
  const lineMap = new Map(); // en -> [{line, zh}] for line-scoped entries
  if (Array.isArray(entries)) for (const e of entries) {
    if (!e.zh) continue;
    if (e.line) (lineMap.get(e.en) ?? lineMap.set(e.en, []).get(e.en)).push({ line: e.line, zh: e.zh });
    else map.set(e.en, e.zh);
  }
  else for (const [en, zh] of Object.entries(entries)) map.set(en, zh);
  if (!map.size && !lineMap.size) continue;

  const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true,
    rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const edits = [];
  const wanted = new Set(map.keys());
  function visit(n) {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      if (wanted.has(n.text)) edits.push({ start: n.getStart(sf), end: n.getEnd(), kind: 'plain', en: n.text });
      else if (lineMap.has(n.text)) {
        const l = sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
        for (const it of lineMap.get(n.text)) if (Math.abs(it.line - l) <= 2)
          edits.push({ start: n.getStart(sf), end: n.getEnd(), kind: 'plain', en: n.text, zhOverride: it.zh });
      }
    } else if (ts.isTemplateExpression(n)) {
      const parts = [n.head, ...n.templateSpans.map(s => s.literal)];
      const cooked = parts.map(p => p.text).join('${}');
      if (wanted.has(cooked)) {
        const zhSegs = map.get(cooked).split('${}');
        if (zhSegs.length === parts.length) {
          parts.forEach((p, i) => edits.push({ start: p.getStart(sf), end: p.getEnd(), kind: 'tpl', zhSeg: zhSegs[i], en: cooked }));
        } else {
          console.log(`  TPL-SEGMISMATCH ${rel} "${cooked.slice(0, 60)}"`);
        }
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
    if (e.kind === 'plain') {
      const zh = e.zhOverride ?? map.get(e.en);
      text = text.slice(0, e.start) + raw[0] + (raw[0] === '`' ? escTpl(zh) : escPlain(zh, raw[0])) + raw[0] + text.slice(e.end);
      done.add(e.en);
    } else {
      // literal part: first char is ` or }, last is ` or $ (of ${)
      const headLen = raw.startsWith('`') || raw.startsWith('}') ? 1 : 0;
      const tailLen = raw.endsWith('`') || raw.endsWith('${') ? (raw.endsWith('${') ? 2 : 1) : 0;
      text = text.slice(0, e.start + headLen) + escTpl(e.zhSeg) + text.slice(e.end - tailLen);
      done.add(e.en);
    }
  }
  const missed = [...wanted].filter(w => !done.has(w)).concat([...lineMap.keys()].filter(w => !done.has(w)));
  if (clean.length) writeFileSync(path, text);
  totalApplied += clean.length;
  for (const m of missed) totalMissed.push(`${rel}: ${m.slice(0, 80)}`);
  console.log(`${rel}: ${clean.length} applied, ${missed.length} missed`);
}
console.log(`TOTAL applied=${totalApplied} missed=${totalMissed.length}`);
if (totalMissed.length) writeFileSync('/Users/devin/omp-i18n/apply-missed.log', totalMissed.join('\n'));
