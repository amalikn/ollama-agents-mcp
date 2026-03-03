#!/usr/bin/env python3
"""MCP server for setting up local Ollama-based sub-agent environments."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ollama-agents-mcp")

DEFAULT_STATE_DIR = "/Volumes/Data/_ai/mcp-data/ollama-agents-mcp"
DEFAULT_BASE_DIR = "~/ollama-agents"
ALLOWED_ACTIONS = {"setup", "run", "setup_and_run"}

COLLECTOR_PROMPT = """ROLE: Collector
GOAL: Extract and normalize inputs into structured JSON + short evidence notes.
RULES:
- If data is missing, list it explicitly as missing. Do not invent numbers.
- Output TWO sections:
  1) JSON block (strict JSON)
  2) Evidence notes (bullets, file names/commands referenced)
"""

WRITER_PROMPT = """ROLE: Writer
GOAL: Produce a polished monthly ops report using the provided JSON.
RULES:
- Do not invent facts. If missing, add a "Data gaps" section.
- Use the following sections:
  1) Executive summary (5 bullets)
  2) Availability & incidents
  3) Changes & releases
  4) Capacity/perf highlights
  5) Risks & mitigations
  6) Next month plan
- Keep it concise and operational.
"""

REVIEWER_PROMPT = """ROLE: Reviewer
GOAL: Validate the report for correctness, completeness, and internal consistency.
RULES:
- Check: numbers match JSON, claims supported, missing sections, vague language.
- Output:
  - PASS/FAIL
  - Issues (bullets)
  - Required fixes (bullets)
"""

RUN_AGENTS_SH = """#!/usr/bin/env bash
set -euo pipefail

# ---- models (override via env vars) ----
MODEL_COLLECTOR="${MODEL_COLLECTOR:-deepseek-r1:latest}"
MODEL_WRITER="${MODEL_WRITER:-llama3.1:8b}"
MODEL_REVIEWER="${MODEL_REVIEWER:-deepseek-r1:latest}"

WORKDIR="${WORKDIR:-work}"
INPUT_FILE="${1:-work/input.txt}"

mkdir -p "$WORKDIR"

ts() { date +"%Y%m%d_%H%M%S"; }

run_agent () {
  local model="$1"
  local sys_prompt_file="$2"
  local user_file="$3"
  local out_file="$4"

  [[ -f "$sys_prompt_file" ]] || { echo "Missing prompt: $sys_prompt_file" >&2; exit 1; }
  [[ -f "$user_file" ]] || { echo "Missing input: $user_file" >&2; exit 1; }

  {
    echo "=== SYSTEM ==="
    cat "$sys_prompt_file"
    echo
    echo "=== USER ==="
    cat "$user_file"
  } | ollama run "$model" > "$out_file"
}

# 1) Collector
COLLECT_OUT="$WORKDIR/01_collector_$(ts).md"
run_agent "$MODEL_COLLECTOR" "agents/collector.md" "$INPUT_FILE" "$COLLECT_OUT"

# Extract JSON for writer (first ```json ... ``` block)
JSON_OUT="$WORKDIR/02_data.json"
python3 - "$COLLECT_OUT" "$JSON_OUT" <<'PY'
import re, sys, json, pathlib
src = pathlib.Path(sys.argv[1]).read_text(errors="ignore")
m = re.search(r"```json\\s*(\\{.*?\\})\\s*```", src, flags=re.S)
if not m:
    print("ERROR: collector output missing ```json``` block", file=sys.stderr)
    sys.exit(2)
obj = json.loads(m.group(1))
pathlib.Path(sys.argv[2]).write_text(json.dumps(obj, indent=2))
PY

# 2) Writer
WRITER_IN="$WORKDIR/03_writer_input.md"
{
  echo "Use this JSON as the ONLY source of truth:"
  echo '```json'
  cat "$JSON_OUT"
  echo '```'
} > "$WRITER_IN"

REPORT_OUT="$WORKDIR/04_report_$(ts).md"
run_agent "$MODEL_WRITER" "agents/writer.md" "$WRITER_IN" "$REPORT_OUT"

