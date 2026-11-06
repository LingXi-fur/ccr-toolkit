# CCR Toolkit

**Focused, offline-testable fixes and diagnostics for Claude Code Router.**
**为 Claude Code Router 提供聚焦、可离线验证的修复与诊断小工具。**

[![Offline checks](https://github.com/LingXi-fur/ccr-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/LingXi-fur/ccr-toolkit/actions/workflows/ci.yml)
[MIT](LICENSE) · Zero runtime dependencies · No account, key, or install step

[English](#english) · [简体中文](#简体中文)

---

## English

### Why this exists

Two failure classes cost real hours before they were understood:

1. **A screenshot became a billable wall of text.** One 219 KB image inside a `tool_result` was forwarded as inline base64 text — **147,618 input tokens for a single image**, and four of them reliably triggered upstream `400 context_too_large`.
2. **A legal partial tool round was rejected.** Claude Code may send one assistant message with N tool calls and answer with M < N results in the next user message. Some upstreams demand an output for every call and answer `400 No tool output found for function call`.

CCR Toolkit extracts the fixes for both, plus a drift checker and a publish gate, into one small repository you can **read, test offline, and adapt** — without copying anyone's credentials or routing setup.

> **Experimental `0.1.0` · Unofficial.** Algorithms have synthetic tests; real CCR plugin loading is not yet verified. This is a toolkit to evaluate, not a drop-in installer.

### Quick start

Requirements: **Node.js 20+**, **Python 3.9+**, **Bash**. No `npm install`.

```bash
git clone https://github.com/LingXi-fur/ccr-toolkit.git
cd ccr-toolkit
bash scripts/test.sh
```

Synthetic messages and temporary configurations only. **No network, no model calls, no production changes, no restarts.** Current suite: **107/107** (plugin 27, doctor 31, content scanner 22, preflight gate 27). Not real-host integration evidence; check the badge run for the commit you care about.

### What's inside

| Component | Solves | Guardrail |
| --- | --- | --- |
| `strip-tool-images` | Tool-result media and detected large binary payloads → text placeholders | Explicit provider opt-in; top-level user media untouched |
| `fix-partial-tool-rounds` | Omits assistant tool calls still missing results in the next user message | Only edits assistant calls; keeps a placeholder for emptied turns |
| `ccr-doctor` | Environment drift across Claude Code / CCR profile / wrapper; permissions; optional health & usage | Offline by default; parses wrappers, never executes them |
| `check_public_content.py` | Likely credentials, personal paths, unexpected files before publishing | Reports locations only, never matched values |

The media transform in one line:

```text
tool_result: [image(base64…), text("Build passed")]
              ↓
tool_result: [text("[image omitted]"), text("Build passed")]
```

**Lossy compatibility, not a multimodal upgrade** — use only against an upstream with a confirmed problem.

### Repository layout

```text
plugins/tool-result-compat/   the two gateway transforms (single .mjs, no deps)
bin/ccr-doctor.sh             thin entry for the doctor
scripts/doctor.py             static diagnostics (bounded reads, snapshot DB)
scripts/check_public_content.py  publish-content gate (worktree / index / HEAD)
scripts/run_real_host.py      preflight-only real-host readiness gate
tests/                        107 offline regressions (Python + Node)
docs/                         compatibility, verification, troubleshooting (Chinese)
```

### Diagnostics without side effects

```bash
# Show options only
bash bin/ccr-doctor.sh --help

# Check drift across the three config surfaces (defaults to your real paths)
bash bin/ccr-doctor.sh

# Optional, explicit opt-in extras
bash bin/ccr-doctor.sh --health --front-port 3460 --core-port 3461
bash bin/ccr-doctor.sh --usage --expected-model example-model --expected-provider example-provider
```

Boundaries worth knowing before you trust a result:

- **Static fields only**: `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `DISABLE_AUTOUPDATER`. Wrappers are parsed as literal exports — anything beyond blank lines, comments, and simple numeric exports of those fields fails as unsupported. A FAIL here means "cannot verify", not "definitely drifted".
- **Databases**: a bounded copy is queried read-only; active `-wal`/`-shm`/`-journal` sidecars are refused. Never delete live sidecars to make a check pass.
- **Attestation limits**: a green `/health` does not prove service identity; recorded provider/model does not prove what the upstream actually executed. SKIP is not a pass.

Exit codes: `0` PASS/WARN, `1` FAIL (or WARN under `--strict`), `2` invalid arguments.

### Evaluate the plugin before wiring it in

`setup(ctx)` returns two `gatewayRequestTransforms`, selectable by ID. **No provider is selected by default, so nothing changes until you opt in.** See the [configuration example](examples/plugin-config.example.json) — it is not a complete registration file, and this repo deliberately ships no install command for an unverified host interface.

Real-host status, stated once and honestly:

- Host plugin facts (fields, permissions, module resolution, transform application) were **source-audited on CCR 3.0.22**. The local install moved to **3.1.3 on 2026-11-06**; those facts were not re-audited, and the preflight metadata gate now pins 3.1.3.
- Real loading is **unverified; CCR has never been started by this repo**. `run_real_host.py` is preflight-only — explicit opt-in permits a metadata check and a fixed `true` sandbox probe, never CCR startup; even success ends `BLOCKED integration-not-implemented`, exit 2.
- Three individually approved probes exited −6 / 71 / −11 while the policy approached a complete bootstrap set; the hard-stop rule paused that route. Resuming needs a separately scoped isolation mechanism and fresh authorization. No unsandboxed fallback exists.

Details: [compatibility](docs/compatibility.md) · [verification](docs/verification.md).

### Check what you will actually publish

```bash
python3 -I scripts/check_public_content.py          # working directory
python3 -I scripts/check_public_content.py --index  # complete staged snapshot
python3 -I scripts/check_public_content.py --head   # complete committed snapshot
```

Git modes read the selected object snapshot (not worktree files), reject links/submodules/conflicts/missing objects instead of skipping, disable lazy fetch and transports, and byte-bound all Git output. Heuristic gate, not a secret-free guarantee. CI additionally checks HEAD on every push.

### Small by design

- **No** global bundle patching, auto-registration, model switching, or credential storage.
- **No** stop/restart/kill, database restore, checkpoint, or permission changes.
- **No** personal configurations, conversations, screenshots, or production replays bundled.
- Static review findings were fixed and re-reviewed; that is not a security certification.

### Contribute & license

Most welcome: **version-specific integration evidence, synthetic edge cases, clearer diagnostics, translations**. Run `bash scripts/test.sh` before proposing changes. Bug reports: versions, expected behavior, synthetic reproduction — never raw databases, requests, logs, or secrets. Sensitive reports follow [SECURITY.md](SECURITY.md).

MIT licensed. Independent from, and not endorsed by, [Claude Code Router](https://github.com/musistudio/claude-code-router).

---

## 简体中文

### 为什么有这个仓库

两类真实故障，弄明白之前各耗掉数小时：

1. **一张截图变成一整段计费文本。** `tool_result` 里一张 219 KB 图片被按内联 base64 文本转发——**单图 147,618 input tokens**，四张就稳定触发上游 `400 context_too_large`。
2. **合法的部分工具回合被拒。** Claude Code 允许一条 assistant 消息发 N 个工具调用、下一条 user 消息只回 M < N 个结果；部分上游要求每个调用都有输出，直接回 `400 No tool output found for function call`。

CCR Toolkit 把这两个修复，加上漂移检查与发布门禁，提取成一套**源码可读、离线可测、按需接入**的小工具，不需要照搬任何人的密钥或路由配置。

> **实验候选版 `0.1.0` · 非官方。** 算法有合成测试；真实 CCR 插件加载尚未验证。适合先评估、再适配，不是开箱即用的安装器。

### 快速开始

要求：**Node.js 20+、Python 3.9+、Bash**，无需 `npm install`。

```bash
git clone https://github.com/LingXi-fur/ccr-toolkit.git
cd ccr-toolkit
bash scripts/test.sh
```

只用合成消息与临时配置。**不联网、不调模型、不改生产配置、不重启服务。** 当前套件 **107/107**（插件 27、doctor 31、内容扫描 22、预检门禁 27）。这不是实机集成证据；请核对你所关注提交对应的徽章 Actions 运行。

### 里面有什么

| 组件 | 解决什么 | 防护 |
| --- | --- | --- |
| `strip-tool-images` | 工具结果媒体与检测到的大二进制 → 文本占位 | 必须显式选 provider；顶层 user 媒体不动 |
| `fix-partial-tool-rounds` | 略去下一条 user 消息中仍无结果的 assistant 工具调用 | 只改 assistant 调用；空回合保留占位 |
| `ccr-doctor` | Claude Code / CCR profile / wrapper 三面环境漂移；权限；可选健康与用量 | 默认离线；解析 wrapper、绝不执行 |
| `check_public_content.py` | 发布前的疑似凭据、个人路径、非预期文件 | 只报位置，不回显命中内容 |

媒体变换一行示例：

```text
tool_result: [image(base64…), text("Build passed")]
              ↓
tool_result: [text("[image omitted]"), text("Build passed")]
```

**这是有损兼容，不是多模态增强**——仅用于已确认有问题的上游。

### 仓库布局

```text
plugins/tool-result-compat/   两个网关 transform（单 .mjs，零依赖）
bin/ccr-doctor.sh             doctor 薄入口
scripts/doctor.py             静态诊断（有界读取、快照查库）
scripts/check_public_content.py  发布内容门禁（工作区 / 暂存 / HEAD）
scripts/run_real_host.py      仅预检的实机就绪门禁
tests/                        107 项离线回归（Python + Node）
docs/                         兼容、验证、排错文档（中文）
```

### 无副作用的诊断

```bash
# 只看参数
bash bin/ccr-doctor.sh --help

# 检查三个配置面的漂移（默认读当前用户真实路径）
bash bin/ccr-doctor.sh

# 可选、需显式开启
bash bin/ccr-doctor.sh --health --front-port 3460 --core-port 3461
bash bin/ccr-doctor.sh --usage --expected-model example-model --expected-provider example-provider
```

信任结果前先知道的边界：

- **只查静态字段**：`CLAUDE_CODE_AUTO_COMPACT_WINDOW`、`CLAUDE_CODE_MAX_CONTEXT_TOKENS`、`DISABLE_AUTOUPDATER`。wrapper 按字面量 export 解析——空行、注释、上列字段独立数字 export 之外的任何 shell 都报不支持。FAIL 含义是"无法验证"，不是"确定漂移"。
- **数据库**：有界复制后只读查询；发现活跃 `-wal`/`-shm`/`-journal` 即拒绝。永远不要为通过检查去删生产旁文件。
- **证明力上限**：`/health` 绿不证明服务身份；记录的 provider/model 不证明上游实际执行的模型。SKIP 不等于通过。

退出码：`0` PASS/WARN，`1` FAIL（`--strict` 下 WARN 也算），`2` 参数错误。

### 插件先评估，再接入

`setup(ctx)` 返回两个 `gatewayRequestTransforms`，按 ID 可分别启用。**默认不选任何 provider，未显式启用前不改任何请求。** 见[配置样例](examples/plugin-config.example.json)——它不是完整注册文件，宿主接口未验证前本仓库刻意不提供安装命令。

实机状态，一次说清：

- 宿主插件事实（字段、权限、module 解析、transform 应用）系 **CCR 3.0.22 安装源码审计**。本机 2026-11-06 升至 **3.1.3**；上述事实未随 3.1.3 复审，预检元数据门禁已改 pin 3.1.3。
- 真实加载**未验证；本仓库从未启动过 CCR**。`run_real_host.py` 仅预检——显式 opt-in 只允许元数据检查与固定 `true` 沙箱探针，绝不启动 CCR；即便探针成功也以 `BLOCKED integration-not-implemented`、退出码 2 结束。
- 三次逐次批准的探针以 −6 / 71 / −11 退出，策略逐步逼近完整启动集合；按硬停点规则该路线已暂停。恢复需另定隔离机制与新的授权范围。不提供无沙箱降级。

细节：[兼容边界](docs/compatibility.md) · [验证记录](docs/verification.md)。

### 检查真正要发布的内容

```bash
python3 -I scripts/check_public_content.py          # 工作目录
python3 -I scripts/check_public_content.py --index  # 完整暂存快照
python3 -I scripts/check_public_content.py --head   # 完整已提交快照
```

Git 模式读选定对象快照（不是工作区文件）；链接、子模块、冲突、缺失对象一律拒绝而不是跳过；禁 lazy fetch 与传输协议，Git 输出全部有界。属启发式门禁，不保证零秘密。CI 每次推送另查 HEAD。

### 有意保持小而克制

- **不**改全局 bundle、不自动注册、不切模型、不存凭据。
- **不** stop/restart/kill、不恢复数据库、不 checkpoint、不改权限。
- **不**附带个人配置、会话、截图或生产重放。
- 静态复核发现的问题已修复并复核；不把这包装成安全认证。

### 贡献与许可

最欢迎：**具体 CCR 版本的接入证据、合成边界用例、更清楚的诊断、翻译**。提交前跑 `bash scripts/test.sh`。报问题请附版本、预期行为、合成复现——不上传原始数据库、请求、日志或秘密。敏感报告走 [SECURITY.md](SECURITY.md)。

MIT 许可。独立于 [Claude Code Router](https://github.com/musistudio/claude-code-router)，不代表上游背书。
