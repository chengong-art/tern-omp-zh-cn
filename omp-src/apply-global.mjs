#!/usr/bin/env bun
// Global content-keyed application of zh-CN dict onto source literals.
// Multi-word / long / capitalized entries apply in ANY file; short lowercase
// single-word entries apply only in their original dict files (machine-literal risk).
import ts from '/Users/devin/omp-i18n-tool/node_modules/typescript/lib/typescript.js';
import { readFileSync, writeFileSync } from 'fs';
import { readdirSync, statSync } from 'fs';
import { join, relative } from 'path';

const dictPath = process.argv[2];
const repo = process.argv[3] || '/Users/devin/oh-my-pi';
const dict = JSON.parse(readFileSync(dictPath, 'utf8'));

// build global map: en -> zh (only consistent ens)
const en2zh = new Map();
const conflicts = new Set();
const fileScoped = new Map(); // file -> Map(en->zh)
for (const [rel, entries] of Object.entries(dict)) {
  for (const e of entries) {
    if (!e.zh) continue;
    if (en2zh.has(e.en) && en2zh.get(e.en) !== e.zh) conflicts.add(e.en);
    else en2zh.set(e.en, e.zh);
    if (!fileScoped.has(rel)) fileScoped.set(rel, new Map());
    fileScoped.get(rel).set(e.en, e.zh);
  }
}
for (const c of conflicts) en2zh.delete(c);

const isGlobal = en => en.includes(' ') || en.includes('\n');
const globalMap = new Map();
for (const [en, zh] of en2zh) if (isGlobal(en)) globalMap.set(en, zh);
console.log(`global entries: ${globalMap.size}, file-scoped files: ${fileScoped.size}`);

const escPlain = (zh, q) => {
  let s = zh.replace(/\\/g, '\\\\');
  s = q === "'" ? s.replace(/'/g, "\\'") : s.replace(/"/g, '\\"');
  return s.replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
};
const escTpl = zh => zh.replace(/\\/g,'\\\\').replace(/`/g,'\\`').replace(/\$\{/g,'\\${');

// collect files
const files = [];
function walk(dir) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '.git' || name === 'dist' || name === 'build' || name === '.omp') continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p);
    else if (/\.tsx?$/.test(name) && !name.endsWith('.d.ts')) files.push(p);
  }
}
walk(join(repo, 'packages'));
console.log(`scanning ${files.length} files`);

let totalApplied = 0;
const missedCount = new Map();
for (const [en] of globalMap) missedCount.set(en, 0);

for (const path of files) {
  const rel = relative(repo, path);
  let text;
  try { text = readFileSync(path, 'utf8'); } catch { continue; }
  const local = new Map(globalMap);
  if (fileScoped.has(rel)) for (const [en, zh] of fileScoped.get(rel)) if (!local.has(en)) local.set(en, zh);
  const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true,
    rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const edits = [];
  const wanted = new Set(local.keys());
  function visit(n) {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      if (wanted.has(n.text)) edits.push({ start: n.getStart(sf), end: n.getEnd(), kind: 'plain', en: n.text });
    } else if (ts.isTemplateExpression(n)) {
      const parts = [n.head, ...n.templateSpans.map(s => s.literal)];
      const cooked = parts.map(p => p.text).join('${}');
      if (wanted.has(cooked)) {
        const zhSegs = local.get(cooked).split('${}');
        if (zhSegs.length === parts.length)
          parts.forEach((p, i) => edits.push({ start: p.getStart(sf), end: p.getEnd(), kind: 'tpl', zhSeg: zhSegs[i], en: cooked }));
      }
    }
    ts.forEachChild(n, visit);
  }
  visit(sf);
  if (!edits.length) continue;
  edits.sort((a, b) => a.start - b.start);
  const clean = []; let lastEnd = -1;
  for (const e of edits) if (e.start >= lastEnd) { clean.push(e); lastEnd = e.end; }
  for (const e of clean.reverse()) {
    const raw = text.slice(e.start, e.end);
    if (e.kind === 'plain') {
      const zh = local.get(e.en);
      text = text.slice(0, e.start) + raw[0] + (raw[0] === '`' ? escTpl(zh) : escPlain(zh, raw[0])) + raw[0] + text.slice(e.end);
    } else {
      const headLen = raw.startsWith('`') || raw.startsWith('}') ? 1 : 0;
      const tailLen = raw.endsWith('`') || raw.endsWith('${') ? (raw.endsWith('${') ? 2 : 1) : 0;
      text = text.slice(0, e.start + headLen) + escTpl(e.zhSeg) + text.slice(e.end - tailLen);
    }
  }
  writeFileSync(path, text);
  totalApplied += clean.length;
  console.log(`${rel}: ${clean.length}`);
}
console.log(`TOTAL applied=${totalApplied}`);
