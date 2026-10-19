# Deep Research Skill for Hermes Agent

> Adapted from [Weizhena/Deep-Research-skills](https://github.com/Weizhena/Deep-Research-skills) (MIT)

Structured multi-phase research workflow for **Hermes Agent**: outline → parallel deep dive → markdown report.

## Install

```bash
# Via Hermes CLI (when published to ClawHub)
hermes skills install deep-research

# Or manually — copy to skills directory
cp -r deep-research ~/.hermes/skills/research/
```

## Usage

```
User: "Research AI agent frameworks 2025"

Phase 1 — OUTLINE
  → outline.yaml + fields.yaml

User: "Run deep phase"  
Phase 2 — DEEP RESEARCH
  → delegate_task parallel subagents
  → results/*.json (validated)

User: "Generate report"
Phase 3 — REPORT  
  → report.md
```

## How it works

1. **Phase 1** — model knowledge + web search → structured outline with items and field definitions
2. **Phase 2** — `delegate_task` spawns parallel subagents (up to 3 at once), each researches an item, writes structured JSON, validates against field schema
3. **Phase 3** — all JSONs compiled into a comprehensive markdown report with table of contents


## Structure

```
deep-research/
├── SKILL.md                  # Full workflow (3 phases)
└── references/
    └── validate_json.py      # JSON field coverage validator
```

## Requirements

- Hermes Agent
- `pip install pyyaml`
- Web search configured

## License

MIT — based on [Weizhena/Deep-Research-skills](https://github.com/Weizhena/Deep-Research-skills)

## Credits

- Original concept: [RhinoInsight paper](https://arxiv.org/abs/2511.18743)
- Claude Code implementation: [Weizhena/Deep-Research-skills](https://github.com/Weizhena/Deep-Research-skills)
