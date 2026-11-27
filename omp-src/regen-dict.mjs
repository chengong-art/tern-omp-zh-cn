// Regenerate zh-CN dictionary by AST-aligning original (HEAD) vs patched working tree.
import ts from '/Users/devin/omp-i18n-tool/node_modules/typescript/lib/typescript.js';
import { execSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = '/Users/devin/oh-my-pi';
const CJK = /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/;

function collectLits(src, fileName) {
  const sf = ts.createSourceFile(fileName, src, ts.ScriptTarget.Latest, true,
    fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out = [];
  function visit(n) {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      out.push(n.text);
    } else if (ts.isTemplateExpression(n)) {
      out.push(n.head.text);
      for (const span of n.templateSpans) {
        visit(span.expression);
        out.push(span.literal.text);
      }
    }
    ts.forEachChild(n, visit);
  }
  visit(sf);
  return out;
}

// files containing CJK in patched tree
const files = execSync(
  `rg -l --no-messages '\\p{Han}' packages -g '*.ts' -g '*.tsx' || true`,
  { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }
).trim().split('\n').filter(f => f && !f.includes('node_modules') && !f.includes('/dist/') && !f.includes('/build/'));

const dict = {};
let misaligned = 0, alignedFiles = 0;
for (const rel of files) {
  const abs = path.join(ROOT, rel);
  let orig;
  try {
    orig = execSync(`git show HEAD:${rel}`, { cwd: ROOT, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  } catch { continue; } // new file in working tree (not in HEAD) — skip
  const patched = fs.readFileSync(abs, 'utf8');
  const lo = collectLits(orig, rel);
  const lp = collectLits(patched, rel);
  const entries = new Map();
  const n = Math.min(lo.length, lp.length);
  for (let i = 0; i < n; i++) {
    const en = lo[i], zh = lp[i];
    if (en !== zh && CJK.test(zh) && en.length > 0) {
      entries.set(en, zh);
    }
  }
  if (lo.length !== lp.length) misaligned++;
  if (entries.size) {
    dict[rel] = [...entries.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([en, zh]) => ({ en, zh }));
    alignedFiles++;
  }
}
const sorted = Object.fromEntries(Object.entries(dict).sort(([a],[b])=>a.localeCompare(b)));
const total = Object.values(sorted).reduce((s, v) => s + v.length, 0);
fs.writeFileSync('/Users/devin/repos/tern-omp-zh-cn/omp-src/zh-CN-src.json.new', JSON.stringify(sorted, null, 2));
console.log(`files=${alignedFiles} entries=${total} misalignedLen=${misaligned}`);
