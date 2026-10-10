# Smart Multi-AI operations

## Boundary and rollout

Smart Multi-AI uses one shared catalog and two backend-selected workloads. Vision retains its existing route; the feature defaults off. Migration 0012 is required for durable preparation.

Interactive chat and initial interpretation use GPT-OSS 20B, Nemotron Ultra, North Mini Code, GLM 5.3, Laguna XS and Kimi K3. Complex worker drafting, tool/capability resolution, integration preparation, background research and execution planning use Nemotron Ultra, Kimi K3, GPT-OSS 20B and Laguna XS. These benchmark-derived lists are provisional, not guarantees of code correctness.

AI_INTERACTIVE_ROUTES and AI_COMPLEX_ROUTES select inference order. SMART_PLANNER_ROUTES remains the complete catalog, including unqualified models. Explicit inclusion in a workload list qualifies a model for that workload; readiness presents both lists and exclusions. New models require a compatible profile and explicit inclusion. Model roles describe output format; the backend selects workload by operation. Repository text cannot select budgets or grant authority.

OpenRouter requires a separate OPENROUTER_API_KEY, explicit OPENROUTER_PLANNER_DATA_ALLOWED consent and a nonempty OPENROUTER_ALLOWED_PROVIDERS endpoint list. Provider data collection is denied and endpoint fallback is disabled. The six original free models remain configured. Additional free models using the same API can be added through a route and validated compatibility profile; paid substitutions remain rejected. Secrets never enter prompts or public diagnostics. Changes to credentials and production configuration require separate authorization.

## Budgets and recovery

Complex decisions receive 2,000 seconds from saved preparation, including catalog/tool work, inference, correction and recovery waits. Interactive requests retain 1,500 seconds overall and 250 seconds per model attempt, including correction. No inactivity watchdog is added. Complex attempts may consume the remaining saved deadline. Explicit upstream HTTP 408/504 failures switch models; overall expiry or unclassified runtime transport-wait expiry pauses the decision. Interactive attempt expiry switches while budget remains.

Initial inference and one correction share the attempt budget. No same-model transport retry occurs inside an attempt. At most two transient recovery rounds use 15/30-second backoff and provider timing within the original deadline. Safety refusal, cancellation and daily quota exhaustion do not trigger cycling.

Both workloads share total organization concurrency/request/token ceilings. Defaults reserve one of two slots, five of twenty minute requests and 500,000 of 50,000,000 daily token units for interactive requests. Background counters limit background consumption; they do not create extra global allowance. Account concurrency defaults to four across workloads and organizations, with one slot reserved for interactive requests. Both paths share credential health identity. Reservations must be smaller than total ceilings to admit background work.

Request and token reservations are admitted atomically across shared/background counters.
After inference, reported total usage (or prompt plus completion usage) replaces the token
reservation, including usage above the estimate. Before-dispatch failures refund both request
and token reservations. Unknown usage after dispatch retains the conservative bound; these
counters are admission units, not billing totals. Settlement is idempotent and preserves counter
expiry. Request counters include their minute identity so late refunds cannot alter a new window.
Logs include `model_called`, budget scope, reserved/charged units and usage basis. A local-budget
block is reported as a workspace limit, while provider account quota failures permit independent
eligible account fallback. Existing counters are preserved when increasing the configured limit.

PostgreSQL is authoritative for decisions and attempts; existing runtime leases and decision generations prevent stale responses from being accepted. Accepted proposals can be reused only for the same context fingerprint, and remain subject to current execution authority. Completed actions and their action identities are preserved. Never restart a workflow to recover inference.

Database rollback cannot undo GitHub writes or messages. Uncertain writes wait for adapter reconciliation before replay. Compensation requires a supported, explicitly granted capability and any required approval. Unsupported rollback is a blocker, not a successful reversal. Browser sessions and expired coding workspaces require fresh observation or supported restoration.

## Readiness, diagnostics and evidence

GET /api/v1/organizations/{id}/planner/readiness (also v2) requires integrations.manage. It reports configuration and refreshes the public endpoint catalog into a five-minute Redis cache keyed by model compatibility configuration. The additive modelList presents the shared catalog with workload qualification and readiness/exclusion reasons; workloads exposes both ordered lists. Configuration readiness does not establish live inference availability. Execution requires compatible readiness; an expired cache is refreshed lazily within the attempt deadline, and catalog failure fails closed. Refresh readiness before starting live acceptance. No inference or external write is performed by readiness.

