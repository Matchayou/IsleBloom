# 旅行任务文件交付验收

现有手册已接入网页“资料与结果 → 交付文件”，点击 handbook.html 可直接预览、下载或在新窗口打开。该文件来自已发布结果 result:8d2257e8-4115-4a6a-b9dd-ecca0786a93a，下载字节与原验收 HTML 完全一致，SHA-256 为 fb1cf284314f27c3eb63e8cc177e6f9f4e83d3986161dfb1c133721d3a496b12。

## 实现

- 主助手和研究计划步骤通过 ResearchAnswer.files 交付文件名及完整 UTF-8 内容。支持 HTML、Markdown、文本、CSV、JSON、SVG、CSS、JavaScript；HTML 使用内联样式/脚本或绝对 HTTPS 外部依赖。
- 文件内容与报告一起原子保存到既有不可变运行结果。文件目录和下载字节从已发布结果读取，不维护第二份容易脱节的文件状态。
- 完整报告内的完整 HTML 文档也会成为独立文件条目。原旅行报告没有改写，无需手工导入本地验收文件。
- 文件名、完整 HTML、重复文件名在结果校验阶段检查。失败/未发布的草稿不进入文件列表；格式纠错必须逐字保留完整交付文件。
- 项目文件面板及旅行任务面板均显示交付文件，标注产生它的节点和结果版本。新结果发布后任务文件列表自动刷新。
- 预览/下载重新验证项目归属、来源权限和已保存结果；不接收本地磁盘路径。HTML 使用 opaque-origin sandbox，仅允许脚本，响应 CSP 限制资源与网络加载；下载保持原文件字节。

## 验证

- 相关 Python 测试通过：文件名与完整性校验、失败草稿不交付、版本不覆盖、跨项目访问拒绝、撤回后预览/下载拒绝，以及 HTML/CSV 的格式纠错保留。
- 前端生产构建成功。
- 内置浏览器实际打开原手册预览，显示七天表格与 12 个地图标记；预算输入 1000 后实时合计为 1000。通过“下载文件”保存到 Downloads/handbook.html，其哈希与已发布结果一致。
- 在原旅行项目创建明确标记测试数据的验证分支，真实模型生成 travel-delivery-check.html 与 budget-check.csv。最终运行 a795df0b-c259-40ff-b080-f080c516cdcb 成功，两个文件自动出现在网页列表。HTML 输入 200 并点击计算后显示 400。
- 重启 8744 服务后再次读取所有三个文件，文件标识、内容哈希、预览/下载字节均保持一致。最后运行服务 PID 26213。

初次新文件验证发现：JSON 代码围栏里的 HTML 字符串尚未解码，保留校验误将转义后的字符串与实际 HTML 比较。已修复并加入 SDK 回归测试；失败运行保留，没有从失败草稿发布文件。

验收元数据：travel-retry-2026-10-13/final/file-delivery.json、future-delivery-verification.json、file-delivery-restart-verification.json。

范围：本次交付主助手/研究节点生成的 UTF-8 文本文件；没有增加 PDF、图片等二进制文件生成能力，也没有实施本地文件存储替换数据库的改造。

预览模式参照 MDN iframe 与 Content-Disposition 文档：
- https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Disposition
