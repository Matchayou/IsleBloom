<div align="center">
  <img src="docs/assets/logo.svg" width="88" alt="Research Island: a leaf growing from an open book" />
  <h1>Research Island · 研究小岛</h1>
  <p><strong>Let ideas grow into answers.</strong></p>
  <p>A local AI research workspace for branching exploration, specialist collaboration, and traceable deliverables.</p>
  <p><a href="README.md">简体中文</a> · <strong>English</strong></p>
  <p>
    <a href="#quick-start">Quick start</a> ·
    <a href="#a-look-inside">A look inside</a> ·
    <a href="#architecture">Architecture</a> ·
    <a href="#documentation">Documentation</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.12+" />
    <img src="https://img.shields.io/badge/React-19-244D3B?style=flat-square&amp;logo=react&amp;logoColor=white" alt="React 19" />
    <img src="https://img.shields.io/badge/PostgreSQL-persistent-4169E1?style=flat-square&amp;logo=postgresql&amp;logoColor=white" alt="PostgreSQL persistence" />
    <img src="https://img.shields.io/badge/DBOS-durable-52866B?style=flat-square" alt="DBOS durable execution" />
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-52866B?style=flat-square" alt="MIT License" /></a>
    <img src="https://img.shields.io/badge/status-early%20development-C49A49?style=flat-square" alt="Early development" />
  </p>
  <img src="docs/assets/hero.png" width="100%" alt="An open book becomes a floating island, with growing leaves and connected lights representing research and collaboration" />
</div>

## Turn a question into a research path you can revisit

Research Island brings sources, conversation branches, specialist tasks, and result versions into one workspace. Start with a question, explore alternatives in separate branches, delegate focused work to specialists, and return to conclusions with their sources and revision history.

It is built for work that involves revisiting material, comparing options, and preserving the research process. For developers, context assembly, execution, evaluation, and methods are separate modules. The current product is a **single-host local workspace in early development**.

| Capability | What you can do |
| :--- | :--- |
| **Branching research** | Fork from a selected message, freeze the inherited background, and explore directions independently. |
| **Plan before execution** | Generate a research plan, inspect inputs and dependencies, and confirm a specific revision before background execution. |
| **Specialist collaboration** | Delegate to temporary or persistent specialist identities and inspect tasks and handoffs in Flow. |
| **Automatic review and revision** | Have an independent reviewer inspect a fixed artifact, with up to two automatic revisions and retained versions. |
| **Context and memory** | Retrieve memory within user, project, and specialist scopes; load skills, tools, and authorized material on demand. |
| **Files and sources** | Inspect versioned resources, results, evidence, and delivered files; preview or download reports and HTML artifacts. |
| **Recovery and observability** | Resume confirmed steps, replay saved events, and compare duration, tool calls, and token usage. |
| **Methods and evaluation** | Manage versioned methods and isolated candidates, with frozen cases and human feedback. |

## A look inside

### Follow tasks and handoffs in one Flow

Projects and research tasks live on the left, conversations and collaboration in the center, and sources and results on the right. Select a node to inspect a task, or an edge to inspect the version and review advice behind a handoff.

![Research workspace and collaboration Flow](docs/assets/workspace.png)

### Every revision has a history

Execution and review agents have distinct roles. Revisions retain the original requirements, previous artifact, and review advice. Compare versions side by side and see unresolved issues when the revision limit is reached.

![Automatic review and side-by-side revisions](docs/assets/collaboration.png)

<details>
<summary><strong>Inspect run analysis: replay, failure location, and baseline comparison</strong></summary>

Run analysis reads saved ledger records without executing models or tools. Missing usage and duration remain unknown.

![Run analysis](docs/assets/run-analysis.png)

</details>

> These existing product acceptance screenshots use synthetic task data to demonstrate UI behavior. They are not research-quality or performance benchmarks. The README is bilingual; the application UI is primarily Chinese.

## From question to deliverable

1. **Create a project and research task**, then upload sources.
2. **Chat directly or generate a plan**; confirm the plan before execution.
3. **Explore branches and delegate specialists** with authorized sources and fixed result versions.
4. **Inspect reviews and revisions**, including handoff advice, sources, and unresolved issues.
5. **Preview files, review and adopt results**, and revisit execution through run analysis.

Execution completion, agent review, human approval, adoption, and publication are separate states. Automatic review does not replace formal human approval or automatically publish a result into the main conversation.

## Architecture

The workspace calls domain services through a local API. Chat, specialists, and background jobs share controlled execution and the OpenAI Agents SDK Runner. Dynamic context assembles authorized inputs; PostgreSQL stores business state and the execution ledger; DBOS coordinates durable background workflows; local files hold source blobs and private context artifacts.

![Current Research Island architecture](docs/assets/architecture.svg)

| Layer | Implementation | Entry points |
| :--- | :--- | :--- |
| Workspace | React 19, Vite, React Flow, animal-island-ui | [frontend/src/workspace](frontend/src/workspace) |
| API and domain | Starlette, Uvicorn; projects, research, tasks, collaboration | [app](src/agent_core/app), [research](src/agent_core/research), [collaboration](src/agent_core/collaboration) |
| Dynamic context | Authorized sources, history, memory, skills, and tools | [context](src/agent_core/context), [memories](src/agent_core/memories) |
| Controlled execution | Frozen requests and permissions, ledger, checkpoints, output validation | [controlled](src/agent_core/controlled), [OpenAI adapter](src/agent_core/adapters/openai.py) |
| Persistence and recovery | PostgreSQL, SQLAlchemy, DBOS, local artifacts | [postgres](src/agent_core/adapters/postgres), [durable](src/agent_core/durable.py) |
| Evaluation and improvement | Frozen cases, feedback, versioned methods, candidate validation | [evaluation](src/agent_core/evaluation), [evolution](src/agent_core/evolution), [evals](evals) |

