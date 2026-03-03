#!/usr/bin/env python3
"""MCP server for setting up local Ollama-based sub-agent environments."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ollama-agents-mcp")

DEFAULT_STATE_DIR = "/Volumes/Data/_ai/mcp-data/ollama-agents-mcp"
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

DEFAULT_ROLE_PROMPTS: Dict[str, str] = {
  "collector": COLLECTOR_PROMPT,
  "writer": WRITER_PROMPT,
  "reviewer": REVIEWER_PROMPT,
}
ROLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

RUN_AGENTS_SH = """#!/usr/bin/env bash
set -euo pipefail

# ---- models (override via env vars) ----
MODEL_COLLECTOR="${MODEL_COLLECTOR:-deepseek-r1:latest}"
MODEL_WRITER="${MODEL_WRITER:-llama3.1:8b}"
MODEL_REVIEWER="${MODEL_REVIEWER:-deepseek-r1:latest}"
COLLECTOR_RETRIES="${COLLECTOR_RETRIES:-3}"
ENFORCE_SCHEMA="${ENFORCE_SCHEMA:-false}"
SCHEMA_REQUIRED_KEYS="${SCHEMA_REQUIRED_KEYS:-incidents,changes,metrics,risks,next_month_plan}"

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

extract_json () {
  local in_file="$1"
  local out_file="$2"
  python3 - "$in_file" "$out_file" <<'PY'
import json, pathlib, re, sys
src = pathlib.Path(sys.argv[1]).read_text(errors="ignore")

# First try explicit fenced JSON block.
m = re.search(r"```json\\s*(\\{.*?\\})\\s*```", src, flags=re.S)
if m:
    obj = json.loads(m.group(1))
    pathlib.Path(sys.argv[2]).write_text(json.dumps(obj, indent=2))
    sys.exit(0)

# Fallback: first decodable JSON object anywhere in the output.
decoder = json.JSONDecoder()
for idx, ch in enumerate(src):
    if ch != "{":
        continue
    try:
        obj, end = decoder.raw_decode(src[idx:])
        if isinstance(obj, dict):
            pathlib.Path(sys.argv[2]).write_text(json.dumps(obj, indent=2))
            sys.exit(0)
    except json.JSONDecodeError:
        continue

print("ERROR: collector output missing parseable JSON object", file=sys.stderr)
sys.exit(2)
PY
}

validate_schema () {
  local json_file="$1"
  python3 - "$json_file" "$SCHEMA_REQUIRED_KEYS" <<'PY'
import json, pathlib, sys
obj = json.loads(pathlib.Path(sys.argv[1]).read_text())
if not isinstance(obj, dict):
    print("ERROR: JSON root must be an object", file=sys.stderr)
    sys.exit(3)
required = [k.strip() for k in sys.argv[2].split(",") if k.strip()]
missing = [k for k in required if k not in obj]
if missing:
    print("ERROR: schema validation failed; missing keys: " + ", ".join(missing), file=sys.stderr)
    sys.exit(3)
PY
}

# 1) Collector (with retry + robust JSON extraction)
JSON_OUT="$WORKDIR/02_data.json"
COLLECT_OUT=""
success=0
for attempt in $(seq 1 "$COLLECTOR_RETRIES"); do
  COLLECT_OUT="$WORKDIR/01_collector_$(ts)_try${attempt}.md"
  run_agent "$MODEL_COLLECTOR" "agents/collector.md" "$INPUT_FILE" "$COLLECT_OUT"
  if extract_json "$COLLECT_OUT" "$JSON_OUT"; then
    success=1
    break
  fi
done

if [[ "$success" -ne 1 ]]; then
  echo "ERROR: collector failed to produce valid JSON after $COLLECTOR_RETRIES attempts" >&2
  exit 2
fi

if [[ "${ENFORCE_SCHEMA,,}" == "true" ]]; then
  validate_schema "$JSON_OUT"
fi

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


def _default_base_dir() -> Path:
  raw = os.getenv("OLLAMA_AGENTS_BASE_DIR", "").strip()
  if raw:
    return Path(raw).expanduser().resolve()
  return _state_dir() / "workspace"


def _resolve_base_dir(base_dir: str) -> Path:
  return Path(base_dir).expanduser().resolve() if base_dir.strip() else _default_base_dir()


def _validate_role_name(role: str) -> str:
  normalized = role.strip().lower()
  if not normalized or not ROLE_NAME_RE.fullmatch(normalized):
    raise ValueError("role must match ^[a-zA-Z0-9_-]{1,64}$")
  return normalized


