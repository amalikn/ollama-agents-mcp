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

- `~/ollama-agents/agents/collector.md`
- `~/ollama-agents/agents/writer.md`
- `~/ollama-agents/agents/reviewer.md`
- `~/ollama-agents/run_agents.sh`
- `~/ollama-agents/work/input.txt` (optional)

`run_agents.sh` executes the 3-stage flow:

1. Collector extracts structured JSON
2. Writer produces monthly report from JSON only
3. Reviewer validates report consistency against JSON

## Implemented MCP Tools

- `health_check`
- `setup_ollama_agents_environment`

## Data Root Policy

Runtime state for this MCP is persisted under:

- `<MCP_DATA_ROOT>/ollama-agents-mcp`

Configure with env var:

- `OLLAMA_AGENTS_MCP_STATE_DIR`

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
        "OLLAMA_AGENTS_MCP_STATE_DIR": "<MCP_DATA_ROOT>/ollama-agents-mcp"
      }
    }
  }
}
```

## Example Tool Usage

Create environment only:

- `setup_ollama_agents_environment(action="setup", base_dir="~/ollama-agents")`

Run existing pipeline only:

- `setup_ollama_agents_environment(action="run", base_dir="~/ollama-agents", pipeline_input_file="work/input.txt")`

Create environment and pull models:

- `setup_ollama_agents_environment(action="setup", base_dir="~/ollama-agents", pull_models=true)`

Setup and run in one call:

- `setup_ollama_agents_environment(action="setup_and_run", base_dir="~/ollama-agents", pull_models=true, pipeline_input_file="work/input.txt")`

## Notes

- Requires local `python3` and `ollama`.
- Model pulls and pipeline execution can take several minutes depending on model size and hardware.