GET /api/v1/organizations/{id}/runs/{run}/planner-attempts (also v2) uses the same administrative permission, returns tenant-scoped metadata only and does not expose prompts. Run contracts add planning status, attempt count, recovery round/mode and reason. Per-attempt records separate transport success, schema validity and accepted decisions; original invocation telemetry remains compatible. Public worker messages use safe templates and the originating conversation, with durable notification deduplication.

Smart prompts retain full required structured evidence instead of legacy prompt clipping. Conservative per-model context bounds reject requests that cannot fit. The planner must use authorized observation/read tools to obtain missing evidence; it cannot treat an omitted result as verified. Repository and page text remain untrusted.

## Verification and live gate

Run deterministic switching/recovery and existing runtime/browser/integration tests, Ruff, Pyright, frontend type checks/tests/build and migration upgrade/downgrade on an isolated PostgreSQL database. Offline migration compilation is useful but does not establish a live upgrade/downgrade.

Live checks require separately approved credentials and data-handling settings. Begin with read-only inference and injected failures; verify the same saved decision resumes and only one proposal is accepted. Live GitHub writes require their own explicit authorization. Mocked tests do not prove free endpoint availability, privacy guarantees or production reliability.


## Twelve-model configuration and future additions

Put these entries in `backend/.env`, one assignment per line. Edit existing entries instead of creating duplicates. Keep actual keys only in the ignored local file, never in `.env.example` or chat.

```dotenv
AI_COORDINATOR_API_KEY=YOUR_NVIDIA_API_KEY
AI_COORDINATOR_MODEL=nvidia/nemotron-3-ultra-550b-a55b
AI_PROVIDER_BASE_URL=https://integrate.api.nvidia.com/v1
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
SMART_PLANNER_ENABLED=false
SMART_PLANNER_ATTEMPT_SECONDS=2000
RUNTIME_PLANNER_TIMEOUT_SECONDS=2000
SMART_PLANNER_OUTPUT_TOKENS=1800
SMART_PLANNER_MODEL_PROFILES={}
OPENROUTER_PLANNER_DATA_ALLOWED=false
OPENROUTER_ALLOWED_PROVIDERS=[]
SMART_PLANNER_ROUTES=["nvidia_nim:moonshotai/kimi-k3","nvidia_nim:z-ai/glm-5.3","nvidia_nim:configured","nvidia_nim:z-ai/glm-5.3-flash","openrouter:nvidia/nemotron-3-super-120b-a12b:free","nvidia_nim:poolside/laguna-xs-2.1","openrouter:cohere/north-mini-code:free","openrouter:google/gemma-4-31b-it:free","openrouter:nvidia/nemotron-3.5-lightning:free","openrouter:google/gemma-4-26b-a4b-it:free","nvidia_nim:openai/gpt-oss-20b","openrouter:thinkingmachines/inkling:free"]
```

One NVIDIA key covers the six NVIDIA-hosted entries, including the original model. One OpenRouter key covers the six OpenRouter entries. No per-model keys are required. Vision credentials and routes stay separate and unchanged. `configured` resolves the original coordinator ID when the decision is created; the two workload lists select text models; vision stays separate.

The empty endpoint list intentionally disables OpenRouter until the operator chooses permitted hosting endpoints. Obtain exact endpoint identifiers from each model's OpenRouter provider page/API, assess its data policy, and add the approved identifiers as a JSON array. This controls permitted hosts, not model order. Set `OPENROUTER_PLANNER_DATA_ALLOWED=true` only after approving transmission of task/repository content. Existing restrictions denying provider data collection and hidden endpoint fallback remain enforced. A free model may have no currently eligible endpoint.

All twelve built-in compatibility profiles are supplied in code. No overrides are needed for them. For a future supported-format model, add its `connection:model` string to the order and supply a profile, for example:

```dotenv
SMART_PLANNER_MODEL_PROFILES={"openrouter:example/future-model:free":{"context_tokens":131072,"generation_tokens":8192,"output_format":"prompt_json","request_parameters":{}}}
```

Replace the example with a real model and documented limits. Supported output formats are `prompt_json`, `json_object`, and `json_schema`; local JSON/action validation always applies. Profiles only permit documented reasoning/template/top-p parameters; they cannot override messages, model identity, routing, credentials or authority. An unknown profile or unsupported adapter is unavailable, never presumed compatible.

Compatibility settings are snapshotted with new decisions along with order. Existing pending decisions keep their saved sequence; pre-existing decisions lacking a profile use current compatibility configuration. Changes to model order apply to new decisions. Kimi/GLM receive appropriate reasoning controls rather than the universal Nemotron thinking-disable flag. Their larger generation allowance accommodates reasoning; the shared planner final-output target remains 1,800 tokens. Providers do not expose reasoning content as task evidence. Token admission reserves the larger generation allowance.