# 3) Reviewer
REVIEW_IN="$WORKDIR/05_review_input.md"
{
  echo "DATA JSON:"
  echo '```json'
  cat "$JSON_OUT"
  echo '```'
  echo
  echo "DRAFT REPORT:"
  echo '```markdown'
  cat "$REPORT_OUT"
  echo '```'
} > "$REVIEW_IN"

REVIEW_OUT="$WORKDIR/06_review_$(ts).md"
run_agent "$MODEL_REVIEWER" "agents/reviewer.md" "$REVIEW_IN" "$REVIEW_OUT"

echo "Collector: $COLLECT_OUT"
echo "JSON:      $JSON_OUT"
echo "Report:    $REPORT_OUT"
echo "Review:    $REVIEW_OUT"
"""

TEST_INPUT = """Incidents:
- P1 2026-02-12: BNG-A radius timeouts, 27 min impact, mitigation: failover to BNG-B
Changes:
- Upgraded Asterisk 11->20 cutover, post-cutover SIP reg storm mitigated by registration rate-limits
Metrics (Feb):
- Core uptime 99.95%
- Tickets opened 312, closed 298
Risks:
- ASA 5520 ageing, replacement project in progress (target Q2)
Next month plan:
- Replace suspect fibre patch lead at site EAN03
"""


def _state_dir() -> Path:
  raw = os.getenv("OLLAMA_AGENTS_MCP_STATE_DIR", DEFAULT_STATE_DIR)
  path = Path(raw).expanduser().resolve()
  path.mkdir(parents=True, exist_ok=True)
  return path


def _run_command(
  argv: List[str],
  cwd: Path | None = None,
  timeout_sec: int = 120,
  env_overrides: Dict[str, str] | None = None,
) -> Dict[str, Any]:
  env = os.environ.copy()
  if env_overrides:
    env.update(env_overrides)
  proc = subprocess.run(
    argv,
    cwd=str(cwd) if cwd else None,
    text=True,
    capture_output=True,
    timeout=timeout_sec,
    check=False,
    env=env,
  )
  return {
    "ok": proc.returncode == 0,
    "returncode": proc.returncode,
    "command": " ".join(shlex.quote(a) for a in argv),
    "stdout": proc.stdout,
    "stderr": proc.stderr,
  }


def _write_file(path: Path, content: str, overwrite: bool) -> str:
  if path.exists() and not overwrite:
    return "skipped"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content)
  return "written"


def _check_binary(name: str) -> Dict[str, Any]:
  result = _run_command([name, "--version"], timeout_sec=20)
  line = ""
  if result["stdout"].strip():
    line = result["stdout"].splitlines()[0]
  elif result["stderr"].strip():
    line = result["stderr"].splitlines()[0]
  return {"available": result["ok"], "version": line.strip(), "returncode": result["returncode"]}


def _setup_environment(base_dir: Path, overwrite: bool, create_test_input: bool) -> Dict[str, str]:
  agents_dir = base_dir / "agents"
  work_dir = base_dir / "work"
  base_dir.mkdir(parents=True, exist_ok=True)
  agents_dir.mkdir(parents=True, exist_ok=True)
  work_dir.mkdir(parents=True, exist_ok=True)

  created: Dict[str, str] = {}
  created[str(agents_dir / "collector.md")] = _write_file(agents_dir / "collector.md", COLLECTOR_PROMPT, overwrite)
  created[str(agents_dir / "writer.md")] = _write_file(agents_dir / "writer.md", WRITER_PROMPT, overwrite)
  created[str(agents_dir / "reviewer.md")] = _write_file(agents_dir / "reviewer.md", REVIEWER_PROMPT, overwrite)
  created[str(base_dir / "run_agents.sh")] = _write_file(base_dir / "run_agents.sh", RUN_AGENTS_SH, overwrite)

  run_script = base_dir / "run_agents.sh"
  if run_script.exists():
    run_script.chmod(0o755)

  if create_test_input:
    created[str(work_dir / "input.txt")] = _write_file(work_dir / "input.txt", TEST_INPUT, overwrite)

  return created


def _pull_models(models: List[str]) -> List[Dict[str, Any]]:
  results: List[Dict[str, Any]] = []
  for model in models:
    results.append({"model": model, **_run_command(["ollama", "pull", model], timeout_sec=1800)})
  return results


def _run_pipeline(base_dir: Path, input_file: str, collector_model: str, writer_model: str, reviewer_model: str) -> Dict[str, Any]:
  return _run_command(
    ["./run_agents.sh", input_file],
    cwd=base_dir,
    timeout_sec=3600,
    env_overrides={
      "MODEL_COLLECTOR": collector_model,
      "MODEL_WRITER": writer_model,
      "MODEL_REVIEWER": reviewer_model,
    },
  )


@mcp.tool()
def health_check() -> Dict[str, Any]:
  """Check readiness for creating and running local Ollama sub-agent environments."""
  state = _state_dir()
  return {
    "server": "ollama-agents-mcp",
    "state_dir": str(state),
    "checks": {
      "python3": _check_binary("python3"),
      "ollama": _check_binary("ollama"),
    },
    "defaults": {
      "base_dir": DEFAULT_BASE_DIR,
      "action": "setup",
      "collector_model": "deepseek-r1:latest",
      "writer_model": "llama3.1:8b",
      "reviewer_model": "deepseek-r1:latest",
    },
  }


@mcp.tool()
def setup_ollama_agents_environment(
  action: str = "setup",
  base_dir: str = DEFAULT_BASE_DIR,
  overwrite: bool = False,
  pull_models: bool = False,
  create_test_input: bool = True,
  pipeline_input_file: str = "work/input.txt",
  collector_model: str = "deepseek-r1:latest",
  writer_model: str = "llama3.1:8b",
  reviewer_model: str = "deepseek-r1:latest",
) -> Dict[str, Any]:
  """Setup and/or run the local Ollama sub-agent pipeline.

  Args:
    action: One of setup, run, setup_and_run
    base_dir: Target directory for the environment (default: ~/ollama-agents)
    overwrite: Replace existing files when true
    pull_models: Run `ollama pull` for collector/writer/reviewer models
    create_test_input: Create work/input.txt sample file
    pipeline_input_file: Input file path passed to run_agents.sh
    collector_model: Collector model name for optional pull
    writer_model: Writer model name for optional pull
    reviewer_model: Reviewer model name for optional pull
  """
  normalized_action = action.strip().lower()
  if normalized_action not in ALLOWED_ACTIONS:
    return {
      "ok": False,
      "error": f"Invalid action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}",
    }

  do_setup = normalized_action in {"setup", "setup_and_run"}
  do_run = normalized_action in {"run", "setup_and_run"}
  target = Path(base_dir).expanduser().resolve()

  created: Dict[str, str] = {}
  if do_setup:
    created = _setup_environment(target, overwrite, create_test_input)

  if pull_models:
    target.mkdir(parents=True, exist_ok=True)
  model_pull_results = _pull_models([collector_model, writer_model, reviewer_model]) if pull_models else []

  pipeline_result: Dict[str, Any] | None = None
  if do_run:
    run_script = target / "run_agents.sh"
    if not run_script.exists():
      return {
        "ok": False,
        "error": f"Missing {run_script}. Run with action='setup' first or set base_dir correctly.",
        "base_dir": str(target),
      }
    pipeline_result = _run_pipeline(target, pipeline_input_file, collector_model, writer_model, reviewer_model)

  manifest = {
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "action": normalized_action,
    "base_dir": str(target),
    "files": created,
    "did_setup": do_setup,
    "did_run": do_run,
    "pull_models": pull_models,
    "model_pulls": [{"model": r["model"], "ok": r["ok"], "returncode": r["returncode"]} for r in model_pull_results],
    "pipeline_input_file": pipeline_input_file if do_run else None,
    "pipeline_ok": None if pipeline_result is None else pipeline_result["ok"],
  }

  state_file = _state_dir() / "last_action.json"
  state_file.write_text(json.dumps(manifest, indent=2))

  return {
    "ok": True,
    "action": normalized_action,
    "base_dir": str(target),
    "files": created,
    "model_pulls": model_pull_results,
    "pipeline_result": pipeline_result,
    "next_commands": [
      f"cd {shlex.quote(str(target))}",
      f"./run_agents.sh {shlex.quote(pipeline_input_file)}",
      "ls -lh work",
    ],
    "state_file": str(state_file),
  }


def main() -> int:
  mcp.run()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
