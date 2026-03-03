# ollama-agents-mcp

MCP server that scaffolds and runs a local Ollama "sub-agent" pipeline with three role prompts (`collector`, `writer`, `reviewer`) and `run_agents.sh`.

## What This MCP Actually Does

This MCP provides a repeatable local pipeline where the same local LLM is run multiple times with different roles, and each output is saved as a file.

Think of it as a small offline workflow engine:

1. Collector agent
- Input: messy notes, logs, metrics, tickets
- Output: normalized JSON plus short evidence notes
- Purpose: reduce invention and force structured extraction
2. Writer agent
- Input: only the collector JSON
- Output: polished report in Markdown
- Purpose: consistent report structure and faster drafting
3. Reviewer agent
- Input: JSON plus report
- Output: PASS/FAIL plus issues and required fixes
- Purpose: quality gate for contradictions, omissions, and vague claims

These are separate role runs with a shared workspace. They are not autonomous background workers.

## Pipeline Artifacts (What You Get Every Run)

Under `work/`, each run creates:

- `01_collector_<timestamp>_tryN.md`: raw collector output
- `02_data.json`: normalized source-of-truth data used by writer and reviewer
- `04_report_<timestamp>.md`: generated draft report
- `06_review_<timestamp>.md`: PASS/FAIL review and required fixes

Why this matters:

- if report text looks wrong, inspect `02_data.json` first
- if JSON is wrong, inspect collector output and input notes
- reviewer output tells you exactly what to fix before sharing

## Typical Operator Flow

1. Paste current month notes into `work/input.txt` (incidents, changes, metrics, risks, next plan)
2. Run setup once (or when role prompts/scripts change)
3. Run pipeline
4. Open `02_data.json`, `04_report_*.md`, and `06_review_*.md`
5. Apply reviewer-required fixes and re-run if needed

## Core Use Cases

1. Monthly or weekly ops reports
- Input: incidents, key metrics, change summary
- Output: normalized data JSON, final report, quality review
- Benefit: consistent month-over-month format with fewer manual errors
2. Post-incident and RCA packs
- Collector extracts timeline, impact, mitigation, and actions
- Writer drafts RCA document
- Reviewer checks missing root cause, owners, due dates, and unsupported claims
3. Change review and maintenance summaries
- Turn change notes and outcomes into a standard "what changed / risk / rollback / verification" artifact
4. Messy input to clean artifact conversion
- Examples: meeting notes to minutes, ticket dumps to executive summaries, log snippets to hypotheses and next checks
5. Offline or privacy-sensitive operations
- Keeps processing local; no cloud dependency for the pipeline itself

## Why Split Into Roles Instead Of One Prompt

Single large prompts often mix extraction and writing, miss sections, and drift in style over time.

Role separation gives:

- separation of concerns
- reusable monthly process
- audit trail (`02_data.json` as source of truth)
- quality gate (reviewer can block weak drafts)

## Non-Goals

- It does not auto-pull Grafana/Prometheus/Jira data unless you add separate scripts or API integrations.
- It does not run roles in parallel by default.
- It does not know your environment automatically; you still provide inputs.

## Quick Start In 60 Seconds

Prereqs:

- `ollama` installed and running
- `python3` available
- MCP server configured with env vars:
- `OLLAMA_AGENTS_MCP_STATE_DIR=<MCP_DATA_ROOT>/ollama-agents-mcp`
- `OLLAMA_AGENTS_BASE_DIR=<MCP_DATA_ROOT>/ollama-agents-mcp/workspace`

Then run:

1. `health_check()`
2. `setup_default_environment()`
3. `list_agent_roles()` (expect `collector`, `writer`, `reviewer`)
4. `run_default_pipeline()`

Expected outputs under `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace/work`:

- `01_collector_*.md`
- `02_data.json`
- `04_report_*.md`
- `06_review_*.md`

Optional hardening on run:

- `run_ollama_agents_pipeline(pipeline_input_file="work/input.txt", collector_retries=3, enforce_schema=true)`

## Intuitive Commands (Short Aliases)

Use these for day-to-day work:

- `setup_default_environment()`
- `run_default_pipeline()`
- `setup_and_run_default_pipeline()`

Use full commands only when overriding models/behavior:

- `setup_ollama_agents_environment(...)`
- `run_ollama_agents_pipeline(...)`

## Guided Inputs (Options + Defaults)

If you want selectable options with default-enter behavior:

1. `list_pipeline_run_options()`
- returns available `work/*` input files
- returns currently installed Ollama models from `ollama list`
- returns defaults used by guided run
2. `run_pipeline_guided(...)`
- leave fields blank to use defaults
- set only fields you care about (for example `collector_model`)

Example:

- `run_pipeline_guided()`
- `run_pipeline_guided(collector_model="deepseek-r1:latest")`
- `run_pipeline_guided(input_file="work/input.txt")`

## Path Placeholders

- `<MCP_STUFF_ROOT>`: parent MCP checkout root (example: `/Volumes/Data/_ai/_mcp/mcp_stuff`)
- `<MCP_DATA_ROOT>`: persistent MCP runtime data root (example: `/Volumes/Data/_ai/mcp-data`)