def _role_file_path(base_dir: Path, role: str) -> Path:
  return base_dir / "agents" / f"{role}.md"


def _list_role_names(base_dir: Path) -> List[str]:
  agents_dir = base_dir / "agents"
  if not agents_dir.exists():
    return []
  return sorted(path.stem for path in agents_dir.glob("*.md"))


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


def _run_ollama_with_system_user(model: str, system_prompt: str, user_text: str, timeout_sec: int = 1800) -> Dict[str, Any]:
  payload = "\n".join(
    [
      "=== SYSTEM ===",
      system_prompt,
      "",
      "=== USER ===",
      user_text,
    ]
  )
  proc = subprocess.run(
    ["ollama", "run", model],
    text=True,
    input=payload,
    capture_output=True,
    timeout=timeout_sec,
    check=False,
    env=os.environ.copy(),
  )
  return {
    "ok": proc.returncode == 0,
    "returncode": proc.returncode,
    "command": f"ollama run {shlex.quote(model)}",
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
  for role, prompt in DEFAULT_ROLE_PROMPTS.items():
    role_file = _role_file_path(base_dir, role)
    created[str(role_file)] = _write_file(role_file, prompt, overwrite)
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


def _run_pipeline(
  base_dir: Path,
  input_file: str,
  collector_model: str,
  writer_model: str,
  reviewer_model: str,
  collector_retries: int = 3,
  enforce_schema: bool = False,
) -> Dict[str, Any]:
  return _run_command(
    ["./run_agents.sh", input_file],
    cwd=base_dir,
    timeout_sec=3600,
    env_overrides={
      "MODEL_COLLECTOR": collector_model,
      "MODEL_WRITER": writer_model,
      "MODEL_REVIEWER": reviewer_model,
      "COLLECTOR_RETRIES": str(max(1, collector_retries)),
      "ENFORCE_SCHEMA": "true" if enforce_schema else "false",
    },
  )


def _list_workspace_inputs(base_dir: Path) -> List[str]:
  work_dir = base_dir / "work"
  if not work_dir.exists():
    return []
  files: List[str] = []
  for path in sorted(work_dir.glob("*")):
    if path.is_file():
      files.append(f"work/{path.name}")
  return files


def _default_input_file(base_dir: Path) -> str:
  preferred = base_dir / "work" / "input.txt"
  if preferred.exists():
    return "work/input.txt"
  files = _list_workspace_inputs(base_dir)
  if files:
    return files[0]
  return "work/input.txt"


def _list_ollama_models() -> List[str]:
  result = _run_command(["ollama", "list"], timeout_sec=30)
  if not result["ok"]:
    return []
  models: List[str] = []
  for line in result["stdout"].splitlines():
    raw = line.strip()
    if not raw or raw.lower().startswith("name "):
      continue
    model = raw.split()[0]
    if model not in models:
      models.append(model)
  return models


def _resolve_model_choice(choice: str, fallback: str) -> str:
  picked = choice.strip()
  return picked if picked else fallback


def _ensure_under_base(path: Path, base_dir: Path) -> None:
  path.relative_to(base_dir.resolve())


def _build_combined_input(base_dir: Path, input_spec: str) -> Dict[str, Any]:
  spec = input_spec.strip()
  if not spec:
    spec = _default_input_file(base_dir)

  parts = [p.strip() for p in spec.split(",") if p.strip()]
  if not parts:
    parts = [_default_input_file(base_dir)]

  source_files: List[Path] = []
  for part in parts:
    candidate = (base_dir / part).resolve()
    try:
      _ensure_under_base(candidate, base_dir)
    except ValueError:
      return {"ok": False, "error": f"Input path must resolve under base_dir: {part}"}
    if not candidate.exists():
      return {"ok": False, "error": f"Input path not found: {part}"}
    if candidate.is_dir():
      files = sorted(p for p in candidate.rglob("*") if p.is_file())
      if not files:
        return {"ok": False, "error": f"Input folder has no files: {part}"}
      source_files.extend(files)
    else:
      source_files.append(candidate)

  # De-duplicate while preserving order.
  unique_sources: List[Path] = []
  seen: set[str] = set()
  for file_path in source_files:
    key = str(file_path)
    if key not in seen:
      seen.add(key)
      unique_sources.append(file_path)

  if len(unique_sources) == 1:
    rel = unique_sources[0].relative_to(base_dir.resolve())
    return {
      "ok": True,
      "pipeline_input_file": str(rel),
      "source_files": [str(rel)],
      "combined": False,
    }

  work_dir = base_dir / "work"
  work_dir.mkdir(parents=True, exist_ok=True)
  ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
  combined_path = work_dir / f"00_combined_input_{ts}.txt"

  chunks: List[str] = []
  for src in unique_sources:
    rel = src.relative_to(base_dir.resolve())
    chunks.append(f"### SOURCE: {rel}")
    chunks.append(src.read_text(errors="ignore"))
    chunks.append("")
  combined_path.write_text("\n".join(chunks))

  rel_combined = combined_path.relative_to(base_dir.resolve())
  return {
    "ok": True,
    "pipeline_input_file": str(rel_combined),
    "source_files": [str(p.relative_to(base_dir.resolve())) for p in unique_sources],
    "combined": True,
    "combined_file": str(rel_combined),
  }


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
      "base_dir": str(_default_base_dir()),
      "action": "setup",
      "collector_model": "deepseek-r1:latest",
      "writer_model": "llama3.1:8b",
      "reviewer_model": "deepseek-r1:latest",
      "collector_retries": 3,
      "enforce_schema": False,
    },
    "tools": [
      "health_check",
      "setup_ollama_agents_environment",
      "setup_default_environment",
      "setup_and_run_default_pipeline",
      "list_pipeline_run_options",
      "run_ollama_agents_pipeline",
      "run_default_pipeline",
      "run_pipeline_guided",
      "run_role_agent",
      "list_agent_roles",
      "get_agent_role_prompt",
      "upsert_agent_role_prompt",
      "delete_agent_role_prompt",
    ],
  }


