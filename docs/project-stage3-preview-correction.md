# 项目页与 8771 基准对齐

2026-10-03

本次纠正项目页的交互回退，并处理两个运行服务共用静态构建目录的问题。本文记录本次修正，不代表第三阶段全部功能已完成验收。

## 页面

- 项目页直接列出多个主任务；每项可独立展开自己的 Flow，无需先进入额外的“打开主任务”页面。
- Flow 节点打开完整对话或专家任务，右侧保留资料与结果预览。
- 恢复侧栏搜索，可从当前主任务搜索并打开同项目其他主任务下的对话。
- 整理后的名称用于侧栏、Flow、对话标题和导航标题，不改变执行目标。
- 对话组件按对象身份重新挂载，避免切换专家或节点后残留上一对象的输入状态；Flow 刷新保留已有内容。

## 两个运行入口

- `http://127.0.0.1:8771/`：此前修复版的内存服务。PID 1904 未停止、未重启；已重新载入页面并打开原“来源 URL 与说明规则验证”对话。原进程中的数据仍保留。
- `http://127.0.0.1:8782/`：本次项目持久化预览，使用独立 PostgreSQL 库 `project3_verify` 和合成数据。它没有原 8771 内存会话。

两个服务原先都指向 `frontend/dist`，因此新构建会使 8771 刷新后加载不匹配的新前端。已从本次修改前保存的工具输出恢复原页面入口、Actions 和 RightPanel 的构建快照，恢复到原服务的构建目录；新预览改为明确使用 `/private/tmp/project3-preview-dist`。开发源码仍在 `frontend/src/workspace`，后续新功能继续从这里迭代。

8771 仍存活期间，项目预览使用：

```sh
npm run build:project-preview --prefix frontend
PYTHONPATH=src .venv/bin/python scripts/serve_project_preview.py
```

原服务仍指向 `frontend/dist`；普通 `npm run build --prefix frontend` 会覆盖其构建，不应用于这两个服务并存时的热更新。`/private/tmp` 构建和验收数据库属于本地开发资源，不是部署产物。

## 本次验证

- 新前端构建通过。
- 浏览器已核对：8771 刷新后打开原对话；8782 项目页直接展开两个独立 Flow；从第二主任务搜索并打开第一主任务的分支；重命名标题一致；右侧资料正文可读。
- `tests/test_projects.py`、`tests/test_resources.py`、`tests/test_research_web.py`：32 项在沙箱通过、21 项 PostgreSQL 场景跳过；唯一需绑定本地端口的测试在允许本地端口绑定后单独重跑通过。合计 33 项通过、21 项跳过。
- `frontend/tests/projects.spec.js` 已更新为多 Flow、延迟响应隔离及跨主任务搜索场景；本次修正后尚未重新运行这些 Playwright 自动化用例。上述浏览器交互由实际页面核对。
- 本次没有调用真实模型，没有重测模型回答质量。

页面截图：`project-stage3-layout-corrected.png`。
