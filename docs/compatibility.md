# 兼容边界与验证范围

## 当前状态

| 层次 | 已核事实 | 尚未验证 |
| --- | --- | --- |
| CCR 宿主 | 本地 3.0.22 安装含 app plugin `setup(context)` 与 gateway transform 接口 | 其他发行版、注册完整结构、module 解析、真实加载 |
| 插件 | 合成 host 模拟按顺序调用 transforms；返回 `{body}` 或 `undefined` | 真服务请求、生产并发与跨版本行为 |
| Doctor | 合成 SQLite、临时 HOME、静态 wrapper、模拟 HTTP 边界 | 不同版本 schema、生产拓扑、真实健康状态 |
| 运行平台 | 本次在 macOS / Node 24 / Python 3.11 执行默认套件；shell入口另测Python 3.9 | Linux CI 和其他 Node/Python 组合需远端实际验证 |
| 独立复核 | 静态复核发现5项问题；已修复并补回归，本轮修复复核 APPROVE | 不等同于完整安全审计或发布验收 |

Node 20+、Python 3.9+ 是代码预期最低要求，不代表每个版本已测试。没有第三方包、锁文件或依赖安装步骤；原生包管理审计无依赖对象，不把这等同于供应链审计通过。

## 插件语义

`setup({pluginConfig, logger})` 返回两个 `gatewayRequestTransforms`。每项 `transform({body,routedModel})` 在需要修改时返回 `{body: 新对象}`；不需要时返回 `undefined`，不修改输入。

- `providers`：数组或逗号分隔字符串，必须明确配置。默认空列表，即不改任何请求。
- `transforms`：数组或逗号分隔字符串。默认两个；空列表同样表示两个，不表示禁用。要全部禁用，请移除宿主注册或把 `providers` 设为空。
- `routedModel`：当前仅接受 `provider/model`，provider 为字母、数字、点、下划线、连字符；model 另允许冒号。多段路径和逗号形式不变换，给固定诊断。较窄的语法是保守 no-op，不代表 CCR 所有合法模型都受支持。
- 未匹配 provider 或 route 异常：不变换，按原因每 5 分钟限频日志，实例内数字计数；不记录 route、body、会话或真实 provider 值。
- 阈值需为 1 到 16 MiB 的安全整数；占位符最多 512 字符，不允许控制字符。错误配置抛 `TypeError`，由宿主决定插件加载失败后的行为。
- 输入必须是 JSON 消息对象。克隆用 JSON 序列化，不支持循环引用或任意 JS 对象；这是兼容变换，不是通用内容安全过滤器。

### `strip-tool-images`

只扫描 `messages[].content[]` 内的 `tool_result`：

- `image/document/audio/video/file/container_upload` 换文本占位；小媒体也替换。
- 嵌套结构化二进制字段和较大的文本内 base64/data URL 按启发式检测；阈值衡量编码字符串字节，而不是解码后文件字节。
- 结构化字段达到 `maxInlinePayloadBytes` 就可替换；普通文本仅超过 `maxTextPayloadBytes` 才扫描。
- `tool_reference` 保留，空/null/missing 结果换空结果占位。
- 顶层 user 图片不变，普通文本不变；类 base64 的长字符串可能被误判。需要保留二进制原文时不要启用此 transform。

### `fix-partial-tool-rounds`

只修改 assistant 消息里的 `tool_use`，仅用紧邻的下一条 user 消息中的 `tool_result.tool_use_id` 配对，删除该回合没有对应结果的调用。assistant 只剩空内容时保留文本占位。user 消息不删改，不拿其它回合同 ID 的结果配对。结果若分散在多条 user 消息或非紧邻位置，不支持此启发式；不要在这类历史上启用。它不修复孤立 result 或错误顺序，也不应接收增量流片段。

## 接入与撤销

**真实 CCR 接入尚未验证，当前不提供可复制的宿主安装/注册命令。**

已在本地安装包观察到：插件条目读取 module/id/config；context 暴露 pluginConfig、logger、路径及注册入口；执行代码要求 `trusted-code`，gateway transforms 要求 gateway surface 与 `gateway-request-transforms` 权限。这不是一份完整注册 schema。

未核实之处包括 module 相对路径基准、surfaces 完整格式、是否存在额外 manifest 要求。源码目录未见 manifest，故本版不编造清单。

后续验证应在**单独准备并获授权的测试 CCR 实例**中进行：

1. 根据该版本正式插件文档/类型，确定 module 与注册结构。
2. 使用绝对路径指向插件文件，配置显式 provider；只提供合成消息和测试专用服务，禁止复制生产凭据。
3. 验证不匹配 provider 不变、两个 transform 可分别启用、返回新 body 被宿主采用，并核对无正文日志。
4. 仅在这类验证完成后，才补该版本接入步骤与已测标记。

撤销原则：移除测试实例中本插件的注册；保留原始配置的恢复点。是否必须重启由宿主版本决定。此工具集不自动执行这些步骤，不更改任何现用实例。

## Doctor 格式与资源上限

- 配置库：`app_config(key,value_json)`，`key='default'`，值为 JSON object；profile 为 `profile.profiles[]` 中唯一 `name='code'`。
- settings：JSON object 的 `env`；wrapper：只接受已列字段的静态数字 `export`，不解释 shell。
- usage：`usage_events(created_at,model,provider,status_code)`；时间由 SQLite `julianday` 解释，过去24小时，排除未来数据。模型精确匹配，provider 精确比较。
- JSON/wrapper 最大 2 MiB，源数据库最大 128 MiB，usage 最多 100000 行；超过上限报 FAIL，不静默抽样。
- 源数据库有 WAL/SHM/journal 即拒绝。未出现旁文件不保证外部写入永远不存在；仅支持安静输入，读取期修改/变化只做尽力检测，不是事务备份。
- 查询只在私有临时副本执行，`mode=ro&immutable=1`、`query_only=ON`、禁 trusted schema，并设置 VM 步数预算。拒绝视图、虚表与生成列；Python 支持 `setlimit` 时另限 SQLite 值长度为 2 MiB，旧 Python 仍可能先读取较大的普通存储值（上界为源库限制），不应拿它处理敌对数据库。
- 默认不读 usage、不联网、不执行外部程序。`--health` 禁代理、重定向，3 秒 socket 超时和 8192 字节回复上限；并非对慢速分段响应的严格总耗时保证。
- `--expected-version` 仅比对路径名/父目录名，不执行二进制，因此不是二进制版本认证。默认跳过。

权限检查只告警输入文件 group/world 访问位或不同 owner，不进行全目录 ACL/父路径信任审计，不修改权限。最终组件软链接拒绝；不能据此推断整个路径树已防篡改。
