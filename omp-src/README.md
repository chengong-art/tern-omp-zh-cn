# omp 源码级简体中文汉化（v18.20.2）

对 [oh-my-pi](https://github.com/can1357/oh-my-pi) 做**源码级**全量汉化的产物。与 `../omp/` 的就地补丁不同，这里直接改源码树里的英文字符串字面量，覆盖全部用户可见文案。

**非官方项目。** 补丁打在你自己克隆的 oh-my-pi 仓库上；omp 的名称和程序归原作者所有。

| 项 | 值 |
| --- | --- |
| 基线 | `@oh-my-pi/pi-coding-agent` **18.20.2**，commit `7c49c11aa5bbaf3f418406583096168ad5b8c6dc` |
| 词表 | `zh-CN-src.json`，**20,280 条** `{en, zh}` 对照，覆盖 ~1,660 个文件 |
| 补丁 | `omp-zh-src.patch`，**1,670 个文件**改动，`git apply` 干净应用 |
| 测试 | `bun test` 40,520 个测试：除断言英文原文的失败外无翻译引起的破坏；二进制 `--smoke-test` 通过 |

## 用法

```sh
git clone https://github.com/can1357/oh-my-pi.git && cd oh-my-pi
git checkout 7c49c11aa5bbaf3f418406583096168ad5b8c6dc
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
| `omp-zh-src.patch` | `git diff` 产物，打在 7c49c11a 上 |
| `zh-CN-src.json` | `{文件相对路径: [{en, zh}]}`，全部 20,280 条对照 |
| `apply-dict.mjs` | 词表回灌工具：`bun apply-dict.mjs <dict.json>`，用于增量更新 |

## 已知边界

- 约 100 条刻意保留的英文：产品名、模型/方言名、单位（`1 KB`、`30 fps`）、协议字段
- 测试断言英文原文的用例必然失败（预期，非缺陷）
- 新版 upstream 发布后 patch 需要重出——`zh-CN-src.json` 里按文件分组的 `en` 原文可用于快速核对哪些条目已失效
