# Configure and benchmark additional Smart Multi-AI models

One selector still owns both `AI_INTERACTIVE_ROUTES` and `AI_COMPLEX_ROUTES`.
GPT-OSS 20B is first for interactive requests, followed by Groq Qwen 3.8.
Groq Qwen 3.8 remains first for complex work.
Earlier routes retain their relative order. HF candidates remain outside the ranked lists.
The explicit candidate benchmark still tests all three independently.
Interactive remains 1,500 seconds overall / 250 seconds per attempt. Complex remains
2,000 seconds per saved decision. Existing correction, authority, checkpoint and outcome
validation still apply. Vision continues to use its existing transport.

## 1. Database update

Before restarting FastAPI, apply `docs/sql/0013_ai_connections_neon.sql` in Neon SQL Editor
as the migration/table-owning role. It requires `0012_ai_workloads` and adds only two JSON
identity columns. The script updates Alembic's version in the same transaction.
Alternatively, a configured migration environment can run `python -m alembic upgrade head`.
The implementation does not apply this migration to your database.

## 2. Get the credentials

Put these in **backend/.env**, never the frontend environment:

```dotenv
GROQ_API_KEY=YOUR_GROQ_KEY
HF_TOKEN=YOUR_HUGGING_FACE_TOKEN
```

