# Animal Island 研究工作台 UI 验收

日期：2026-10-11

## 实现

- 接入 animal-island-ui 2.1.1、naive-icons 1.5.3 和 classnames 2.5.1。
- 在前端入口仅导入一次 `animal-island-ui/style`；字体随本地构建托管。
- 统一项目导航、项目总览、流程图、主线聊天、专家讨论、资料阅览及操作弹窗。
- 按库 API 使用 Button、Input、Select、Checkbox、Card、Title 和 game Modal。
- 旧样式文件已整体替换；无旧主题兼容分支。API、研究状态模型和流式回复 Hook 未改变。
- Select 的菜单在可滚动弹窗内部排列，避免被 blob clip-path 裁切；保留组件自身颜色、圆角和阴影。
- 多行文本框和 React Flow 保留专用实现；应用层装饰与布局使用设计变量。

## 验证

- `npm run build --prefix frontend`：通过。构建有单个 JavaScript chunk 超过 500 kB 的体积提示。
- `.venv/bin/python -m pytest tests/test_workspace_api.py -q`：1 passed。
- 使用仓库 `scripts/verify_workspace_server.py` 的临时内存服务，所有写操作均使用合成测试资料，不调用真实模型。
- 浏览器实际验证：新建项目、新建主任务、打开主对话、提交消息并显示脚本回复、从助手消息创建分支、显示继承消息、选择文件并上传、显示资料预览、选择专家任务种类、勾选资料、创建派发专家、显示专家结果及执行详情。
- 键盘验证：下拉列表方向键与 Enter、复选框 Space、按钮 Enter。
- 桌面 1280 px、窄屏实际 569 px 均无页面横向溢出。窄屏默认折叠侧栏和资料栏；移动端选择导航后关闭侧栏。
- 最终测试页浏览器日志未记录 error / warn。
- `overview-desktop.png` 和 `overview.png` 为实际浏览器截图，内容为合成测试项目。

## 运行状态

验收前半段在原 `127.0.0.1:8744` 服务确认新版页面可呈现。最终刷新时，该后端已不再监听；未更改后端配置或研究数据。最终产物已写入 `frontend/dist`，原服务按现有 README 的原配置启动即可加载。
