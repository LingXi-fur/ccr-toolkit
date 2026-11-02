# CCR Toolkit

把真实排错中积累的修复，整理成**可选择、可离线验证、不携带个人配置**的小工具。

**English:** Opt-in Claude Code Router compatibility transforms and offline-first diagnostics. No bundled credentials, model calls, auto-installation, or service restarts.

> 本地候选版 `0.1.0`，不是 CCR 官方组件。静态复核发现的5项问题已修复，并通过本轮修复复核；这不等同于整体安全认证。未验证真实 CCR 加载与跨版本兼容，暂不建议直接用于生产。详见[兼容边界](docs/compatibility.md)与[本次验证记录](docs/verification.md)。

## 两个核心工具

| 工具 | 解决什么 | 默认行为 |
| --- | --- | --- |
| `tool-result-compat` | 某些网关把工具媒体编码当文本；严格上游拒绝尚未返回结果的工具调用 | provider 未明确选中时不改请求 |
| `ccr-doctor` | 发现静态配置漂移、敏感文件权限、可选路由聚合与健康状态异常 | 不联网、不执行配置、不修改原文件 |

插件保留 `strip-tool-images`、`fix-partial-tool-rounds` 两个 transform ID，可分别启用。

**重要：媒体净化是有损降级。** 它删除工具结果里的媒体/大二进制，不是多模态转换器。pending-call 处理会删除当前请求中没有对应结果的 `tool_use`，不是通用工具编排。只对已确认存在相应问题的上游使用。

## Quick Start：先离线验证

要求：Node.js 20+、Python 3.9+、Bash；macOS 或 Linux。无第三方运行依赖，无需 `npm install`。

```bash
bash scripts/test.sh
# 等价入口，需要 npm 已安装：
npm test
```

测试全部使用合成消息和临时配置/数据库，不访问现用 CCR、不发模型请求、不连接网络。日志默认写终端；不要把真实诊断输出直接贴入 issue。

## 静态诊断

```bash
# 默认目录：当前用户的 .claude-code-router 与 .claude
bash bin/ccr-doctor.sh

# 建议先对自己准备的独立配置副本检查
bash bin/ccr-doctor.sh \
  --ccr-dir ./private-input/ccr \
  --claude-dir ./private-input/claude \
  --strict
```

`private-input` 仅为命令示意；真实输入放仓库外，不能提交。

当前支持 CCR SQLite `app_config(key,value_json)` 的 `default` 项、`profile.profiles` 中名为 `code` 的 profile，以及静态 wrapper 格式。比对字段仅：

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
- `CLAUDE_CODE_MAX_CONTEXT_TOKENS`
- `DISABLE_AUTOUPDATER`

不执行 wrapper，不读取请求正文、会话或环境变量全集。只解析数字/数字字符串的静态 `export`，动态 shell 表达式报告不支持。

**数据库安全边界：** 原始数据库通过有界文件读取复制到私有临时目录，再以 SQLite `mode=ro&immutable=1` 查询副本；源路径不会由 SQLite 打开。存在 `-wal`、`-shm`、`-journal` 时拒绝读取。不能将这当作活跃数据库备份机制；请提供已有、安静且一致的独立备份。工具不生成备份、不 checkpoint、不删旁文件。输入有大小和查询预算，详见兼容文档。

退出码：`0` 为 PASS/WARN，`1` 为 FAIL（`--strict` 下 WARN 也为 `1`），`2` 为参数错误。SKIP 不等于通过。

### 显式开启回环健康检查

```bash
bash bin/ccr-doctor.sh --health --front-port 3460 --core-port 3461
```

仅请求 `http://127.0.0.1:<port>/health`，禁代理和重定向，3 秒 socket 超时、8 KiB 响应上限。不发模型请求。HTTP 状态通过**不能证明**服务身份、进程拓扑或模型身份。

### 可选 usage 聚合

```bash
bash bin/ccr-doctor.sh --usage \
  --expected-model example-model \
  --expected-provider example-provider
```

读取过去 24 小时的匹配事件数、provider 不匹配数和 5xx 数，只输出数字。活跃数据库同样拒绝。usage 中的 provider/model 是**记录路由**，不是上游真实执行模型的证明。

## 插件接入状态

插件导出 `setup(ctx)`，返回 `gatewayRequestTransforms`。配置字段样例见 [`examples/plugin-config.example.json`](examples/plugin-config.example.json)；这是 `pluginConfig` 的内容，**不是完整 CCR 注册文件**。

已核对安装包中的部分宿主接口，但未完成 module 路径、注册结构和真实加载验证。因此首版不提供猜测性安装命令，不自动注册插件、不改 SQLite、不重启服务。请先阅读[接入与撤销说明](docs/compatibility.md#接入与撤销)。

## 安全与来源

- 原创部分 MIT；第三方来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 不包含个人配置、数据库、API Key、会话、截图或历史重放。
- `python3 -I scripts/check_public_content.py` 做本地内容门禁；不是完整 DLP/安全审计。
- 静态代码复核及本轮修复复核已完成，不宣称安全认证。安全报告请先看 [SECURITY.md](SECURITY.md)。

## 后续方向

先完成宿主加载验证和独立复核，再考虑通用路由状态栏。重启、进程终止、数据库恢复等高风险维护脚本不属于首版；已有用量统计项目不并入此仓库。
