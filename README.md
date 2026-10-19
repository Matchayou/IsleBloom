<div align="center">
  <img src="docs/assets/logo.svg" width="88" alt="研究小岛标识：书页上生长的叶子" />
  <h1>研究小岛 · Research Island</h1>
  <p><strong>让想法，慢慢长成答案。</strong></p>
  <p>一个支持分支探索、专家协作与可追溯交付的本地 AI 研究工作台。</p>
  <p><strong>简体中文</strong> · <a href="README.en.md">English</a></p>
  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="#产品一览">产品一览</a> ·
    <a href="#系统架构">系统架构</a> ·
    <a href="#文档导航">文档导航</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.12+" />
    <img src="https://img.shields.io/badge/React-19-244D3B?style=flat-square&amp;logo=react&amp;logoColor=white" alt="React 19" />
    <img src="https://img.shields.io/badge/PostgreSQL-persistent-4169E1?style=flat-square&amp;logo=postgresql&amp;logoColor=white" alt="PostgreSQL 持久化" />
    <img src="https://img.shields.io/badge/DBOS-durable-52866B?style=flat-square" alt="DBOS 持久执行" />
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-52866B?style=flat-square" alt="MIT 许可证" /></a>
    <img src="https://img.shields.io/badge/status-early%20development-C49A49?style=flat-square" alt="早期开发阶段" />
  </p>
  <img src="docs/assets/hero.png" width="100%" alt="研究小岛品牌插画：开放书页化作浮岛，叶子与相连的光点代表研究生长与专家协作" />
</div>

## 把一个问题，变成一条可回看的研究路径

研究小岛把资料、对话分支、专家任务和交付版本放进同一个工作空间。你可以从主线提出问题，在分叉处探索不同方向，把明确的任务交给专家，再带着来源与修订记录回到结论。

它面向需要反复查阅材料、比较方案、保留研究过程的人，也为开发者提供独立的上下文、执行、评测与方法模块。当前是**单宿主的本地研究工作台**，处于早期开发阶段。

| 能力 | 你可以做什么 |
| :--- | :--- |
| **分支研究** | 从指定消息创建独立对话，固定分叉时的背景，分别探索不同思路。 |
| **先规划，再执行** | 生成研究计划，查看输入与依赖，确认具体版本后进入后台执行。 |
| **专家协作** | 委派临时 Agent 或固定身份的专精 Agent，在 Flow 中查看任务与交接。 |
| **自动评审与修订** | 独立评审 Agent 检查固定产物，按建议最多自动修订两轮，保留各版结果。 |
| **上下文与记忆** | 按用户、项目和专家范围检索记忆，按需加载技能、工具及授权资料。 |
| **文件与来源** | 查看版本化资料、结果、证据及交付文件，预览或下载生成的报告与 HTML 等产物。 |
| **恢复与观测** | 从已确认步骤恢复；回放已保存事件，对比运行耗时、工具调用和 token 用量。 |
| **方法与评测** | 管理版本化方法、隔离改进候选，用冻结案例与人工反馈评估结果。 |

## 产品一览

### 一张 Flow，串起任务与交接

左侧组织项目与研究主任务，中间查看对话和专家协作，右侧查阅资料与结果。点击 Flow 节点查看任务，点击交接边查看对应版本及评审建议。

![研究工作台与协作 Flow](docs/assets/workspace.png)

### 每次修订，都有来处

执行 Agent 与评审 Agent 分工协作。修订保留原始需求、上一版产物和修改建议，支持并排比较结果；达到修订上限后展示剩余问题。

![专家自动评审与修订版本对比](docs/assets/collaboration.png)

<details>
<summary><strong>查看运行分析：事件回放、失败定位与基线对比</strong></summary>

运行分析读取保存的账本，不重新执行模型或工具。缺失用量与耗时保留为未知。

![运行分析](docs/assets/run-analysis.png)

</details>

> 以上为项目现有页面的验收截图，使用合成任务数据展示界面行为；不代表真实研究质量或性能基准。README 支持中英文切换，产品界面当前以中文为主。

## 从问题到交付

1. **创建项目与主任务**，上传研究资料。
2. **直接对话或生成计划**；计划经确认后执行。
3. **分支探索与委派专家**，按任务挂载获准资料与固定结果版本。
4. **查看评审与修订**，检查交接建议、来源及未解决问题。
5. **预览交付文件、审核并采用结果**，通过运行分析回看执行过程。

执行完成、Agent 评审通过、人工审核、采用和发布分别记录。自动评审不会替代正式人工审核，也不会自动把结果发布到主线。

## 系统架构

工作台通过本机 API 进入领域服务；聊天、专家与后台任务共用受控执行服务和 OpenAI Agents SDK Runner。动态上下文负责组装获准输入，PostgreSQL 保存业务事实与执行账本，DBOS 协调持久化后台工作流，本地文件保存资料与私有上下文工件。

![研究小岛当前系统架构](docs/assets/architecture.svg)

| 层次 | 当前实现 | 代码入口 |
| :--- | :--- | :--- |
| 工作台 | React 19、Vite、React Flow、animal-island-ui | [frontend/src/workspace](frontend/src/workspace) |
| API 与业务 | Starlette、Uvicorn；项目、研究、任务与协作服务 | [app](src/agent_core/app)、[research](src/agent_core/research)、[collaboration](src/agent_core/collaboration) |
| 动态上下文 | 授权资料、历史、记忆、技能与工具按需组装 | [context](src/agent_core/context)、[memories](src/agent_core/memories) |
| 受控执行 | 请求与权限快照、调用账本、checkpoint、输出校验 | [controlled](src/agent_core/controlled)、[OpenAI 适配器](src/agent_core/adapters/openai.py) |
| 持久化与恢复 | PostgreSQL、SQLAlchemy、DBOS、本地资料工件 | [postgres](src/agent_core/adapters/postgres)、[durable](src/agent_core/durable.py) |
| 评测与改进 | 固定案例、反馈、版本化方法与候选验证 | [evaluation](src/agent_core/evaluation)、[evolution](src/agent_core/evolution)、[evals](evals) |

