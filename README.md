# CCR Toolkit

**Small, opt-in fixes for awkward Claude Code Router failures.**

**把难排查的 CCR 问题，变成可验证、按需启用的小工具。**

[![Offline checks](https://github.com/LingXi-fur/ccr-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/LingXi-fur/ccr-toolkit/actions/workflows/ci.yml)
[MIT License](LICENSE) · No third-party runtime dependencies · No API key needed to try

[English](#english) · [简体中文](#简体中文)

---

## English

### Less guesswork. Smaller fixes.

A few screenshots should not turn tool output into a wall of billable base64. A partial tool round should not send you hunting through an entire router installation. And checking configuration drift should not require a restart.

CCR Toolkit extracts focused fixes and diagnostics from hands-on troubleshooting into a small repository you can **read, test offline, and adapt**—without copying someone else's credentials or routing setup.

**Clone it if you want to:**

- Investigate tool-result media being forwarded as text and inflating requests.
- Reproduce an upstream rejection of partially completed parallel tool calls.
- Compare selected settings across Claude Code, a CCR profile, and its wrapper.
- Start with synthetic regression tests instead of experimenting on a live session.

> **Experimental `0.1.0` · Unofficial.** The algorithms have synthetic tests; real CCR plugin registration/loading and cross-version compatibility are not yet verified. Treat this as a toolkit to evaluate, not a drop-in production installer.

### Try it—no account, key, or install step

Requirements: **Node.js 20+**, **Python 3.9+**, and **Bash**. No `npm install` needed.

```bash
git clone https://github.com/LingXi-fur/ccr-toolkit.git
cd ccr-toolkit
bash scripts/test.sh
```

The suite uses synthetic messages and temporary configurations/databases. **No network, model calls, production configuration changes, or service restarts.**

The initial candidate passed **58/58 local tests**: 27 plugin, 26 doctor, and 5 content-scanner tests. GitHub CI runs the offline suite on Linux; the badge links to current results. See [verification details](docs/verification.md).

### What's inside?

| Component | What it does | Guardrail |
| --- | --- | --- |
| `strip-tool-images` | Replaces tool-result media and detected large binary payloads with text placeholders | Explicit provider opt-in; top-level user media left untouched |
| `fix-partial-tool-rounds` | Omits assistant tool calls without results in the immediately following user message | Only edits assistant calls; preserves an empty assistant turn with a placeholder |
| `ccr-doctor` | Checks selected static environment fields and input-file permissions; optionally checks health and usage aggregates | Offline by default; no automatic repairs or config execution |
| Release-content checks | Flags likely credentials, personal paths, and unexpected files | Reports locations, not matched values; not a substitute for manual review |

For example, the media transform turns a tool result like this:

```text
tool_result: [image(base64…), text("Build passed")]
                           ↓
tool_result: [text("[image omitted]"), text("Build passed")]
```

**This is lossy compatibility handling, not a multimodal upgrade.** Use it only for an upstream with a confirmed problem. If the task needs tool-returned images or documents, dropping them is not a complete fix.

### Run a read-only diagnostic

```bash
# Show options without inspecting your configuration
bash bin/ccr-doctor.sh --help

# Inspect existing, quiet configuration copies kept OUTSIDE this repository
bash bin/ccr-doctor.sh \
  --ccr-dir ../private-ccr-input \
  --claude-dir ../private-claude-input \
  --strict
```

Without path options, doctor uses the current user's `.claude-code-router` and `.claude` directories. It checks only these static environment fields:

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
- `CLAUDE_CODE_MAX_CONTEXT_TOKENS`
- `DISABLE_AUTOUPDATER`

It parses the wrapper; **it never executes it**. Dynamic expressions and unsupported formats fail rather than silently count as a match. Exit codes: `0` PASS/WARN, `1` FAIL or WARN under `--strict`, `2` invalid arguments. SKIP is not a pass.

**Database boundary:** doctor reads a bounded source file into a private temporary copy, then queries the copy with SQLite read-only settings. It refuses inputs with `-wal`, `-shm`, or `-journal` sidecars. Supply an existing, quiet, consistent standalone backup; **do not delete live sidecars to make it pass**. It is not a backup tool or a hostile-database sandbox.

Optional checks require explicit flags:

```bash
# Loopback /health only; no proxy, redirects, or model request
bash bin/ccr-doctor.sh --health --front-port 3460 --core-port 3461

# Aggregate recorded routes for the past 24 hours; no request-body output
bash bin/ccr-doctor.sh --usage \
  --expected-model example-model \
  --expected-provider example-provider
```

Health success does not attest to service identity. Recorded provider/model fields do not prove which model an upstream actually executed. Usage input must also satisfy the quiet-database requirement.

### Evaluate the plugin before connecting it

The module exports `setup(ctx)` and returns `gatewayRequestTransforms`. Both transforms retain their IDs above and can be selected separately. **No provider is selected by default, so no requests are changed.**

See the [configuration example](examples/plugin-config.example.json) for `pluginConfig` fields. It is **not** a complete CCR registration file. This repository deliberately does not invent an installation command for an unverified host interface.

Before connecting it, read [compatibility and integration boundaries](docs/compatibility.md). In particular, partial-round handling assumes results are in the immediately following user message; histories with results spread across multiple messages are not supported.

### Small by design

- **No global bundle patching, automatic registration, model switching, or credential storage.**
- **No stop/restart/kill, database restore, checkpoint, or permission changes.**
- **No personal configurations, conversations, screenshots, or production replays bundled.**
- Static review findings were fixed and the five fixes were re-reviewed; this is not a comprehensive security certification.

Explore: [troubleshooting](docs/troubleshooting.md) · [compatibility](docs/compatibility.md) · [verification](docs/verification.md) · [security](SECURITY.md) · [sources & notices](THIRD_PARTY_NOTICES.md). Supporting documents are currently in Chinese; this README covers the essential commands and limits in both languages.

### Help make it useful on more setups

Contributions most welcome: **version-specific integration evidence, synthetic edge cases, clearer diagnostics, and documentation translations**. Run `bash scripts/test.sh` before proposing a change. For a bug report, include versions, expected behavior, and a synthetic reproduction—not raw databases, requests, logs, or secrets. See [SECURITY.md](SECURITY.md) for sensitive reports.

MIT licensed. Independent from, and not endorsed by, [Claude Code Router](https://github.com/musistudio/claude-code-router).

---

## 简体中文

### 少猜原因，少动环境。

几张截图，不该让工具输出变成一大段按文本处理的 base64。一次未完成的并行工具回合，不该让你翻遍整个路由器安装目录。查配置漂移，更不该先重启服务。

CCR Toolkit 把实际排错中积累的修复与诊断，提取成一套**源码可读、离线可测、按需接入**的小工具。不需要照搬别人的密钥、路由配置或运行环境。

**遇到这些问题，值得 clone 看看：**

- 工具返回的图片或二进制被当作文本转发，请求体或 token 用量异常膨胀。
- 并行工具只返回部分结果，上游却要求每个调用都有 output。
- Claude Code、CCR profile 与 wrapper 的部分配置对不上。
- 想先用合成回归复现问题，而不是拿正在工作的会话试错。

> **实验候选版 `0.1.0` · 非官方组件。** 算法已做合成测试；真实 CCR 插件注册/加载与跨版本兼容尚未验证。适合先评估、再适配，不是开箱即用的生产安装器。

### 先试用：不需要账号、Key 或安装依赖

要求：**Node.js 20+、Python 3.9+、Bash**。无需 `npm install`。

```bash
git clone https://github.com/LingXi-fur/ccr-toolkit.git
cd ccr-toolkit
bash scripts/test.sh
```

测试只用合成消息与临时配置/数据库。**不联网、不调用模型、不改生产配置、不重启服务。**

首版候选的本地验证为 **58/58 通过**：插件 27 项、doctor 26 项、内容扫描 5 项。GitHub CI 在 Linux 运行离线套件，页首徽章可查看最新结果；详见[验证记录](docs/verification.md)。

### 里面有什么？

| 组件 | 解决什么 | 默认保护 |
| --- | --- | --- |
| `strip-tool-images` | 将工具结果媒体和检测到的大二进制替换为文本占位 | 必须显式选择 provider；顶层 user 媒体不动 |
| `fix-partial-tool-rounds` | 略去紧邻下一条 user 消息中尚无结果的 assistant 工具调用 | 只改 assistant 调用；空 assistant 回合保留占位 |
| `ccr-doctor` | 查部分静态环境配置与输入文件权限；可选健康检查和 usage 聚合 | 默认离线；不自动修复、不执行配置 |
| 发布内容检查 | 标记疑似凭据、个人路径和非预期文件 | 只报位置，不回显命中内容；不能代替人工审查 |

例如，媒体变换会把这类工具结果：

```text
tool_result: [image(base64…), text("Build passed")]
                           ↓
tool_result: [text("[image omitted]"), text("Build passed")]
```

**这是有损兼容，不是多模态能力增强。** 仅用于已确认有问题的上游。如果任务需要工具返回的图片或文档，删掉它们不算完整修复。

### 跑一次只读诊断

```bash
# 只看参数，不读取你的配置
bash bin/ccr-doctor.sh --help

# 检查仓库外已有、安静的独立配置副本
bash bin/ccr-doctor.sh \
  --ccr-dir ../private-ccr-input \
  --claude-dir ../private-claude-input \
  --strict
```

不指定路径时，doctor 使用当前用户的 `.claude-code-router` 与 `.claude`。静态环境比对仅包含：

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
- `CLAUDE_CODE_MAX_CONTEXT_TOKENS`
- `DISABLE_AUTOUPDATER`

只解析 wrapper，**不执行 wrapper**。动态表达式与不支持的格式会报告失败，不会悄悄算作一致。退出码：`0` 为 PASS/WARN，`1` 为 FAIL（`--strict` 下 WARN 也为 `1`），`2` 为参数错误。SKIP 不等于通过。

**数据库边界：** 有界读取源文件到私有临时副本，再以 SQLite 只读设置查询副本。发现 `-wal`、`-shm`、`-journal` 即拒绝。请提供已有、安静且一致的独立备份；**不要为了通过检查而删除生产旁文件**。这不是备份工具，也不是敌对数据库沙箱。

可选检查必须显式开启：

```bash
# 只请求回环 /health；禁代理和重定向，不发模型请求
bash bin/ccr-doctor.sh --health --front-port 3460 --core-port 3461

# 聚合过去24小时的记录路由，不输出请求正文
bash bin/ccr-doctor.sh --usage \
  --expected-model example-model \
  --expected-provider example-provider
```

健康状态不能证明服务身份；usage 里的 provider/model 记录不能证明上游实际执行了哪个模型。usage 输入同样必须满足安静数据库要求。

### 插件先评估，再接入

模块导出 `setup(ctx)`，返回 `gatewayRequestTransforms`。两个 transform 保留上表中的 ID，可分别启用。**默认不选任何 provider，因此不改任何请求。**

[配置样例](examples/plugin-config.example.json) 展示 `pluginConfig` 字段，**不是完整 CCR 注册文件**。真实宿主接口未验证前，不提供猜测性的安装命令。

接入前请读[兼容与接入边界](docs/compatibility.md)。尤其注意：部分回合处理假定结果在紧邻的下一条 user 消息中；结果分散在多条消息里的历史不受支持。

### 有意保持小而克制

- **不改全局 bundle、不自动注册、不切模型、不保存凭据。**
- **不 stop/restart/kill、不恢复数据库、不 checkpoint、不改权限。**
- **不附带个人配置、会话、截图或生产重放。**
- 静态复核发现的问题已修复，五项修复已复核；不把这些包装成全面安全认证。

继续了解：[排错](docs/troubleshooting.md) · [兼容边界](docs/compatibility.md) · [验证记录](docs/verification.md) · [安全](SECURITY.md) · [来源与许可](THIRD_PARTY_NOTICES.md)。补充文档目前以中文为主，本 README 提供完整中英入口、命令与核心限制。

### 一起让更多环境用得上

欢迎贡献：**具体 CCR 版本的接入证据、合成边界用例、更清楚的诊断、文档翻译**。提交改动前运行 `bash scripts/test.sh`。报告问题请附版本、预期行为与合成复现，不上传原数据库、真实请求、日志或秘密。敏感问题按 [SECURITY.md](SECURITY.md) 处理。

MIT 许可。独立于 [Claude Code Router](https://github.com/musistudio/claude-code-router)，不代表上游背书。
