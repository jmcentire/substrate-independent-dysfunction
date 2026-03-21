# Spec: Unary Experiment Runner

Build `experiments/unary/run.py` — a wrapper that invokes Claude Code CLI as a single-agent baseline, captures all instrumentation, and writes output in the standard experiment format.

## What This Is

Part of a 4-architecture experiment comparing coordination topologies. The "unary" condition is one Claude Code agent with no coordination overhead — the control. The other three architectures (hi-trust hierarchy, org swarm, emergence swarm) already exist and produce standardized output. This wrapper needs to match their format exactly.

## CLI Interface

```bash
python experiments/unary/run.py \
  --task "I need a command-line tool that lets me manage a personal book library..." \
  --budget 10.0 \
  --output experiments/unary/simple \
  --run-id run001
```

### Arguments

| Arg | Type | Required | Description |
|-----|------|----------|-------------|
| `--task` | str | yes | Task prompt (identical across all architectures) |
| `--budget` | float | yes | USD budget cap (for result.json; Claude Code manages its own spending) |
| `--output` | str | yes | Base output directory |
| `--run-id` | str | yes | Unique run identifier |

## Output Directory Structure

Must create exactly:

```
{output}/{run_id}/
├── task.txt        # Verbatim copy of --task
├── output/         # All code artifacts Claude Code produces
├── log.jsonl       # Instrumentation log (format below)
└── result.json     # Run summary (format below)
```

## How It Works

1. Create the run directory and `output/` subdirectory
2. Write `task.txt`
3. Write a `run_started` event to `log.jsonl`
4. Invoke Claude Code CLI, working inside the `output/` directory
5. Parse Claude Code's output for cost/token data
6. Write a `run_completed` event to `log.jsonl`
7. Write `result.json`

## Invoking Claude Code

Use the `claude` CLI in print mode so it runs non-interactively:

```bash
claude --print \
  --output-format json \
  --model claude-opus-4-6 \
  --max-turns 50 \
  "YOUR TASK PROMPT HERE"
```

Run this as a subprocess from Python (`subprocess.run` or `asyncio.create_subprocess_exec`), with `cwd` set to the `output/` directory so all files Claude Code creates land there.

**`--output-format json`** is critical — it returns structured JSON with token usage and cost data that we can parse. The JSON output includes fields like `input_tokens`, `output_tokens`, and `cost_usd` (or similar — inspect the actual output and adapt).

If `--output-format json` doesn't provide token data, fall back to `--output-format stream-json` which emits per-message JSON lines, or just use `--print` and accept that cost data will be `null`.

**Timeout:** Set a 2-hour subprocess timeout (7200 seconds). If it times out, record the error.

## JSONL Log Format

Each line is a JSON object. Write these events:

```jsonl
{"ts": "2026-02-13T20:00:00Z", "wall_s": 0.0, "agent": "system", "event": "run_started", "summary": "Unary (Claude Code) | budget=$10.0", "tokens_in": 0, "tokens_out": 0}
{"ts": "2026-02-13T20:15:00Z", "wall_s": 900.0, "agent": "claude-code", "event": "work_produced", "summary": "Claude Code session completed", "tokens_in": 50000, "tokens_out": 15000}
{"ts": "2026-02-13T20:15:01Z", "wall_s": 900.1, "agent": "system", "event": "run_completed", "summary": "$3.50 spent, 12 files, 900s", "tokens_in": 0, "tokens_out": 0}
```

Minimum events: `run_started` and `run_completed`. Add `work_produced` with token data if parseable from Claude Code output. Add `error` if the subprocess fails or times out.

### Implementation

Use the logger from `experiments/shared/instrumentation.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.instrumentation import InstrumentationLogger

logger = InstrumentationLogger(run_dir / "log.jsonl")
logger.log("system", "run_started", "Unary (Claude Code) | budget=$10.0")
# ... run claude ...
logger.log("claude-code", "work_produced", "Session completed", tokens_in=N, tokens_out=M)
logger.log("system", "run_completed", f"${cost:.2f} spent, {num_files} files, {wall_s:.0f}s")
```

## result.json Format

```json
{
  "architecture": "unary",
  "run_id": "run001",
  "task_prompt": "I need a command-line tool...",
  "model": "claude-opus-4-6",
  "started_at": "2026-02-13T20:00:00Z",
  "completed_at": "2026-02-13T20:15:00Z",
  "wall_seconds": 900.0,
  "budget": {
    "cap_usd": 10.0,
    "spent_usd": 3.50,
    "total_input_tokens": 50000,
    "total_output_tokens": 15000
  },
  "files_produced": ["main.py", "models.py", "cli.py"],
  "error": null
}
```

- `spent_usd`: Parse from Claude Code output if available, otherwise `null`
- `total_input_tokens` / `total_output_tokens`: Parse from output if available, otherwise `null`
- `files_produced`: List all files in `output/` (relative paths)
- `error`: `null` on success, error string on failure/timeout

## Important Notes

- **Don't over-engineer.** This is a thin wrapper — ~80-120 lines of Python. The real work is done by Claude Code.
- **The `ANTHROPIC_API_KEY` env var must be set** (Claude Code reads it automatically).
- **The `claude` CLI must be on PATH.**
- **All code Claude Code produces goes in `output/`.** Set `cwd` to that directory when invoking.
- **Don't modify Claude Code's behavior.** No custom CLAUDE.md, no hooks, no system prompts beyond the task. The point is measuring vanilla single-agent performance.
- **Replace the existing `run.sh`** — this Python version supersedes it.

## Existing File to Replace

There's currently a `experiments/unary/run.sh` shell script. The new `run.py` replaces it. You can delete `run.sh` after creating `run.py`.

## Testing

After building, verify with:
```bash
# Should show help
python experiments/unary/run.py --help

# Dry run (will fail if ANTHROPIC_API_KEY not set, but should create dirs + task.txt)
python experiments/unary/run.py --task "Write hello world" --budget 0.50 --output /tmp/unary_test --run-id test001
```
