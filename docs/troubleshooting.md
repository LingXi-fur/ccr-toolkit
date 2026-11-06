# 排错与安全复现

## 工具媒体导致请求异常

症状可能是少量截图后 token 数异常、超上下文或网关请求被拒绝。先确认该上游是否将 `tool_result` 媒体错误编码为文本；不要把任何 400 都归因于图片，也不要用本工具隐蔽更换模型。

修复策略：仅为确认受影响的 provider 启用 `strip-tool-images`。代价是工具结果媒体丢失；如果任务需要视觉内容，应使用正确支持多模态工具结果的上游，而不是删图后声称能力完整。

## 并行工具中间状态被拒绝

症状可能是上游要求每条 function call 都有对应 output，而客户端此次只回传部分结果。`fix-partial-tool-rounds` 按请求历史的 ID 配对，临时略去未完成调用。并不修复所有消息次序问题；先用合成消息复现，再在隔离测试实例验证。

## Doctor FAIL / SKIP

| 检查 | 常见原因 | 下一步 |
| --- | --- | --- |
| `config` FAIL | 缺文件、损坏、schema不同、超大小、活跃旁文件 | 在仓库外准备安静的一致备份，核对版本/结构；不要删生产 WAL |

网关运行时（CCR ≥3.0.22 实测 3.1.3）`config.sqlite` 以 WAL 模式持有常开连接，`-wal`/`-shm` 旁文件常驻属宿主正常运行状态，不是损坏；此时 doctor 按设计拒绝读取。要跑 config 检查：先 `ccr stop`（或用既有安静备份指向 `--ccr-dir`），检查完再启动——doctor 自己不做 stop/start。
| `settings` FAIL | 非 JSON object、软链接、文件过大 | 本地核对输入，禁止直接上传原件 |
| `env-drift` FAIL | 三处静态字段不同、重复 profile、动态 wrapper | 手动核对少数字段；工具不自动改配置 |
| `routing` FAIL | usage schema不支持、活跃数据库或查询超预算 | 使用受支持、安静的独立备份，不裁剪原库 |
| `routing` WARN | 无匹配数据、记录provider不符、5xx | 只是一条线索，不能证明实际执行模型 |
| `health` SKIP | 默认不联网 | 需要时显式 `--health`；默认跳过正常 |
| `health-*` FAIL | 无监听、状态不符、超时/超限 | 自行核对服务；工具不 restart/kill |
| `permissions` WARN | 输入可被组/其他用户访问，或owner不同 | 按本机政策手动核查；不在此工具自动 chmod |

## Issue 最小信息

```text
工具版本：
系统 / Node / Python：
使用的功能：
是否合成输入：是
期望：
实际：
退出码：
脱敏后的 PASS/WARN/FAIL/SKIP 汇总：
```

不上传凭据、原数据库、真实模型请求、会话、完整日志/环境或个人路径。用 `example-provider/example-model` 替换实际路由；敏感漏洞按 SECURITY.md 联系私密渠道。

## 本地发布门禁

```bash
bash scripts/test.sh
python3 -I scripts/check_public_content.py
```

扫描只输出路径、行号、规则；不回显命中值。未发现问题不代表没有秘密，仍须人工审查最终文件、许可证与 Git 历史。

维护者必须分开描述：离线算法验证、真实宿主加载验证、独立审查、远端 CI。任一未做就明确写未做，不能合并成“全部验证通过”。
