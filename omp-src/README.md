# omp 源码级简体中文汉化（v19.2.1）

对 [oh-my-pi](https://github.com/can1357/oh-my-pi) 做**源码级**全量汉化的产物。与 `../omp/` 的就地补丁不同，这里直接改源码树里的英文字符串字面量，覆盖全部用户可见文案。

**非官方项目。** 补丁打在你自己克隆的 oh-my-pi 仓库上；omp 的名称和程序归原作者所有。

| 项 | 值 |
| --- | --- |
| 基线 | `@oh-my-pi/pi-coding-agent` **19.2.1**，commit `b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac` |
| 词表 | `zh-CN-src.json`，**24,262 条** `{en, zh}` 对照，覆盖 2,689 个文件 |
| 补丁 | `omp-zh-src.patch`，**2,698 个文件**改动，`git apply` 干净应用 |
| 测试 | `bun test` 39,631 个测试：失败项经纯净基线对照核实全部为"断言英文原文"类或环境性(基线同样失败)；二进制 `--smoke-test` 通过 |

## 用法

```sh
git clone https://github.com/can1357/oh-my-pi.git && cd oh-my-pi
git checkout b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac
git apply /path/to/omp-zh-src.patch
bun install
bun packages/coding-agent/src/cli.ts   # 中文界面
```

编译单文件二进制：在仓库根目录 `bun packages/coding-agent/scripts/build-binary.ts`（需要 bun 在 PATH 里），产物在 `packages/coding-agent/dist/omp`。

## 翻译约定

- 专有名词 / 产品名 / 标识符保留英文，后接全角括号中文注释（≤8 字），如 `MCP（模型上下文协议）`
- 斜杠命令名（`/xxx`）、flag 名（`--xxx`）、模型名、URL、协议字段名、发给 LLM 的 prompt 文本、`prompts/*.md` 一律不译
- `{}`、`{{var}}`、`${x}` 占位符数量与顺序不变；英文复数占位（`skill${s}`）按中文习惯省略
- 术语：session→会话、plan mode→计划模式、provider→服务商、context→上下文、token→词元
- 机器比较值（枚举、wire 字段、状态码、主题名）保持英文；仅显示文本中文化

## 文件

| 文件 | 内容 |
| --- | --- |
| `omp-zh-src.patch` | `git diff` 产物，打在 b55cd1bb 上 |
| `zh-CN-src.json` | `{文件相对路径: [{en, zh}]}`，全部 24,262 条对照 |
| `apply-dict.mjs` | 词表回灌工具：`bun apply-dict.mjs <dict.json>`，用于增量更新 |
| `validate.py` | 译文表与补丁静态校验，CI 自动运行 |

## 自动校验

`validate.py` 固化了几类容易漏进大词表的错误：`${}` / `{}` / `{{name}}` 占位符数量和顺序、`\${` 转义、译文额外包一层引号、以及小写标识符字面量（枚举、action id、union、wire 值等）被翻译。小写标识符一律保守保持英文；需要汉化某个仅用于显示的小写词时，先确认同文件没有机器消费同一字面量。

```sh
python3 omp-src/validate.py
```

GitHub Actions 还会克隆上游 `v19.2.1`，确认 `omp-zh-src.patch` 能干净应用，并扫描补丁后的源码中是否再次出现 locale 引号嵌套（如 `"'zh-CN'"`）。`--audit-machine-usage` 可额外列出中文文案出现在比较、`case`、`includes` 等位置的可疑点；这些是启发式警告，需要人工判断生产者和比较方是否成对翻译。

## 已知边界

- 约 100 条刻意保留的英文：产品名、模型/方言名、单位（`1 KB`、`30 fps`）、协议字段
- 测试断言英文原文的用例必然失败（预期，非缺陷）
- 新版 upstream 发布后 patch 需要重出——`zh-CN-src.json` 里按文件分组的 `en` 原文可用于快速核对哪些条目已失效
- 19.2.1 词表由补丁后源码树反向提取生成：小写标识符形态的条目（含时长单位后缀 `s`/`m`/`ms` 等约 64 条）与 2 条引号边界模板片段未收入词表，未来增量回灌时这些字符串会保持英文；补丁本身不受影响
- 全局内容键回灌仅对多词字符串生效；单词级显示文案在文件移动后可能保持英文（防止污染 wire 协议枚举值的有意取舍）