@mcp.tool()
def setup_ollama_agents_environment(
  action: str = "setup",
  base_dir: str = "",
  overwrite: bool = False,
  pull_models: bool = False,
  create_test_input: bool = True,
  pipeline_input_file: str = "work/input.txt",
  collector_model: str = "deepseek-r1:latest",
  writer_model: str = "llama3.1:8b",
  reviewer_model: str = "deepseek-r1:latest",
  collector_retries: int = 3,
  enforce_schema: bool = False,
) -> Dict[str, Any]:
  """Setup and/or run the local Ollama sub-agent pipeline.

  Args:
    action: One of setup, run, setup_and_run
    base_dir: Target directory for environment files (default: <MCP_DATA_ROOT>/ollama-agents-mcp/workspace)
    overwrite: Replace existing files when true
    pull_models: Run `ollama pull` for collector/writer/reviewer models
    create_test_input: Create work/input.txt sample file
    pipeline_input_file: Input file path passed to run_agents.sh
    collector_model: Collector model name for optional pull
    writer_model: Writer model name for optional pull
    reviewer_model: Reviewer model name for optional pull
    collector_retries: Retries for collector JSON extraction failures
    enforce_schema: If true, require strict schema keys in collector JSON
  """
  normalized_action = action.strip().lower()
  if normalized_action not in ALLOWED_ACTIONS:
    return {
      "ok": False,
      "error": f"Invalid action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}",
    }

  do_setup = normalized_action in {"setup", "setup_and_run"}
  do_run = normalized_action in {"run", "setup_and_run"}
  target = _resolve_base_dir(base_dir)

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
    pipeline_result = _run_pipeline(
      target,
      pipeline_input_file,
      collector_model,
      writer_model,
      reviewer_model,
      collector_retries,
      enforce_schema,
    )

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
    "collector_retries": collector_retries if do_run else None,
    "enforce_schema": enforce_schema if do_run else None,
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


@mcp.tool()
def run_ollama_agents_pipeline(
  base_dir: str = "",
  pipeline_input_file: str = "work/input.txt",
  collector_model: str = "deepseek-r1:latest",
  writer_model: str = "llama3.1:8b",
  reviewer_model: str = "deepseek-r1:latest",
  collector_retries: int = 3,
  enforce_schema: bool = False,
) -> Dict[str, Any]:
  """Run the existing pipeline without modifying setup files.

  pipeline_input_file supports:
  - single file path (for example: work/input.txt)
  - comma-separated file paths (for example: work/a.txt,work/b.txt)
  - folder path (recursively combines files)
  """
  target = _resolve_base_dir(base_dir)
  run_script = target / "run_agents.sh"
  if not run_script.exists():
    return {
      "ok": False,
      "error": f"Missing {run_script}. Run setup_ollama_agents_environment(action='setup') first.",
      "base_dir": str(target),
    }

  input_resolution = _build_combined_input(target, pipeline_input_file)
  if not input_resolution["ok"]:
    return {
      "ok": False,
      "error": input_resolution["error"],
      "base_dir": str(target),
    }

  pipeline_result = _run_pipeline(
    target,
    input_resolution["pipeline_input_file"],
    collector_model,
    writer_model,
    reviewer_model,
    collector_retries,
    enforce_schema,
  )
  return {
    "ok": pipeline_result["ok"],
    "base_dir": str(target),
    "pipeline_input_file": input_resolution["pipeline_input_file"],
    "input_sources": input_resolution["source_files"],
    "combined_input": input_resolution["combined"],
    "combined_input_file": input_resolution.get("combined_file"),
    "collector_retries": collector_retries,
    "enforce_schema": enforce_schema,
    "pipeline_result": pipeline_result,
  }


