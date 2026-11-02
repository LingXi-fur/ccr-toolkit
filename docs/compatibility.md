# 兼容边界与验证范围

## 当前状态

| 层次 | 已核事实 | 尚未验证 |
| --- | --- | --- |
| CCR 宿主 | 3.0.22安装源码已核直接插件字段、权限、解析及transform返回应用 | configDir初始化/覆盖、市场manifest、真实加载与其他版本 |
| 插件 | 合成 host 模拟按顺序调用 transforms；返回 `{body}` 或 `undefined` | 真服务请求、生产并发与跨版本行为 |
| Doctor | 合成 SQLite、临时 HOME、字面量export文件、模拟 HTTP 边界 | 真实wrapper通常含额外shell，可能因不支持而FAIL |
| 运行平台 | macOS / Node24 / Python3.11；Python3.9.6另跑doctor与扫描器通过；首版Linux远端CI已通过 | 本轮Linux验证须核对对应提交的Actions结果，不能沿用首版成绩 |
| 独立复核 | 首版五项修复通过；本轮三项边界问题修复后静态复核APPROVE | 不等同于完整安全审计或发布验收 |

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

**真实 CCR 接入尚未验证，不提供自动安装/注册命令。** 以下是3.0.22安装源码的版本限定笔记，不是实机加载验收。

### 直接插件注册契约（源码已核）

| 字段 | 3.0.22直接加载路径的行为 |
| --- | --- |
| `id` | 非空id优先，其次key，再生成plugin-N；建议显式使用唯一id |
| `module` | module或path；缺少时不加载JS。支持本地`.js/.cjs/.mjs`，拒绝URL与包名 |
| `enabled` | 接受布尔；未给时标准化为true |
| `surfaces` | 兼容surface；字符串/数组限定列出的surface。对象未列字段仍可开启，不应把缺省当作最小权限 |
| `permissions` | 普通插件必须显式声明代码加载与gateway变换权限；不要依赖内置ID预设 |
| `config` | 原样成为context.pluginConfig，缺省为undefined |

直接module加载不会读取manifest；插件市场的manifest机制不在已核范围。`~`支持展开，相对路径基于宿主configDir并有不得越出的限制，但configDir的初始化和环境覆盖未完整追踪。为避免相对基准歧义，后续隔离验证应使用绝对路径。

下面仅展示**插件条目结构**，应由该版本宿主配置容器承载。不是可直接覆盖的完整配置，也没有做真实加载验证：

```json
{
  "id": "tool-result-compat",
  "module": "/absolute/path/to/ccr-toolkit/plugins/tool-result-compat/index.mjs",
  "enabled": true,
  "surfaces": ["gateway"],
  "permissions": ["trusted-code", "gateway-request-transforms"],
  "config": {
    "providers": ["example-provider"],
    "transforms": ["strip-tool-images", "fix-partial-tool-rounds"]
  }
}
```

`trusted-code`允许执行插件代码，不是沙箱承诺。不要给不信任的模块这些权限。`setup(ctx)`优先于`activate(ctx)`，返回注册对象会被采用；context包含全局config、pluginConfig、pluginId、paths、权限、logger和注册API。logger四级接口为带插件前缀的console包装。工具集只使用必要的配置与脱敏warn，不访问宿主SQLite入口。

### Gateway transform应用（源码已核）

- 顺序执行，输入body深拷贝、headers浅拷贝；抛异常时宿主跳过该变换。
- 只有返回对象才应用。直接改输入后返回undefined不生效；返回`{body: 新对象}`替换body。
- 宿主另支持headers按键合并（null删除）、有效routedModel覆盖及responseHeaders累积；本插件不用这些能力。
- 宿主异常跳过不保证请求已修好；不能把HTTP成功当作插件加载/净化成功证据。

源码证据来自`@musistudio/claude-code-router` 3.0.22 的 `dist/main/cli.js`。压缩bundle行号很长，定位用「行／全文件零基字符偏移」：配置标准化304／961500附近；module解析7903／1658676附近；setup/context7903／1648017和1653168附近；transform注册/应用7903／1645433与7904／1662696附近。偏移仅适用于当时核对的安装字节，不是跨发行构建的稳定API。没有复制bundle进仓库。

### 下一步真实验证（本轮未执行）

应在**单独准备并获授权的测试 CCR 实例**中进行：

1. 确定该版本的配置容器和configDir，用合成配置验证绝对module路径解析。
2. 只提供合成消息和本地测试上游，禁止复制生产凭据、配置或访问真实模型服务。
3. 验证不匹配provider不变、两个transform分别启用、返回body确被宿主采用，核对无正文日志。
4. 仅验证完成后标记实测版本，并补完整接入步骤。

撤销原则：移除测试实例中本插件的注册；保留原始配置的恢复点。是否必须重启由宿主版本决定。此工具集不执行这些步骤，不更改任何现用实例。

## Doctor 格式与资源上限

- 配置库：`app_config(key,value_json)`，`key='default'`，值为 JSON object；profile 为 `profile.profiles[]` 中唯一 `name='code'`。
- settings：JSON object 的 `env`；wrapper校验仅接受空行、注释、ENV_FIELDS中字段的独立字面量数字`export`。其它行（包括不相关命令、exec、多行条件、heredoc、函数、续行）使整项unsupported/FAIL。并非完整shell解析器，常见真实wrapper可能不受支持；不会为了通过检查而执行或修改它。
- usage：`usage_events(created_at,model,provider,status_code)`；时间由 SQLite `julianday` 解释，过去24小时，排除未来数据。模型精确匹配，provider 精确比较。
- JSON/wrapper 最大 2 MiB，源数据库最大 128 MiB，usage 最多 100000 行；超过上限报 FAIL，不静默抽样。
- 源数据库有 WAL/SHM/journal 即拒绝。未出现旁文件不保证外部写入永远不存在；仅支持安静输入，读取期修改/变化只做尽力检测，不是事务备份。
- 查询只在私有临时副本执行，`mode=ro&immutable=1`、`query_only=ON`、禁 trusted schema，并设置 VM 步数预算。拒绝视图、虚表与生成列；Python 支持 `setlimit` 时另限 SQLite 值长度为 2 MiB，旧 Python 仍可能先读取较大的普通存储值（上界为源库限制），不应拿它处理敌对数据库。
- 默认不读 usage、不联网、不执行外部程序。`--health` 禁代理、重定向，3 秒 socket 超时和 8192 字节回复上限；并非对慢速分段响应的严格总耗时保证。
- `--expected-version` 仅比对路径名/父目录名，不执行二进制，因此不是二进制版本认证。默认跳过。

权限检查只告警输入文件 group/world 访问位或不同 owner，不进行全目录 ACL/父路径信任审计，不修改权限。最终组件软链接拒绝；不能据此推断整个路径树已防篡改。
