# Run model benchmarks locally

The scripts use the existing configured model list, credentials and compatibility profiles.
They do not change model order or configuration, connect to PostgreSQL/Redis, create a worker,
execute model-generated code, or publish repository changes. Live inference consumes provider quota.
OpenRouter consent, allowed-host restrictions and free-model-only checks remain enforced.

From PowerShell:

```powershell
cd C:\Users\divin\AgentGate\backend
$env:PYTHONPATH = Join-Path $env:LOCALAPPDATA "Audoryn\python313-packages"

# List routes without sending any request.
uv run --python 3.13 --no-project python scripts/benchmark_ai_interactive.py --dry-run

# Same short synthetic question for every model; 250 seconds per trial including correction.
uv run --python 3.13 --no-project python scripts/benchmark_ai_interactive.py --trials 3

# Same synthetic coding problem; 2,000 seconds per trial including correction.
uv run --python 3.13 --no-project python scripts/benchmark_ai_complex.py --trials 3
```

Defaults use `AI_INTERACTIVE_ATTEMPT_SECONDS` (250) and
`RUNTIME_PLANNER_TIMEOUT_SECONDS` (2,000). No fallback occurs inside a trial: each model
is tested explicitly, with at most one schema correction and no transport retry.
The interactive benchmark measures one complete structured response, not the two-stage chat turn.
For a preliminary run, use `--trials 1`; repeat with at least three trials before choosing an order.
Sequential requests and a rotating start position reduce quota bursts and order bias.
Provider retry timing is honored; invalid account credentials/configuration and account quota
exhaustion mark sibling routes as **not called**, rather than inventing failures for each model.

Optional filters and overrides:

```powershell
uv run --python 3.13 --no-project python scripts/benchmark_ai_interactive.py --trials 1 --models "nvidia_nim:moonshotai/kimi-k3" "nvidia_nim:nvidia/nemotron-3-ultra-550b-a55b"
```

The `configured` alias is resolved before filtering. Use the actual provider:model ID printed by
`--dry-run`. `--timeout SECONDS`, `--delay SECONDS` and `--output-dir PATH` override the benchmark only.

Each run creates `.local/benchmarks/<interactive-or-complex>-<timestamp>/`:

- `report.json`: preparation time, full-response elapsed time, individual transport call times,
  token usage, actual compatibility profile, validation/static-check results and proposed answer.
- `summary.csv`: median and slowest **valid** response, success count, timeout count and error categories.

Reports are updated after each completed trial so interruption preserves earlier results. Timeout waits
are recorded separately and excluded from valid-response latency; all-failed models have no median.
Provider exception bodies and credentials are not recorded. Synthetic answers and patches are retained
for manual review. No generated patch is applied or executed.

The complex benchmark checks JSON constraints, exact allowed file paths and Python syntax. It does
not establish behavioral correctness, successful tests, live GitHub publication or production reliability.
Different models retain their supported reasoning/generation profiles; these are recorded for comparison.
Choose an order using validity, manually reviewed answer quality and latency together. The scripts
do not automatically rank or edit the production model list.

The default is now two trials. Use `--candidates --trials 2` to test only the new Groq/HF
candidates, independently of ranked routes. Setup and exact commands:
[Additional model connections](ai-model-connections.md).
