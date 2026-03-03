# ollama-agents-mcp

MCP server that scaffolds and runs a local Ollama "sub-agent" pipeline with three role prompts (`collector`, `writer`, `reviewer`) and `run_agents.sh`.

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
- `run_ollama_agents_pipeline`
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