## What It Sets Up

Tool `setup_ollama_agents_environment` supports actions:

- `setup`: scaffold environment files
- `run`: run existing pipeline only
- `setup_and_run`: scaffold and then run

When setup is used, it creates:

- `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace/agents/collector.md`
- `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace/agents/writer.md`
- `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace/agents/reviewer.md`
- `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace/run_agents.sh`
- `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace/work/input.txt` (optional)

`run_agents.sh` executes the 3-stage flow:

1. Collector extracts structured JSON
2. Writer produces monthly report from JSON only
3. Reviewer validates report consistency against JSON

## Implemented MCP Tools

- `health_check`
- `setup_ollama_agents_environment`
- `setup_default_environment`
- `setup_and_run_default_pipeline`
- `list_pipeline_run_options`
- `run_ollama_agents_pipeline`
- `run_default_pipeline`
- `run_pipeline_guided`
- `run_role_agent`
- `list_agent_roles`
- `get_agent_role_prompt`
- `upsert_agent_role_prompt`
- `delete_agent_role_prompt`

## Data Root Policy

Runtime state for this MCP is persisted under:

- `<MCP_DATA_ROOT>/ollama-agents-mcp`

Configure with env var:

- `OLLAMA_AGENTS_MCP_STATE_DIR`
- `OLLAMA_AGENTS_BASE_DIR` (optional override for workspace path)

The server stores the latest action manifest in `last_action.json` in this state dir.

## Local Setup

```bash
cd <MCP_STUFF_ROOT>/ollama-agents-mcp
./bootstrap.sh
```

## Run

```bash
cd <MCP_STUFF_ROOT>/ollama-agents-mcp
./venv/bin/python run_server.py
```

## Codex Config Example

`~/.codex/config.toml`

```toml
[mcp_servers.ollama-agents-mcp]
command = "bash"
args = ["-lc", "mkdir -p <MCP_DATA_ROOT>/ollama-agents-mcp && cd <MCP_STUFF_ROOT>/ollama-agents-mcp && exec ./venv/bin/python run_server.py"]

[mcp_servers.ollama-agents-mcp.env]
OLLAMA_AGENTS_MCP_STATE_DIR = "<MCP_DATA_ROOT>/ollama-agents-mcp"
OLLAMA_AGENTS_BASE_DIR = "<MCP_DATA_ROOT>/ollama-agents-mcp/workspace"
```

## Claude Code Config Example

`~/.claude.json`

```json
{
  "mcpServers": {
    "ollama-agents-mcp": {
      "type": "stdio",
      "command": "bash",
      "args": [
        "-lc",
        "mkdir -p <MCP_DATA_ROOT>/ollama-agents-mcp && cd <MCP_STUFF_ROOT>/ollama-agents-mcp && exec ./venv/bin/python run_server.py"
      ],
      "env": {
        "OLLAMA_AGENTS_MCP_STATE_DIR": "<MCP_DATA_ROOT>/ollama-agents-mcp",
        "OLLAMA_AGENTS_BASE_DIR": "<MCP_DATA_ROOT>/ollama-agents-mcp/workspace"
      }
    }
  }
}
```

## Example Tool Usage

Fast path (recommended):

- `setup_default_environment()`
- `run_default_pipeline()`
- `setup_and_run_default_pipeline()`

Guided path (option listing + defaults):

- `list_pipeline_run_options()`
- `run_pipeline_guided()`

Create environment only (uses default workspace under `<MCP_DATA_ROOT>/ollama-agents-mcp/workspace`):

- `setup_ollama_agents_environment(action="setup")`

Run existing pipeline only:

- `setup_ollama_agents_environment(action="run", pipeline_input_file="work/input.txt")`

Create environment and pull models:

- `setup_ollama_agents_environment(action="setup", pull_models=true)`

Setup and run in one call:

- `setup_ollama_agents_environment(action="setup_and_run", pull_models=true, pipeline_input_file="work/input.txt")`

Run pipeline directly:

- `run_ollama_agents_pipeline(pipeline_input_file="work/input.txt")`
- `run_ollama_agents_pipeline(pipeline_input_file="work/input.txt", collector_retries=3, enforce_schema=true)`

Run a single role directly (works for future added roles too):

- `run_role_agent(role="collector", input_file="work/input.txt", model="deepseek-r1:latest")`

List current role prompts:

- `list_agent_roles()`

Read one role prompt:

- `get_agent_role_prompt(role="collector")`

Add a new role prompt (future expansion):

- `upsert_agent_role_prompt(role="analyst", prompt="ROLE: Analyst...")`

Delete a role prompt:

- `delete_agent_role_prompt(role="analyst", confirm=true)`

## Notes

- Requires local `python3` and `ollama`.
- Model pulls and pipeline execution can take several minutes depending on model size and hardware.
- Pipeline hardening includes collector retry and fallback JSON extraction when fenced blocks are missing.
- `enforce_schema=true` validates collector JSON contains keys: `incidents,changes,metrics,risks,next_month_plan`.
