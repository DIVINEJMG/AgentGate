# Worker web research

Conversation requests for current public information use `web.research`. A command is
committed with an initial conversation reply, then the outbox publishes it to the
QStash-signed `/internal/v1/web-research/execute` callback. The callback appends a
second worker reply with inspected sources. The ordinary outbox recovery drain
requeues accepted research commands older than three minutes; command completion
and reply insertion are idempotent.

Set `BRAVE_SEARCH_API_KEY` and `TAVILY_SEARCH_API_KEY` in the backend environment.
Brave is attempted first and Tavily is the fallback. When neither key is present,
the command reports missing integration. `QSTASH_WEB_RESEARCH_URL` may override the
callback URL; otherwise the callback is derived from the HTTPS host of
`QSTASH_RUNTIME_EXECUTE_URL`. The signed callback and normal QStash outbox drain
must both be reachable. Do not put keys in Git.

Each request is limited to two search providers, four candidate page visits, and
three inspected pages. Redis enforces per-organization rolling hourly and daily
search reservations, including named-site lookup during worker creation. The
conversation command also checks its own PostgreSQL hourly and daily admission
count. Redis also caps each provider at 30 calls per minute by default. Provider
rate limits, timeouts, inaccessible pages, and verification pages
are recorded in the command receipt. An explicit required source is never silently
replaced.

Search results grant no standing origin authority. Result pages are fetched as
public HTTP(S) under a temporary exact-origin read policy; redirects are checked
again and private, special, and unresolved DNS destinations are blocked. No login,
form submission, or provider write tool is part of `web.research`. Page text is
passed to the model as untrusted evidence. A source date supplied by search is
labelled as a search-index date, not verified publication metadata.

Worker creation independently extracts site mentions from the user's text and
checks the draft's per-job assignments. Explicit URLs become exact job origins.
Named sites are checked with the configured search providers; ambiguous sites ask
for a URL before authority is granted. An explicit open-web research instruction
grants standing read-only `web.research` authority to the relevant managed jobs.
A later conversation request grants only that request.

The local `.env` currently needs real search-provider keys for live verification.
Run focused tests with `python -m pytest -q tests/test_web_research_flow.py` from
`backend/`. A live acceptance check should create one two-site worker, confirm its
job's `authorizedBrowserOrigins`, run it through both sites, then ask that worker
for a current public fact and verify the final conversation reply contains
inspected source URLs and dates. Use a test organization to avoid shared production
work items.