@mcp.tool()
def list_pipeline_run_options(base_dir: str = "") -> Dict[str, Any]:
  """List available workspace input files and local Ollama models with defaults."""
  target = _resolve_base_dir(base_dir)
  work_dir = target / "work"
  available_input_dirs: List[str] = []
  if work_dir.exists():
    available_input_dirs = sorted(
      str(path.relative_to(target.resolve()))
      for path in work_dir.iterdir()
      if path.is_dir()
    )
  return {
    "ok": True,
    "base_dir": str(target),
    "available_input_files": _list_workspace_inputs(target),
    "available_input_dirs": available_input_dirs,
    "available_ollama_models": _list_ollama_models(),
    "defaults": {
      "input_file": _default_input_file(target),
      "collector_model": "deepseek-r1:latest",
      "writer_model": "llama3.1:8b",
      "reviewer_model": "deepseek-r1:latest",
      "collector_retries": 3,
      "enforce_schema": False,
    },
    "input_examples": [
      "work/input.txt",
      "work/a.txt,work/b.txt",
      "work/",
    ],
    "hint": "Leave parameters blank in run_pipeline_guided to accept defaults.",
  }


@mcp.tool()
def setup_default_environment(
  base_dir: str = "",
  overwrite: bool = False,
  create_test_input: bool = True,
) -> Dict[str, Any]:
  """Short alias for default setup with built-in role prompts and optional test input."""
  return setup_ollama_agents_environment(
    action="setup",
    base_dir=base_dir,
    overwrite=overwrite,
    pull_models=False,
    create_test_input=create_test_input,
  )


@mcp.tool()
def run_default_pipeline(
  base_dir: str = "",
  input_file: str = "work/input.txt",
) -> Dict[str, Any]:
  """Short alias for running the pipeline with default models and hardening settings."""
  return run_ollama_agents_pipeline(
    base_dir=base_dir,
    pipeline_input_file=input_file,
  )


@mcp.tool()
def run_pipeline_guided(
  base_dir: str = "",
  input_file: str = "",
  collector_model: str = "",
  writer_model: str = "",
  reviewer_model: str = "",
  collector_retries: int = 3,
  enforce_schema: bool = False,
) -> Dict[str, Any]:
  """Run pipeline with optional blanks; blank values auto-resolve to defaults."""
  target = _resolve_base_dir(base_dir)
  resolved_input = input_file.strip() or _default_input_file(target)
  resolved_collector = _resolve_model_choice(collector_model, "deepseek-r1:latest")
  resolved_writer = _resolve_model_choice(writer_model, "llama3.1:8b")
  resolved_reviewer = _resolve_model_choice(reviewer_model, "deepseek-r1:latest")
  result = run_ollama_agents_pipeline(
    base_dir=str(target),
    pipeline_input_file=resolved_input,
    collector_model=resolved_collector,
    writer_model=resolved_writer,
    reviewer_model=resolved_reviewer,
    collector_retries=collector_retries,
    enforce_schema=enforce_schema,
  )
  return {
    **result,
    "resolved": {
      "input_file": resolved_input,
      "collector_model": resolved_collector,
      "writer_model": resolved_writer,
      "reviewer_model": resolved_reviewer,
    },
  }


@mcp.tool()
def setup_and_run_default_pipeline(
  base_dir: str = "",
  overwrite: bool = False,
  create_test_input: bool = True,
  input_file: str = "work/input.txt",
) -> Dict[str, Any]:
  """One-call setup and run using default models."""
  return setup_ollama_agents_environment(
    action="setup_and_run",
    base_dir=base_dir,
    overwrite=overwrite,
    pull_models=False,
    create_test_input=create_test_input,
    pipeline_input_file=input_file,
  )