- Groq: open [API Keys](https://console.groq.com/keys), create a key and copy it to
  `GROQ_API_KEY`. This key authenticates the Groq endpoint, not NVIDIA or OpenRouter.
  Check your account's free/paid plan and limits before running requests.
- Hugging Face: open [Access Tokens](https://huggingface.co/settings/tokens), create
  a fine-grained token with **Make calls to Inference Providers**, and copy it to `HF_TOKEN`.
  One HF token can authenticate both configured hosting providers. No separate Novita or
  Featherless key is needed when using HF billing through the router.
- For gated Llama access, follow the license/access instructions on its model page if required.

HF's current [pricing documentation](https://huggingface.co/docs/inference-providers/pricing)
lists **no included inference credits for free accounts**. Purchases or existing paid credits
are your decision. `billing_allowed` is explicit consent to consume those credits; it does
not purchase, upgrade or impose a monetary spending cap. Set spending limits in the hosting
account. Groq account billing is also controlled by your Groq account, not by Audoryn.

## 3. Enable the endpoint connections for benchmarking

Copy the following as **one line** into `backend/.env`. These examples permit the Groq
benchmark and leave HF blocked until you authorize billed inference:

```dotenv
AI_TEXT_CONNECTIONS={"groq":{"base_url":"https://api.groq.com/openai/v1","hosting_provider":"groq","credential_ref":"groq","schema_mode":"groq_strict","enabled":true,"data_allowed":true},"hf_featherless":{"base_url":"https://router.huggingface.co/v1","hosting_provider":"featherless-ai","credential_ref":"huggingface","enabled":true,"data_allowed":true,"billing_required":true,"billing_allowed":false},"hf_novita":{"base_url":"https://router.huggingface.co/v1","hosting_provider":"novita","credential_ref":"huggingface","enabled":true,"data_allowed":true,"billing_required":true,"billing_allowed":false}}
```

If you choose to benchmark HF using your credits, change **both** `billing_allowed` values
to `true`. `data_allowed=true` authorizes sending input to that configured endpoint and host.
The benchmark sends synthetic examples; adding the route to a live list later also sends
actual conversation/repository evidence. Review each provider's handling terms first.

You do not need to edit NVIDIA/OpenRouter credentials, either ranked list, or
`SMART_PLANNER_MODEL_PROFILES`. Built-in candidate profiles are included.

| Candidate route | Endpoint | Explicit host | API model sent |
| --- | --- | --- | --- |
| `groq:qwen/qwen3.8-27b` | `https://api.groq.com/openai/v1` | Groq | `qwen/qwen3.8-27b` |
| `hf_featherless:Qwen/Qwen2.5-7B-Instruct` | `https://router.huggingface.co/v1` | Featherless AI | `Qwen/Qwen2.5-7B-Instruct:featherless-ai` |
| `hf_novita:meta-llama/Llama-3.1-8B-Instruct` | `https://router.huggingface.co/v1` | Novita | `meta-llama/Llama-3.1-8B-Instruct:novita` |

These hosts were listed on the model pages during implementation. Availability can change:
an unavailable mapping fails visibly; there is no automatic replacement host or model.
See the HF [chat completion contract](https://huggingface.co/docs/inference-providers/tasks/chat-completion).

## 4. Run two trials per model, separately for interactive and complex

Open PowerShell:

```powershell
cd C:\Users\divin\AgentGate\backend
$env:PYTHONPATH = Join-Path $env:LOCALAPPDATA "Audoryn\python313-packages"

# No requests: inspect the three candidates and their exact endpoints/hosts.
uv run --python 3.13 --no-project python scripts/benchmark_ai_interactive.py --candidates --dry-run

# Two trials for each of the three candidates: six trials per script.
uv run --python 3.13 --no-project python scripts/benchmark_ai_interactive.py --candidates --trials 2
uv run --python 3.13 --no-project python scripts/benchmark_ai_complex.py --candidates --trials 2
```

To run Groq only while HF remains disabled/unfunded:

```powershell
uv run --python 3.13 --no-project python scripts/benchmark_ai_interactive.py --candidates --trials 2 --models "groq:qwen/qwen3.8-27b"
uv run --python 3.13 --no-project python scripts/benchmark_ai_complex.py --candidates --trials 2 --models "groq:qwen/qwen3.8-27b"
```

Scripts read `.env` in a fresh process; restarting FastAPI is not needed for the benchmark.
Each trial uses at most two inference calls including one correction; no cross-model fallback
occurs within a trial. HF hosts share the same token's cooldown/account failure handling.
Outputs go to `.local/benchmarks/<kind>-<timestamp>/report.json` and `summary.csv`, updated
after every finished trial. Exact endpoint, hosting provider, requested model, compatibility
profile, timings, calls and validation results are recorded. No generated code is executed.
Inspect the generated answers as well as latency: JSON validity does not establish correct code.
No database, GitHub, sandbox, worker execution or model order changes occur in a benchmark.

## 5. Adding another compatible endpoint later

Add a named connection in `AI_TEXT_CONNECTIONS`, its secret under `AI_TEXT_API_KEYS`, and
a `connection:model` compatibility entry under `SMART_PLANNER_MODEL_PROFILES`. The supported
format is `openai_chat`; no new dependency or runtime selector is needed.
Do not use `auto`, `fastest`, or `cheapest` as a hosting provider. HF model IDs must have no
suffix in the selection list; the transport appends the explicit host from the connection.
Use backend-controlled, public HTTPS endpoints without query credentials or redirects.
Connection identifiers must not shadow the reserved `nvidia_nim` / `openrouter` transports.

Endpoint schema modes are `prompt_json`, `json_object`, `json_schema`, and `groq_strict`.
Only select native schema modes verified for that endpoint/model. Groq strict mode uses a
conservative supported subset: closed objects and all fields required. Unsupported schemas
fall back to JSON-object output **without changing the local schema or action authority**.
Native Groq Structured Outputs disable streaming, as documented by
[Groq](https://console.groq.com/docs/structured-outputs). HF candidates default to prompt JSON
and non-streaming until their specific hosting endpoints are qualified.

Saved decisions snapshot the exact endpoint, host, model and profile. Attempts and invocations
store non-secret transport identity; credentials are resolved only at execution. Existing
pre-migration records keep empty identity rather than fabricated historical endpoints.
Operational logs include endpoint/model identity but never authorization headers, private
reasoning or raw error bodies. No Instructor or extra retry loop was introduced.