The 2,000-second persisted decision limit includes preparation, inference and recovery. SMART_PLANNER_ATTEMPT_SECONDS is retained for old environment compatibility but no longer limits attempts. Diagnostics record failed, unavailable and untried counts and a deadline reason; deadline exhaustion never claims every model failed. NVIDIA account-wide 429 cooldowns skip sibling models and preserve the decision for an independent eligible connection.

Before enabling, run deterministic compatibility/recovery tests, scoped lint/type checks and existing browser/GitHub regressions. Migration 0012 is required for durable preparation and preserves runtime decisions. Complete isolated migration checks and authorized read-only live model checks, then change `SMART_PLANNER_ENABLED=true` and restart the backend. No live availability, production reliability or private-data handling is established by mocked tests.


### Finding the OpenRouter endpoint identifiers

This read-only PowerShell command lists the current hosting tags for the six models. It needs no API key and does not send repository content. A missing model or empty list means it is currently unavailable through that lookup, not that another model should be silently substituted.

```powershell
$plannerModels = @(
  'google/gemma-4-26b-a4b-it:free',
  'google/gemma-4-31b-it:free',
  'nvidia/nemotron-3.5-lightning:free',
  'nvidia/nemotron-3-super-120b-a12b:free',
  'cohere/north-mini-code:free',
  'thinkingmachines/inkling:free'
)
foreach ($plannerModel in $plannerModels) {
  try {
    $catalog = Invoke-RestMethod -Uri "https://openrouter.ai/api/v1/models/$plannerModel/endpoints" -TimeoutSec 15
    $endpointTags = @($catalog.data.endpoints | ForEach-Object { $_.tag } | Sort-Object -Unique)
    Write-Output "$plannerModel : $($endpointTags -join ', ')"
  } catch {
    Write-Output "$plannerModel : endpoint lookup unavailable"
  }
}
```

After reviewing those hosts and their data policies, put the approved tags into `OPENROUTER_ALLOWED_PROVIDERS` as a JSON array of quoted strings. For example, `["google-ai-studio"]` permits that host only; it does not enable all six models. Do not put model IDs or API keys into this list. Restart after configuration changes and inspect administrator planner readiness before enabling inference.


## Admission and diagnostics

### Conversation work activity

Accepted tasks have a separate live activity panel; ordinary chat messages and results remain intact.
`GET /api/v1/organizations/{id}/conversations/{thread}/activity` (also v2, same response shape)
requires the conversation's tenant and `workforce.read`. Work-item lookup uses its exact originating
conversation, including explicitly queued existing jobs. Commands additionally require `jobs.read`
and current connection access. No prompts, reasoning, action payloads, file contents or raw runtime
errors are published. Command previews are credential-redacted and bounded to the last 2,000 characters
per output stream; they identify their observation time and actual exit status.

The panel shows preparation, planner wait, selected/executing action, commands/tests, publication,
recovery and terminal outcomes. It separates verified objects from unverified outcomes. Times distinguish
last saved activity from the latest UI check. A deadline elapsed before recovery is recorded is shown
truthfully; it does not invent a successful result or restart execution.

Realtime notifications refresh the snapshot; independent ten-second polling recovers missed events.
Refresh failures retain the last snapshot, abort after twenty seconds and retry. A task failure does
not terminate the activity feed. Conversation changes cancel outstanding requests and discard late
responses. Existing chat delivery includes the conversation SSE event as well as WebSocket delivery.

Smart planner adapters stream supported responses privately. Only received byte counts, call identity
and arrival times are cached in Redis (at most once per ten seconds, with a bounded TTL). The UI can
report response data arrival without disclosing generated content. Incomplete streams and refusals
never authorize an action. Metadata-cache failure cannot fail inference. A model profile may set
`streaming: false` for an endpoint without compatible streaming; it then shows ordinary response wait.
Vision keeps its existing behavior; initial text requests now use ordered fallback as described below.

Tool preparation reuses agent, resource, capability profile and enabled-policy reads inside one pass.
Each scope is still evaluated. The snapshot is discarded afterward; execution and approval revalidation
use fresh reads. No authorization result is cached across decisions or tasks. Durable workload preparation requires migration 0012.

Organization-wide limits stop the selection sequence; another model or account cannot bypass them. Daily allowance exhaustion does not schedule another recovery round. A temporary organization request limit waits within the same saved deadline and bounded recovery rounds. Account-level quotas still permit an independent configured account where eligible. No budget counter is silently reset.