**执行约定。** 同一个数据库只允许一个执行进程；恢复复用已确认步骤，遇到未确认的模型用量或工具副作用时保留未知并等待核查。编码任务使用固定仓库权限与独立 Git worktree；当前不提供 Docker / OS 级沙箱。多人账号、成员邀请和实时共同编辑尚未实现。

## 快速开始

### 1. 准备环境

需要 Python **3.12+**、[uv](https://docs.astral.sh/uv/)、Node.js **22.12+** 与 npm，以及一个已启动、当前用户可创建表的独立 PostgreSQL 数据库。模型端点需支持 OpenAI **Responses API**，并具备所用模型的访问权限。

下载或克隆本仓库后，在项目根目录安装锁定依赖：

```sh
uv sync --locked
npm ci --prefix frontend
npm run build --prefix frontend
```

### 2. 配置数据库与模型

在启动服务的同一个终端设置环境变量，将下面的占位值替换为自己的配置：

```sh
export DATABASE_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/research_island'
export OPENAI_API_KEY='YOUR_API_KEY'
export OPENAI_BASE_URL='https://YOUR_RESPONSES_ENDPOINT/v1'
export OPENAI_MODEL='YOUR_RESPONSES_MODEL'
export OPENAI_MEMORY_MODEL='YOUR_MEMORY_MODEL'
```

`OPENAI_MEMORY_MODEL` 用于轻量记忆查询，未设置时默认为 `deepseek-flash`；请显式选择端点可用的模型。密钥通过本地环境注入，不要提交到 Git。现有 `.env` 文件不会由上述命令自动加载。

### 3. 启动工作台

```sh
uv run agent-research \
  --blobs .stage4/blobs \
  --private .controlled/private \
  serve --durable --port 8744
```

打开 **[http://127.0.0.1:8744](http://127.0.0.1:8744)**，创建项目并开始研究。数据库表由服务初始化；数据库须事先创建。首次解析资料可能需要下载解析模型，具体行为取决于文件类型及本机环境。

重启时沿用同一个数据库、资料目录和私有运行目录。备份时三者需要一同保存。详细预算、恢复和权限规则见[操作说明](docs/OPERATIONS.md)。

### 命令行入口

| 命令 | 用途 |
| :--- | :--- |
| `agent-research` | 研究、资料上传、计划提案与本机 UI/API |
| `agent-tasks` | 专家任务、执行、结果回执、审核与采用 |
| `agent-methods` / `agent-evolution` | 版本化方法与改进候选 |
| `agent-evals` | 人工反馈、冻结案例与评测记录 |
| `agent-resources` | 资料、授权与历史运行查询 |
| `agent-records` / `agent-dispatch` | 历史会话与调度记录查询 |

执行 `uv run <命令> --help` 查看参数。

## 开发与验证

```sh
# Python 单元与契约测试
uv run pytest

# 前端生产构建
npm run build --prefix frontend

# 开发界面：/api 代理到本机 8744 服务
npm run dev --prefix frontend
```

需要 PostgreSQL 或真实模型的测试有额外环境要求，缺失环境时可能跳过，不能把跳过计为通过。浏览器验收使用 Playwright 与对应夹具服务，配置见 [frontend](frontend)。固定研究质量评测的计划、执行与报告入口见 [evals/research_quality](evals/research_quality/README.md)；真实模型调用须显式启用 `--live`，会产生供应商费用。

## 文档导航

| 想了解什么 | 从这里开始 |
| :--- | :--- |
| 运行配置、预算、恢复与命令细节 | [操作说明](docs/OPERATIONS.md) |
| 系统职责、请求链路与源码导读 | [系统 Wiki 原稿](docs/wiki/project-systems-wiki.md) · [离线交互 Wiki](docs/wiki/index.html) |
| 上下文如何装配、技能如何加载 | [动态上下文](docs/dynamic-context.md) · [工具路由](docs/tool-routing-context.md) |
| 记忆如何检索与写入 | [记忆写入](docs/agent-memory-writing.md) · [上下文第二层](docs/agent-context-layer-2.md) |
| 计划确认与后台执行 | [Plan 模式验收](docs/plan-mode-acceptance.md) |
| 自动评审、修订与交接 | [自动协作](docs/automatic-collaboration.md) |
| 文件交付与运行回放 | [交付文件](docs/file-delivery-2026-10-13.md) · [运行分析](docs/run-analysis.md) |
| 研究质量与证据范围 | [质量评测](evals/research_quality/README.md) · [验收流程](docs/development-acceptance-process.md) |

离线交互 Wiki 请下载后用浏览器打开；GitHub 文件页不执行其中的交互脚本。Wiki 中的源码对应其分析快照。

## 参与改进

欢迎通过 Issue 描述使用场景、复现步骤与预期行为，或提交聚焦单个问题的 Pull Request。涉及研究质量时，请附任务输入、固定结果版本与评判依据，区分产品机制验证和真实模型效果。

## 致谢与许可

本项目采用 [MIT 许可证](LICENSE)。第三方代码、依赖和参考材料保留各自的许可与使用条件。

<div align="center">
  <br />
  <img src="docs/assets/logo.svg" width="40" alt="Research Island" />
  <p><sub>让每一步探索，都留下足迹。</sub></p>
</div>
