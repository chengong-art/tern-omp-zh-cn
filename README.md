# Tern 与 omp 简体中文汉化

给 [Tern](https://scl.so/tern)（macOS 终端）和 omp（oh-my-pi 编码智能体）打简体中文补丁的工具和译文表。

**非官方项目。** 仓库里只有脚本和译文，不包含 Tern 或 omp 的任何程序文件；补丁打在你自己安装的那份程序上。Tern、omp 的名称和程序归各自的作者所有。

| 对象 | 测试过的版本 | 方式 |
| --- | --- | --- |
| Tern | 0.9.16、0.9.26、0.9.30、0.9.32（macOS，Apple 芯片；0.9.30 已复测 Option-as-Alt、中文菜单和帧同步文案；0.9.32 构建、签名验证通过） | 生成一份汉化后的 `Tern.app` 副本，原程序只读不改 |
| omp | 19.1.1（npm / Homebrew 安装的 JS 版） | 就地修改 `cli.js`，自动留备份，可一键还原 |
| omp | 19.1.1（源码，commit `31ffee51`） | 源码级全量汉化 patch（20,527 条 / 1,789 文件），见 [`omp-src/`](omp-src/) |

0.9.32 UI 验证通过：从精确路径启动新构建的主程序，唯一 `Tern Tray` helper 和 daemon 稳定运行超过 2 分钟；键盘设置稳定。已验证中文菜单项：`重开标签`、`隐藏组件栏`、`从手机连接`、`共享会话`。

仍为英文的菜单项：

- Tern：`Check for Updates…`、`Services`、`Hide Others`、`Show All`、`Quit tern`
- File：`New Window`、`New Note`、`Close Window`
- View：`Enter Full Screen`
- Tern Tray：`1 running`、`Open Tern`、`New Window`、`Start at Login`、`Settings…`、`Restart Session Daemon`、`Stop Sessions…`

标准 `Edit`/`Window` 菜单项按预期保留英文。

已编译好的 macOS arm64 单文件二进制和打好的源码包在 [Releases](https://github.com/chengong-art/tern-omp-zh-cn/releases)（tag `omp-19.1.1-zh-cn`）里，不用自己装 bun 和 Rust 工具链。

其他版本没有测试过。Tern 的补丁按特征在二进制里定位改写点，换版本后个别条目可能定位不到（会列出来，那几处保持英文）；omp 的补丁按原文匹配，新版本里改过措辞的条目会被跳过并列出来。

## Tern

需要：macOS（arm64）、Python 3 和 `numpy`、Xcode 命令行工具（提供 `codesign`）。

```sh
cd tern
tools/make_app.sh /Applications/Tern.app dist/Tern.app
```

第一个参数必须是**未修改过的官方 Tern.app**，脚本只读取它。几分钟后得到 `dist/Tern.app`，确认能正常打开后再用它替换 `/Applications/Tern.app`。替换前请自己留一份官方原版。

汉化范围：设置页、命令面板、菜单、提示与报错等界面文字，目前约 7800 处引用。仍有一部分保持英文（见下文“做不到的部分”）。

### 使用前要知道的

- **签名变了。** 修改后官方的 Developer ID 签名失效，脚本改用本机临时签名（ad-hoc），并保留原有权限声明。macOS 会把它当成另一个程序：辅助功能、完全磁盘访问等系统授权和钥匙串访问可能要重新授予。
- **自动更新会换回英文版。** Tern 更新后需要对新版重新打补丁，而且新版不保证能直接打上。
- **命令面板按中文搜索。** 面板匹配的是显示出来的文字，已汉化的命令用英文关键词搜不到。
- **替换正在运行的 Tern 后**，窗口和后台会话守护进程版本不一致，Tern 会提示重启守护进程；重启一次即可。

### 原理

Tern 是一个 Rust 写的单文件程序，没有可替换的语言资源，界面文字直接编译在二进制里。工具不改动任何英文原文，而是：

1. 在二进制末尾新增一个只读段，把中文字符串放进去；
2. 找出代码和数据里引用英文字符串的位置（指针与长度），改成指向中文；
3. 解析不了的引用保持原样，所以最坏情况是那一处仍显示英文。

主要脚本（`tern/tools/`）：

| 脚本 | 作用 |
| --- | --- |
| `analyze.py`、`extract.py`、`dump.py` | 扫描二进制，找出字符串及其引用方式 |
| `inline.py` | 处理被编译器内联成“定长拷贝”的字符串 |
| `templates.py` | 处理带占位符的格式化模板 |
| `resolve.py` | 按 `zh-CN.sites.json` 的描述，在当前这份二进制里找出手工改写点的地址 |
| `mkplan.py` | 根据译文表决定哪些引用可以安全替换 |
| `patch.py` | 写入新段并改写引用 |
| `build.sh`、`make_app.sh` | 串起以上步骤，并重新签名（`Contents/Helpers/` 下的辅助程序自动发现） |
| `candidates.py` | 列出新版本里新增、还没有译文的界面文字 |
| `fetch.py` | 登录 Stencil，查询并下载最新的官方 Tern 安装包 |
| `preflight.py` | 每日自动化的预检脚本：保管登录状态，只在出现新版本时启动一次汉化会话 |

译文表（`tern/translations/`）：

| 文件 | 内容 |
| --- | --- |
| `zh-CN.json` | 英文 → 中文，主表 |
| `zh-CN.short.json` | 定长位置放不下时使用的较短译法 |
| `zh-CN.templates.json` | 带 `{}` 占位符的模板；占位符顺序不能变 |
| `zh-CN.sites.json` | 自动扫描处理不了的位置，按特征描述而不是按地址：同一个单词在不同位置含义不同的（`context`，用相邻的字符串区分；译文为 `null` 时保留该副本原文）、数据表里的条目（`data`）、只有指针没有长度的引用（`ptr`）、用立即数拼出来的短字符串（`imm`，如菜单栏标题） |
| `seen.txt` | `candidates.py` 已经看过的字符串的短哈希，用来只列出新版本新增的部分 |

**单个英文单词不会一律替换。** `Agent`、`Light` 这样的词既可能是界面标签，也可能是写进配置文件的值或协议里的标识符，后者译掉会导致设置存不上。`mkplan.py` 只在能确认是界面文字时才替换，传给“接收标识符的函数”的单词一律不动。

### 适配新版本

Tern 是邀请制内测，安装包要登录后才能下载。`fetch.py` 用 Tern 客户端自己的设备登录方式（不需要密码）：

```sh
cd tern
python3 tools/fetch.py login              # 只需一次：打印一个链接，在浏览器里批准
python3 tools/fetch.py latest             # 最新稳定版的版本号、下载地址和 SHA-256
python3 tools/fetch.py download build     # 下载、校验并解压出 build/Tern.app
tools/make_app.sh build/Tern.app dist/Tern.app
python3 tools/candidates.py .scratch      # 新增的、还没有译文的界面文字
```

登录状态存在 `~/.config/tern-zh/stencil.json`（可用环境变量 `TERN_ZH_STATE` 改位置）。Stencil 的刷新令牌每用一次就换一个新的，所以这个文件每次运行都会被改写，不能复制到别处同时使用。

`make_app.sh` 的输出里要看两处：

- `sites: … N of M entries not placed` 和后面的 `not placed` 行：`zh-CN.sites.json` 里没能在新版本中定位到的条目。通常是它旁边的字符串变了，改一下该条目的邻居列表即可；`.scratch/all_strings.json` 里能查到新版本的字符串和地址。
- `candidates.py` 列出的新文字：译好后加进 `zh-CN.json` 或 `zh-CN.templates.json`，再运行 `candidates.py .scratch --mark` 记为已处理。

### 每日自动适配

一条 Devin 自动化每天运行一次 `tools/preflight.py`（在会话启动之前、独立的小虚拟机里）：

1. 用保存在自动化状态里的刷新令牌换取访问令牌，并把换回来的新令牌存回去；
2. 查询最新稳定版，和 `tern/VERSION`（上一次处理完的 `版本号 构建号`）比较；
3. 相同就结束，不启动会话；不同就把安装包的限时下载链接和 SHA-256 交给一次新会话。

会话按上面的步骤打补丁、补译、验证签名和启动，通过后更新译文表和 `tern/VERSION` 并推送；汉化后的 `Tern.app` 只作为会话附件交付，不发布。令牌只在预检脚本里，会话拿不到。同一个构建最多尝试 3 次。

登录失效（长期未运行，或令牌被别处用掉）时预检会报错，需要重新批准一次设备登录，把新的刷新令牌存为组织 secret `STENCIL_REFRESH_TOKEN`。本机用 `fetch.py login` 要另外批准一次，不要把自动化的令牌拿到本机用。

### 做不到的部分

- 引用方式解析不出来的字符串保持英文，例如命令面板的部分分组标题。
- 定长位置的译文不能比原文占的字节多，放不下且没有短译法的条目保持英文。
- 模板里的占位符不能调换顺序，语序差别大的句子只能保持英文或将就原顺序。

## omp

需要：Python 3，以及通过 npm / Homebrew 安装的 omp（`omp` 指向 `cli.js` 的那种）。编译成单个可执行文件的 omp 不支持。

```sh
python3 omp/omp_zh.py            # 汉化（升级 omp 后重新运行一次即可）
python3 omp/omp_zh.py --restore  # 还原成英文
```

不带参数时，脚本自己找到 `omp` 对应的 `cli.js`，并在同目录留一份 `cli.js.en.bak`。也可以手动指定：`omp_zh.py CLI_JS [备份路径 [译文表]]`。已经打开的 omp 会话不受影响，新开的会话才是中文。

汉化范围（`omp/zh-CN.json`）：

- 输入框占位符、新版本提示
- 启动时的小贴士
- 斜杠命令和子命令的说明
- 状态、警告、错误等一次性提示

**有意保持英文的部分**，因为改了会影响功能：

- 斜杠命令名、用法里的参数语法、快捷键标记；
- 发给模型的提示词、工具描述和子智能体描述，翻译它们会改变模型的行为；
- 小贴士前的 `Tip: ` 前缀，程序按它的长度截取文字。

设置菜单、快捷键列表等其余界面目前也还是英文。

## 参与改进

译文有问题或想补充，直接改对应的 JSON 文件即可：键是程序里的英文原文，必须一字不差；值是中文。Tern 的译文要保留原文里的占位符、快捷键符号和首尾空格。

## 许可

脚本和译文以 MIT 许可发布，见 [LICENSE](LICENSE)。