Logs distinguish `Planner selection started`, `Planner inference started`, `Planner not called` (admission/setup rejection), and actual model failures. Cancellation telemetry records `timeout` when the saved deadline elapsed, otherwise `cancelled`, without swallowing cancellation. Preparation reports catalog time, policy-check count/time and slowest policy, memory and observation times. Policy evaluation remains serial on the shared database session; these measurements identify where a separately verified optimization is needed.

As verified during local setup on 2026-10-07, the current hosting tags for the six OpenRouter models are: Gemma 26B/31B = `google-ai-studio`; Nemotron Lightning = `nvidia/nvfp4`; Nemotron Super = `nvidia`; North Mini Code = `cohere`; Inkling = `thinkingmachines/nvfp4`. If these are the hosts you approve, use:

```dotenv
OPENROUTER_ALLOWED_PROVIDERS=["google-ai-studio","nvidia/nvfp4","nvidia","cohere","thinkingmachines/nvfp4"]
SMART_PLANNER_ORG_TOKENS_PER_DAY=50000000
```

Tags may change; rerun the public endpoint lookup above when needed. This permits those hosts but does not guarantee live availability or eligibility under data-collection restrictions. Provider credentials and privacy restrictions remain unchanged.

## First-request fallback and process placement

Initial text interpretation no longer depends on one successful coordinator response before fallback starts. Each interactive request snapshots its configured order in memory; background task and worker preparation persist their complex order in PostgreSQL. Each path and uses the same evidence, schemas and authority boundaries across eligible models. There are at most two structured inference calls per model (initial plus schema correction) and no same-model transport retry. `AI_INTERACTIVE_TIMEOUT_SECONDS=1500` sets one request-scoped deadline, shared across interactive interpretation and reply, including intervening work. `AI_INTERACTIVE_ATTEMPT_SECONDS=250` caps each model attempt, including correction. A timed-out interactive attempt switches to the next eligible model only while the shared deadline has time remaining. Overall deadline expiry stops the request and reports untried models. These are elapsed-time limits, with no additional stall or inactivity watchdog. Interactive responses remain non-streamed; FastAPI still reports periodic waiting and classified failures. Invocation telemetry records each actually called model and attempt timeouts. These interpretation calls do not execute provider actions and do not create managed-runtime decision records; accepted complex work retains its 2,000-second durable decision/checkpoint mechanism without the interactive per-model cap.

First-request admission shares organization concurrency, request/token limits and account/model cooldowns with managed planning. Interactive capacity is reserved at both organization and shared account concurrency boundaries. Account credential problems skip that account; independent accounts remain eligible. Safety refusals and organization-wide blockers stop the sequence. OpenRouter data consent, allowed hosts and free-model restrictions still apply. A shared coordination outage is a setup blocker, not evidence that each model failed. Deadline exhaustion reports untried models honestly. Disabling Smart Multi-AI restores the single-model text path.

FastAPI retains planner selection, preparation, action and recovery logs. Inference adds structured start, periodic thirty-second waiting, response/failure and end records with role, model, connection and correlation identity. Streamed response-arrival logs do not depend on Redis metadata publication succeeding. No response text, prompts, secrets or private reasoning are logged.

The conversation activity interface adds `requestMessageId` and `retryScheduled`. Active work appears in a bounded dock outside the scrollable transcript, so activity growth does not append entries beneath callback messages. Completed, paused and failed activity moves to a native, closed-by-default **Process** disclosure on its original human request. Automatically scheduled retries remain live. No activity is attached to a later worker reply or follow-up message. The endpoint includes up to 100 recent work items/setup records per conversation; older evidence remains in its job/run records. Polling continues after failures, and conversation changes discard stale snapshots.

## Ranked workload configuration

~~~dotenv
AI_INTERACTIVE_ROUTES=["nvidia_nim:openai/gpt-oss-20b","groq:qwen/qwen3.8-27b","nvidia_nim:configured","openrouter:cohere/north-mini-code:free","nvidia_nim:z-ai/glm-5.3","nvidia_nim:poolside/laguna-xs-2.1","nvidia_nim:moonshotai/kimi-k3"]
AI_COMPLEX_ROUTES=["groq:qwen/qwen3.8-27b","nvidia_nim:configured","nvidia_nim:moonshotai/kimi-k3","nvidia_nim:openai/gpt-oss-20b","nvidia_nim:poolside/laguna-xs-2.1"]
AI_INTERACTIVE_TIMEOUT_SECONDS=1500
AI_INTERACTIVE_ATTEMPT_SECONDS=250
RUNTIME_PLANNER_TIMEOUT_SECONDS=2000
AI_INTERACTIVE_RESERVED_SLOTS=1
AI_INTERACTIVE_RESERVED_REQUESTS=5
AI_INTERACTIVE_RESERVED_TOKENS=500000
AI_ACCOUNT_CONCURRENCY=4
~~~