@mcp.tool()
def run_role_agent(
  role: str,
  input_text: str = "",
  input_file: str = "",
  model: str = "",
  base_dir: str = "",
  save_output: bool = True,
  output_subdir: str = "work/role_runs",
) -> Dict[str, Any]:
  """Run one role prompt against input text/file using an Ollama model."""
  target = _resolve_base_dir(base_dir)
  try:
    role_name = _validate_role_name(role)
  except ValueError as exc:
    return {"ok": False, "error": str(exc)}

  role_file = _role_file_path(target, role_name)
  if not role_file.exists():
    return {
      "ok": False,
      "error": f"Role prompt not found: {role_file}",
      "base_dir": str(target),
      "role": role_name,
    }

  user_text: Optional[str] = input_text.strip() or None
  if not user_text and input_file.strip():
    input_path = (target / input_file).resolve()
    try:
      input_path.relative_to(target.resolve())
    except ValueError:
      return {"ok": False, "error": "input_file must resolve under base_dir"}
    if not input_path.exists():
      return {"ok": False, "error": f"Input file not found: {input_path}"}
    user_text = input_path.read_text(errors="ignore")
  if not user_text:
    return {"ok": False, "error": "Provide input_text or input_file"}

  model_name = model.strip()
  if not model_name:
    model_name = os.getenv("OLLAMA_DEFAULT_ROLE_MODEL", "llama3.1:8b")

  system_prompt = role_file.read_text(errors="ignore")
  run_result = _run_ollama_with_system_user(model_name, system_prompt, user_text)

  output_path = None
  if save_output and run_result["ok"]:
    out_dir = (target / output_subdir).resolve()
    try:
      out_dir.relative_to(target.resolve())
    except ValueError:
      return {"ok": False, "error": "output_subdir must resolve under base_dir"}
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"{role_name}_{ts}.md"
    output_path.write_text(run_result["stdout"])

  return {
    "ok": run_result["ok"],
    "base_dir": str(target),
    "role": role_name,
    "role_prompt_path": str(role_file),
    "model": model_name,
    "input_source": "text" if input_text.strip() else "file",
    "output_path": str(output_path) if output_path else None,
    "result": run_result,
  }


@mcp.tool()
def list_agent_roles(base_dir: str = "") -> Dict[str, Any]:
  """List current role prompts in the agents directory."""
  target = _resolve_base_dir(base_dir)
  roles = _list_role_names(target)
  return {
    "ok": True,
    "base_dir": str(target),
    "agents_dir": str(target / "agents"),
    "roles": roles,
    "count": len(roles),
  }


@mcp.tool()
def get_agent_role_prompt(role: str, base_dir: str = "") -> Dict[str, Any]:
  """Get prompt content for one role."""
  target = _resolve_base_dir(base_dir)
  try:
    role_name = _validate_role_name(role)
  except ValueError as exc:
    return {"ok": False, "error": str(exc)}

  role_file = _role_file_path(target, role_name)
  if not role_file.exists():
    return {
      "ok": False,
      "error": f"Role prompt not found: {role_file}",
      "base_dir": str(target),
      "role": role_name,
    }
  return {
    "ok": True,
    "base_dir": str(target),
    "role": role_name,
    "path": str(role_file),
    "prompt": role_file.read_text(),
  }


@mcp.tool()
def upsert_agent_role_prompt(
  role: str,
  prompt: str,
  base_dir: str = "",
  overwrite: bool = True,
) -> Dict[str, Any]:
  """Create or update a role prompt file, enabling more roles over time."""
  target = _resolve_base_dir(base_dir)
  try:
    role_name = _validate_role_name(role)
  except ValueError as exc:
    return {"ok": False, "error": str(exc)}

  if not prompt.strip():
    return {"ok": False, "error": "prompt cannot be empty"}

  role_file = _role_file_path(target, role_name)
  result = _write_file(role_file, prompt, overwrite)
  return {
    "ok": True,
    "base_dir": str(target),
    "role": role_name,
    "path": str(role_file),
    "result": result,
  }


@mcp.tool()
def delete_agent_role_prompt(
  role: str,
  base_dir: str = "",
  confirm: bool = False,
) -> Dict[str, Any]:
  """Delete a role prompt file (requires confirm=true)."""
  if not confirm:
    return {"ok": False, "error": "confirm=true is required to delete role prompts"}

  target = _resolve_base_dir(base_dir)
  try:
    role_name = _validate_role_name(role)
  except ValueError as exc:
    return {"ok": False, "error": str(exc)}

  role_file = _role_file_path(target, role_name)
  if not role_file.exists():
    return {
      "ok": False,
      "error": f"Role prompt not found: {role_file}",
      "base_dir": str(target),
      "role": role_name,
    }
  role_file.unlink()
  return {
    "ok": True,
    "base_dir": str(target),
    "role": role_name,
    "deleted_path": str(role_file),
  }


def main() -> int:
  mcp.run()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
