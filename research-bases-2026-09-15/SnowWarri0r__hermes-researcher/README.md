# Hermes Researcher

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js](https://img.shields.io/badge/Node.js-%3E%3D18-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.8-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v4-06B6D4?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![GitHub issues](https://img.shields.io/github/issues/SnowWarri0r/hermes-researcher)](https://github.com/SnowWarri0r/hermes-researcher/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/SnowWarri0r/hermes-researcher)](https://github.com/SnowWarri0r/hermes-researcher/commits/main)
[![GitHub stars](https://img.shields.io/github/stars/SnowWarri0r/hermes-researcher?style=social)](https://github.com/SnowWarri0r/hermes-researcher/stargazers)

Autonomous deep-research platform powered by [Hermes Agent](https://github.com/NousResearch/hermes-agent). Multi-phase pipeline with parallel research, self-critique, iterative refinement, and real-time streaming.

```
User goal
  |  Plan               -- structured research plan (JSON)
  |  Research x N       -- parallel investigation threads
  |  [Adequacy gate]    -- evaluate coverage, spawn supplementary research if gaps found
  |  Draft              -- synthesize findings into report
  |  Critique           -- strict self-review
  |  Revise             -- final report incorporating critique
  v  [Quality gate]     -- score 1-10, iterate critique→revise if <7 (max 2 rounds)
```

## Features

**Multi-phase pipeline** -- Tasks run through Plan, parallel Research, Draft, Critique, and Revise stages. Each phase is a separate Hermes agent invocation with a tailored prompt.

**Three modes** -- Quick (1 call, direct report), Standard (plan + research + draft), Deep (full pipeline with self-critique + quality gates).

**Real-time streaming** -- All phases stream live -- report text during write/draft/revise, and plan/critique output in the pipeline view with auto-expand and blinking cursor. Incomplete markdown syntax (`**`, `` ` ``, `[`) is sanitized mid-stream.

**Pipeline visualization** -- Sidebar shows each phase's status, tool calls, token usage, and duration. Parallel research branches display in a grid with completion counts.

**Smart retry** -- Failed tasks retry from the point of failure, reusing completed phases. If 3 of 5 research branches finished, only the failed 2 re-run. Cached phases are marked in the pipeline view.

**Iterative refinement** -- Follow-up requests re-run the full pipeline against the prior report. Version tabs let you browse v1, v2, v3... and a line-level diff view highlights changes.

**Token-efficient chaining** -- Critique and revise phases receive prior outputs via conversation history instead of re-embedding full text, saving ~8000+ tokens per deep pipeline run.

**Persistent storage** -- All tasks, turns, phases, and events stored in SQLite (`~/.hermes-researcher/tasks.db`). Survives browser refreshes, device switches, server restarts.

**Search, filter, tags, pin** -- Keyword search, status filter (All/Running/Done/Failed), `#tag` labels, star to pin important research.

**Language preference** -- Auto, Chinese, English, Japanese. Injected into every prompt's style guide.

**Export** -- Copy Markdown to clipboard or download as `.md` file.

**Desktop notifications** -- Browser notification when a pipeline completes or fails (only fires when the page is not focused).

**Knowledge base** -- Research findings are automatically extracted and stored with vector embeddings. Subsequent tasks recall relevant prior knowledge via hybrid retrieval (vector ANN + keyword FTS5 + Reciprocal Rank Fusion + LLM reranking).

**Task chains** -- Link tasks so completing one auto-triggers the next, passing the report as context.

**Task templates** -- Save reusable task templates with typed variables (text, select, number) for quick dispatch.

**Scheduled tasks** -- Cron-based scheduler for recurring research (daily digests, weekly reports). Goal templates support date variables (`{date}`, `{yesterday}`, `{weekStart}`, etc.) that auto-substitute at execution time.

**Discord delivery** -- Completed reports automatically pushed to Discord channels via webhook. Summary embed with TL;DR + metadata, full report as `.md` file attachment. Failure notifications included.

**Research quality gates** -- (Deep mode) After research, LLM evaluates if findings cover the plan — spawns supplementary branches if gaps found. After revise, scores the report 1-10 — iterates critique→revise if score < 7 (max 2 rounds).

**Configurable pipeline** -- Settings UI for per-phase model routing, parallel research concurrency, Hermes gateway limits, and embedding provider.

**LAN accessible** -- Both frontend and middleware bind `0.0.0.0` by default.

## Architecture

```
Browser (React + Vite)
    |  /api/*
Middleware (Hono + SQLite)       <-- orchestrates pipeline, persists state
    |  /v1/runs + SSE
Hermes Gateway (port 8642)       <-- the actual AI agent
```

The middleware is the brain. It:
- Breaks tasks into pipeline phases with quality gates
- Starts Hermes runs for each phase, using `conversation_history` to chain context
- Subscribes to Hermes SSE events, persists them, broadcasts to frontend
- Runs parallel research branches via `Promise.all` (configurable concurrency)
- Evaluates research adequacy and report quality, self-corrects when needed
- Compresses prior reports before injecting into follow-up prompts (>6k chars)
- Caches completed phases for smart retry on failure
- Runs cron schedules and delivers results to Discord

The frontend is stateless -- it fetches everything from the middleware API.

## Quick Start

### Prerequisites

- **Node.js** >= 18
- **pnpm**
- **Hermes Agent** installed and configured ([setup guide](https://github.com/NousResearch/hermes-agent))
- Hermes gateway running with API server enabled

### 1. Configure Hermes

Add to `~/.hermes/.env`:

```bash
API_SERVER_KEY=your-secret-key
API_SERVER_ENABLED=true
API_SERVER_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Start the gateway:

```bash
hermes gateway start
```

### 2. Install & Run

```bash
git clone https://github.com/SnowWarri0r/hermes-researcher.git
cd hermes-researcher

# Install frontend + server deps
pnpm install
cd server && pnpm install && cd ..

# Start both (frontend on :5173, middleware on :8787)
HERMES_API_KEY=your-secret-key pnpm dev
```

Open `http://localhost:5173`.

### Production

```bash
pnpm build
HERMES_API_KEY=your-secret-key pnpm start
# Single server on :8787 serving API + static frontend
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `HERMES_API_KEY` | *(required)* | Bearer token matching `API_SERVER_KEY` in Hermes |
| `HERMES_ENDPOINT` | `http://127.0.0.1:8642` | Hermes gateway URL |
| `PORT` | `8787` | Middleware listen port |
| `HOST` | `0.0.0.0` | Middleware bind address |
| `HERMES_RESEARCHER_DB` | `~/.hermes-researcher/tasks.db` | SQLite database path |
| `EMBEDDING_ENDPOINT` | *(same as HERMES_ENDPOINT)* | OpenAI-compatible embedding API base URL |
| `EMBEDDING_API_KEY` | *(same as HERMES_API_KEY)* | API key for embedding endpoint |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model ID |

> **Knowledge base**: When `EMBEDDING_ENDPOINT` + `EMBEDDING_API_KEY` are set, knowledge entries are stored with vector embeddings and recalled via cosine similarity. Without them, FTS5 keyword search with LLM query expansion is used as fallback.

## Project Structure

```
hermes-researcher/
  src/                     # React frontend
    api/client.ts          # API client + SSE subscriber
    store/tasks.ts         # Zustand store
    components/
      tasks/
        TaskCreator.tsx    # New task form (mode, language, toolsets)
        TaskList.tsx       # Search + filter + task cards
        TaskCard.tsx       # Card with progress bar, pin, status
        TaskDetail.tsx     # Slide-out panel with report + pipeline
        PipelineView.tsx   # Phase visualization with expand/collapse
        ReportDiff.tsx     # Line-level version diff
  server/
    src/
      index.ts             # Hono routes + SSE endpoint
      db.ts                # SQLite schema + queries
      runner.ts            # Pipeline orchestrator (plan/research/draft/critique/revise)
      hermes.ts            # Hermes API client + SSE consumer
      prompt.ts            # All prompt templates per phase
      settings.ts          # Settings persistence + Hermes gateway config
      retrieval.ts         # Hybrid retrieval (vector + FTS5 + RRF + LLM rerank)
      knowledge.ts         # Knowledge extraction and dedup
      embedding.ts         # Multi-provider embedding (OpenAI/Volcengine/Ollama)
      scheduler.ts         # Cron-based task scheduler with date variables
      discord.ts           # Discord webhook delivery (embed + .md attachment)
  shared/
    types.ts               # Shared TypeScript types
```

## Tech Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, Vite, Tailwind CSS v4, Zustand, react-router, react-markdown |
| Middleware | Hono, better-sqlite3, sqlite-vec, node-cron, Node.js |
| Agent | Hermes Agent v0.9+ (any LLM provider it supports) |
| Knowledge | sqlite-vec ANN + FTS5 BM25 + RRF fusion + LLM reranking |
| Embedding | OpenAI / Volcengine Doubao / Ollama (configurable) |
| Design | VoltAgent dark theme (Abyss Black + Emerald Signal Green) |

## How the Pipeline Works

### Plan Phase
The planner decomposes the goal into 3-7 report sections and 3-6 focused research questions. Output is structured JSON.

### Research Phase (parallel)
Each question becomes an independent Hermes run. They execute concurrently (configurable, default 5). Each produces a raw findings document with citations.

### Research Adequacy Gate (deep mode)
After all branches complete, an LLM evaluates whether findings adequately cover the plan. If gaps are found, up to 3 supplementary research branches are spawned automatically.

### Draft Phase
Synthesizes the plan + all findings into a complete Markdown report.

### Critique Phase
Acts as a strict peer reviewer via streaming chat completions. Receives the draft through conversation history (no re-embedding), producing a prioritized list of content gaps, weak claims, structural issues, and missing citations.

### Revise Phase
Rewrites the report incorporating the critique. Receives both draft and critique via `conversation_history` to avoid redundant token usage.

### Quality Gate (deep mode)
After revision, an LLM scores the report 1-10. If the score is below 7, the pipeline runs another critique→revise cycle with the specific issues fed back into the critique prompt. Maximum 2 quality iterations to prevent infinite loops.

### Follow-up
Requesting a refinement re-runs the full pipeline with the prior report condensed into the plan prompt. The new version integrates changes naturally without meta-commentary about what changed.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=SnowWarri0r/hermes-researcher&type=Date)](https://star-history.com/#SnowWarri0r/hermes-researcher&Date)

## License

MIT
