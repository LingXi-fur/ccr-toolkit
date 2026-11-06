# 来源与第三方声明

## 本地修复的通用化

`plugins/tool-result-compat/index.mjs` 的媒体/二进制净化与未完成工具回合处理，提取自本地维护的 CCR app plugin；保留核心算法，重新实现 provider 显式选择、配置校验、按实例诊断与不含用户输入的限频日志。

`bin/ccr-doctor.sh` / `scripts/doctor.py` 的诊断思路来自本地只读巡检脚本。公开版改成参数化入口、静态默认、私有数据库副本、合成测试，不带原始环境。

本项目没有复制个人配置、历史请求、真实测试重放或生产日志。未定位到原插件测试源码，因此当前测试为新编写的合成用例；历史测试成绩不构成本版验证结果。

## Claude Code Router

接口参考项目：[musistudio/claude-code-router](https://github.com/musistudio/claude-code-router)。本地可核安装元数据为 `@musistudio/claude-code-router` 3.1.3（2026-11-06 起；此前为 3.0.22）。源码推导的插件事实核对自 3.0.22 安装字节，未随 3.1.3 复审。项目不复制或分发其安装 bundle，不是该项目官方发行版，也不代表上游背书。

核实到的上游许可如下，保留其署名；若后续纳入任何上游代码片段，必须继续核对具体文件和版本的许可。

```text
MIT License

Copyright (c) 2025 musistudio

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

原创代码与文档按根目录 MIT LICENSE 授权。当前使用集体署名 `CCR Toolkit contributors`；发布前需由项目所有者核定作者归属，不能仅凭本地文件位置断言全部代码权利。
