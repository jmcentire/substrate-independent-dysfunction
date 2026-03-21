# Interface Specification: Emergence Swarm

This document defines the interface that the Emergence Swarm implementation (`experiments/emergence/run.py`) must conform to, so that all four architectures produce comparable, analyzable output.

## CLI Interface

```bash
python experiments/emergence/run.py \
  --task "The task prompt text..." \
  --budget 10.0 \
  --output experiments/emergence/simple \
  --run-id run001
```

### Required Arguments

| Arg | Type | Description |
|-----|------|-------------|
| `--task` | str | The full task prompt (identical across architectures) |
| `--budget` | float | USD spending cap for this run |
| `--output` | str | Base output directory |
| `--run-id` | str | Unique run identifier (e.g., `run001`) |

### Optional Arguments

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--model` | str | `claude-opus-4-6` | Anthropic model ID |
| `--num-agents` | int | 5 | Number of concurrent stigmergic agents |

## Output Directory Structure

The run must create:

```
{output}/{run_id}/
├── task.txt        # Verbatim copy of the --task prompt
├── output/         # All code artifacts produced by agents
├── log.jsonl       # Instrumentation log (format below)
└── result.json     # Run summary (format below)
```

## Shared Infrastructure

Import from `experiments/shared/`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.instrumentation import InstrumentationLogger, BudgetTracker, BudgetExceeded
from shared.tools import make_tools
```

### BudgetTracker

```python
budget = BudgetTracker(cap_usd=args.budget)
# After each API call:
budget.record(input_tokens=response.usage.input_tokens,
              output_tokens=response.usage.output_tokens)
# Raises BudgetExceeded if cap breached
```

### InstrumentationLogger

```python
logger = InstrumentationLogger(run_dir / "log.jsonl")
logger.log(agent_id="agent-03", event_type="claimed_task",
           summary="Claimed task: implement rate limiter",
           tokens_in=500, tokens_out=200,
           extra={"task_id": "task-04"})
```

### Tools

```python
tools = make_tools(work_dir=run_dir / "output")
# Returns: [write_file, read_file, list_directory, run_command]
# All paths sandboxed to work_dir
# Pass these to client.beta.messages.tool_runner(tools=tools, ...)
```

## JSONL Log Format

Each line is a JSON object:

```json
{"ts": "2026-02-13T20:15:30.123Z", "wall_s": 12.345, "agent": "agent-03", "event": "claimed_task", "summary": "Claimed task: implement rate limiter", "tokens_in": 500, "tokens_out": 200, "extra": {"task_id": "task-04"}}
```

### Required Event Types

Log these events so cross-architecture analysis is possible:

| Event Type | When | Summary Should Include |
|------------|------|----------------------|
| `run_started` | Once at start | Task description, num agents, budget |
| `task_received` | Agent begins processing a task | Task description |
| `claimed_task` | Agent claims an open task from the board | Task ID, task description |
| `created_task` | Agent creates a new task on the board | New task ID, description, rationale |
| `decision_made` | Agent decides what to do | Decision description |
| `work_produced` | Agent writes code/files | File names, line counts |
| `assembly_started` | Agent begins integrating components | What's being integrated |
| `assembled_from` | Integration complete | Source components, result files |
| `budget_exceeded` | BudgetExceeded raised | Amount spent, cap |
| `run_completed` | Once at end | Total cost, files produced, elapsed time |

## result.json Format

```json
{
  "architecture": "emergence",
  "run_id": "run001",
  "task_prompt": "...",
  "model": "claude-opus-4-6",
  "num_agents": 5,
  "started_at": "2026-02-13T20:00:00Z",
  "completed_at": "2026-02-13T20:25:00Z",
  "wall_seconds": 1500.0,
  "budget": {
    "cap_usd": 10.0,
    "spent_usd": 7.23,
    "total_input_tokens": 250000,
    "total_output_tokens": 80000
  },
  "files_produced": ["main.py", "models.py", "..."],
  "error": null
}
```

## Architecture Requirements (from experiment.md)

The emergence swarm must implement stigmergic coordination:

1. **Shared environment only** — no direct agent-to-agent communication
2. **Task board** — a shared data structure with `{task_id, description, status, claimed_by}`
3. **All agents identical** — same model, same base prompt, same tools
4. **Agents claim work** — read the board, pick open tasks suited to their assessment
5. **Artifact coordination** — agents see what others built by reading the shared repo
6. **Agent-created subtasks** — agents decompose work by adding tasks to the board
7. **Termination** — all board tasks done, OR budget exhausted, OR timeout

### Agent Loop (pseudocode)

```
loop:
  scan task_board → find open tasks
  scan repository → observe current codebase state
  if suitable_task found:
    claim(task) → set status=claimed
    work = execute(task, repo_context)
    commit(work) → write to shared output/
    update task_board → status=done
    log event
  elif integration_needed:
    create_task("integrate X and Y")
    claim and execute it
  else:
    observe what others did
    maybe create new tasks for gaps
```

### Constraints

- Suggested: 4-6 agents for simple task, 6-10 for complex
- No agent modifies another agent's code without going through the repo
- Task board operations should be atomic (use threading locks or similar)
- Run terminates when all tasks are done OR budget exhausted
- Seed the board with ONE initial high-level task

## Model and API Usage

Use the Anthropic Python SDK (v0.78.0):

```python
from anthropic import Anthropic, beta_tool

client = Anthropic()  # reads ANTHROPIC_API_KEY from env

# For tool-use agentic loops:
runner = client.beta.messages.tool_runner(
    model=model,
    max_tokens=4096,
    system="Your system prompt...",
    messages=[{"role": "user", "content": task_prompt}],
    tools=tools,  # from make_tools()
    max_iterations=20,
)
final = runner.until_done()

# Access usage from runner messages:
# Iterate with `for msg in runner:` to capture per-turn usage
```

## Important Notes

- The `ANTHROPIC_API_KEY` env var must be set before running
- All agents share the same `output/` directory — file clobbering is a valid dysfunction signal
- The BudgetTracker is shared across all agents in the run — thread-safe recording needed
- Keep agent system prompts focused: "You are a software engineer. Observe the codebase, claim a task, build it."