Background integration preparation shares planner_decisions and planner_attempts with runtime planning.
A preparation decision targets its tenant-scoped command or exact worker job revision; runtime decisions target a work item/run.
The database requires exactly one target form. Preparation saves order/profiles, deadline, context/schema
fingerprint, attempts and validated draft. A Redis lease and generation check reject stale responses.
Cancellation, human instruction and membership are rechecked after inference; resource access is resolved
freshly before grants/jobs. Changed evidence invalidates cached proposals without extending deadlines or
erasing history. Preparation cannot execute provider writes.

Integration callbacks acknowledge preparation promptly. Background work runs outside the HTTP delivery
timeout. A fifteen-second recovery scan resumes accepted commands and due pending decisions after restart.
Cancelled commands, exhausted decisions and resource clarification waits do not automatically restart.
Coordination/database failures are logged. Cached drafts require matching context/schema.

Migration 0012 preserves old runtime rows/order/deadlines and adds command/job-revision/workload/purpose metadata.
Downgrade refuses to discard preparation audit records. Apply through normal migrations after isolated
database verification; production migration is not part of this implementation. New decisions get
2,000 seconds; existing ones retain their saved budgets.

FastAPI logs workload, purpose, role, model, attempts and timings without prompts/private reasoning.
Task activity stays in its originating conversation with preparation deadline/recovery status.
Interactive responses remain non-streamed. Full schema/action-input validation, authority rechecks,
uncertain-write reconciliation and truthful outcomes remain mandatory. Benchmark syntax acceptance
does not establish successful code execution or publication.


## Conversation routing and grounded replies

Explicit human retry of exhausted or expired integration preparation creates a new decision
with a fresh configured complex budget (2,000 seconds by default). Preparation sequence numbers
keep every earlier decision and its attempts under the same command. Automatic recovery does
not extend deadlines. New decisions snapshot current routes; old decisions are superseded and
their acceptance generations fenced. Already-created work items cannot restart preparation.
Chat clarification and the Continue control share this behavior. Queued receipts do not claim
inference has started; background preparation emits a started update. Attempt `failure_details`
records local budget scope, account/organization scope, model dispatch and HTTP status. Apply
`docs/sql/0014_preparation_retry_neon.sql` before restarting; it adds only the audit column.

After the interactive AI selects `conversation.answer`, ordinary replies use bounded chat
summaries rather than full execution context. No greeting classifier or text-based context
gate is applied. The model selects relevant facts and is guided to omit task facts from
greetings and unrelated replies. Interpretation and action preparation retain their own
context; compact reply context does not restrict which operation can be selected.
Status answers select relevant
backend facts labeled with the task and run; duplicate rendered facts are removed. Rejection
logs include fixed diagnostic codes such as `empty_output`, `truncated_output`, `malformed_json`,
`schema_failure` and `unsupported_claim`, never raw response text or provider error bodies.

GPT-OSS `openai/gpt-oss-20b` is first for interactive workloads, followed by Groq.
Groq `qwen/qwen3.8-27b` remains the first route for complex workloads.
These priorities reflect the user's October 8 selection; other routes retain their
relative order. A Groq connection still requires its
credential, enabled setting and data consent. Saved pending decisions keep their original
route snapshots; the new order applies to new decisions after backend restart.

The interactive model selects a supported command from the human request, conversation history and
attachment context. Examples guide natural understanding; no second classification, quote requirement,
or fixed semantic-category mapping is required. Role and workload remain backend-controlled.

Valid structured proposals need one inference. Schema failures or concrete contradictions (ordinary
chat carrying a patch, command or control payload) can use one correction within the same model
attempt. Persistent invalid output moves to the next eligible model; transport failures and refusals
receive no same-model transport retry. The existing 250-second attempt and shared 1,500-second
interactive deadline cover both calls. Context cannot grant authority; account/resource resolution,
permissions and execution validation remain downstream checks. This cannot guarantee perfect routing
of every natural-language request.

Ordinary replies also accept valid proposals directly. Execution/availability claims require recorded
backend fact IDs; previous assistant messages and incoming content are not execution evidence.
Unsupported claims trigger bounded correction/fallback. No migration or credential change is required.
