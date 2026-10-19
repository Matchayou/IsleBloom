# 项目系统 Wiki

直接用浏览器打开 `index.html`。页面包含完整讲解、50 张表格、19 个系统请求流程、分层架构、全局调用时序与交付状态图；搜索可按系统名、函数和状态定位章节。160 个流程/架构/时序/状态节点均可点击，展示对应源码函数、节点输入输出、链路作用和相关章节入口；系统流程详情还能沿上一步/下一步浏览。点击源码函数可查看分析时的本地源码快照与定义行。

页面中的样式、脚本、流程图和源码快照均已内嵌，不需要网络或后端。Markdown 原稿与源码指纹链接指向同目录文件；打印按钮展开正文后调用浏览器打印，可保存为 PDF。

## 维护

- `project-systems-wiki.md`：完整技术内容。
- `system-views.json`：各系统职责、请求步骤和关键函数表。
- `flow-nodes.json`：逐节点的源码映射、输入输出、职责与相关系统。
- `source-snapshot.json`：固定分析源码快照，构建器校验已有源码指纹并从快照 AST 提取函数签名和定义行。
- `wiki.css` / `wiki.js`：页面样式与本地交互。
- `build_wiki.mjs`：复用项目已有 React、React Markdown 和 GFM 依赖，生成静态 HTML；运行时不依赖 React。
- `source-manifest.json` / `check_wiki.py`：原始分析源码指纹与函数定位检查。

在项目根目录执行：

```sh
node docs/wiki/build_wiki.mjs
```

`python3 docs/wiki/check_wiki.py` 另外检查当前工作区相对分析的漂移；检测出源码变化时，旧 Wiki 仍对应其固定快照，不会自动混入新实现。

重建依赖已安装的 `frontend/node_modules` 和 Python 3。业务源码变更后先更新分析与指纹，不能只重建页面而保留过时说明。更新分析时须同时更新原稿、节点说明、源码快照与指纹。网页中的源码是分析时快照，并非浏览器实时读取工作区。