**Execution model.** Each database permits one execution process. Recovery reuses confirmed steps; unconfirmed model usage or tool side effects remain unknown and require investigation. Coding tasks use fixed repository permissions and independent Git worktrees. Docker / OS sandbox isolation, multi-user accounts, invitations, and real-time co-editing are not implemented.

## Quick start

### 1. Prepare the environment

Install Python **3.12+**, [uv](https://docs.astral.sh/uv/), Node.js **22.12+** with npm, and a running PostgreSQL instance. Create a dedicated database whose user can create tables. Your model endpoint must support the OpenAI **Responses API**, with access to the selected models.

After downloading or cloning this repository, install the locked dependencies from its root:

```sh
uv sync --locked
npm ci --prefix frontend
npm run build --prefix frontend
```

### 2. Configure the database and models

Set these environment variables in the terminal that will start the service. Replace the placeholder values with your own configuration:

```sh
export DATABASE_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/research_island'
export OPENAI_API_KEY='YOUR_API_KEY'
export OPENAI_BASE_URL='https://YOUR_RESPONSES_ENDPOINT/v1'
export OPENAI_MODEL='YOUR_RESPONSES_MODEL'
export OPENAI_MEMORY_MODEL='YOUR_MEMORY_MODEL'
```

`OPENAI_MEMORY_MODEL` handles lightweight memory queries and defaults to `deepseek-flash` when unset. Explicitly select a model available at your endpoint. Inject credentials through your local environment and keep them out of Git. These commands do not automatically load an existing `.env` file.

### 3. Start the workspace

```sh
uv run agent-research \
  --blobs .stage4/blobs \
  --private .controlled/private \
  serve --durable --port 8744
```

Open **[http://127.0.0.1:8744](http://127.0.0.1:8744)** and create a project. The service initializes its tables; create the database first. First-time document parsing may download parsing models, depending on the file type and local environment.

Use the same database, blob directory, and private runtime directory when restarting. Back up all three together. See the [operations guide](docs/OPERATIONS.md) for budgets, recovery, and authorization rules.

### CLI entry points

| Command | Purpose |
| :--- | :--- |
| `agent-research` | Research, uploads, plan proposals, local UI/API |
| `agent-tasks` | Specialist tasks, execution, receipts, approval, adoption |
| `agent-methods` / `agent-evolution` | Versioned methods and improvement candidates |
| `agent-evals` | Human feedback, frozen cases, evaluation records |
| `agent-resources` | Resources, authorization, historical run queries |
| `agent-records` / `agent-dispatch` | Historical conversation and dispatch queries |

Run `uv run <command> --help` for available arguments.

## Development and validation

```sh
# Python unit and contract tests
uv run pytest

# Production frontend build
npm run build --prefix frontend

# Frontend development: proxies /api to the local service on port 8744
npm run dev --prefix frontend
```

PostgreSQL and live-model tests need additional environment configuration. Missing prerequisites may cause skips; skips are not passes. Browser acceptance uses Playwright with the corresponding fixture services; see [frontend](frontend). Frozen research-quality evaluation supports planning, execution, and reports through [evals/research_quality](evals/research_quality/README.md). Live model execution requires explicit `--live` and incurs provider charges.

## Documentation

Detailed engineering documents are currently primarily in Chinese.

| Topic | Read next |
| :--- | :--- |
| Runtime configuration, budgets, recovery, commands | [Operations guide](docs/OPERATIONS.md) |
| System responsibilities, request paths, source walkthrough | [System Wiki source](docs/wiki/project-systems-wiki.md) · [Offline interactive Wiki](docs/wiki/index.html) |
| Context assembly, skills, tool loading | [Dynamic context](docs/dynamic-context.md) · [Tool routing](docs/tool-routing-context.md) |
| Memory retrieval and writing | [Memory writing](docs/agent-memory-writing.md) · [Context layer 2](docs/agent-context-layer-2.md) |
| Plan confirmation and background execution | [Plan acceptance](docs/plan-mode-acceptance.md) |
| Automatic review, revision, handoffs | [Automatic collaboration](docs/automatic-collaboration.md) |
| Delivered files and run replay | [File delivery](docs/file-delivery-2026-10-13.md) · [Run analysis](docs/run-analysis.md) |
| Research quality and evidence scope | [Quality evaluation](evals/research_quality/README.md) · [Acceptance process](docs/development-acceptance-process.md) |

Download the interactive Wiki and open it in a browser. GitHub's file view does not execute its scripts. Embedded source reflects the Wiki's analysis snapshot.

## Contributing

Use Issues to describe a use case, reproduction steps, and expected behavior, or open a Pull Request focused on one problem. For research-quality changes, include task inputs, fixed result versions, and evaluation criteria. Distinguish mechanism validation from live model quality.

## Acknowledgments and license

README presentation takes inspiration from [Poirot](https://github.com/HezaoHezao/poirot). The brand illustration and architecture graphic were created for this project. See [pyproject.toml](pyproject.toml) and [frontend/package.json](frontend/package.json) for core dependencies.

This project is licensed under the [MIT License](LICENSE). Third-party code, dependencies, and reference materials retain their respective licenses and terms.

<div align="center">
  <br />
  <img src="docs/assets/logo.svg" width="40" alt="Research Island" />
  <p><sub>Leave a trace of every step of discovery.</sub></p>
</div>
