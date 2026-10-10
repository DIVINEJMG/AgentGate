# AgentGate pre-commit corrections

## Scope

This review fixes the reported verification failures and excludes generated output from Git. It does not change Console, active environment files, database schemas, deployment, or public-site design. The subsequent authorized atomic-commit preparation stages the complete AgentGate changes on `codex/agentgate-complete-integration`; pushing remains a separate user action.

## Causes and corrections

| Finding | Cause | Correction |
| --- | --- | --- |
| Six production type errors | Repeated dictionary lookups prevented reliable narrowing of nullable runtime evidence. | Capture the value once, check its type, and retain the existing queued/retrying response. Five malformed/evidence cases exercise this response. |
| Test type errors | Partial mocks were passed as nominal database/identity types; browser and AI fakes lacked newly introduced protocol parameters; optional results were used before checking them. | Explicit casts at partial-mock injection boundaries, complete fake method signatures, model validation for serialized drafts, and stronger shape/presence assertions. No type-check exclusion or blanket `Any` was introduced. |
| Four GitHub test failures | Historical F26/F29 scenarios inherited enabled local expansion/foundation flags. Expanded providers correctly required stronger approval/evidence. | Select legacy providers explicitly in legacy contract tests and scope the authorization flag to its legacy scenario. Add a foundation test proving an allow guard cannot bypass manifest approval. Expanded provider tests remain enabled and unchanged in purpose. |
| Frontend suite hung | The CMS before-hook used a Vite development server and SSR module-runner transport. | Compile one in-memory test bundle, resolve external packages to file URLs to share React identity, and retain every CMS test, including all nine baseline comparisons. No watcher or persistent test service is needed. |
| Architecture checks failed | Obsolete provider file, deleted worker profile, old UI labels, and old planner/structured-output source shapes. | Check the current package, routes, rejection handler, bounded repair loop, and acknowledged background planning. Keep the 2,000-second planner and 600-second initialization budgets. |
| Two architecture checks timed out | Recursive traversal visited installed dependencies and generated output. | Enumerate tracked plus nonignored untracked Git candidates. A temporary-repository regression proves tracked files remain scanned even after matching an ignore rule. |
| Frontend secret-boundary check | A readiness message displayed a backend secret-setting identifier. | Show an administrator-facing secure-storage readiness message while retaining the connection gate. |
| Production dependency audit | Installed PDF and spreadsheet versions had published advisories. | Approved updates: jsPDF 4.2.1 and SheetJS 0.20.3 from its official distribution, pinned with lockfile integrity. Export regressions check PDF pages/footer APIs and spreadsheet round-trip data. |
| Whitespace and formatting | Import ordering, required execution formatting, and one trailing-space line. | Apply import ordering and the existing CI formatting scope; remove the trailing space. |

SheetJS's package download uses its official distribution host during installation. This introduces no runtime CDN, CMS delivery service, or additional production dependency. References: [SheetJS installation](https://docs.sheetjs.com/docs/getting-started/installation/frameworks/) and [jsPDF patched advisory](https://github.com/parallax/jsPDF/security/advisories/GHSA-wfv2-pwc8-crg5).

## Changed files

- Production corrections: `backend/app/api/jobs_routes.py`, the heartbeat interval annotation in `backend/app/application/services/ai_gateway.py`, and `frontend/src/components/GitHubConnection.tsx`.
- Existing CI formatting: 31 files in the execution formatting scope; import ordering in backend source/tests.
- Test fixture corrections: the diagnosed backend type-error files, `backend/tests/legacy_execution.py`, and `backend/tests/test_repository_verification_files.py`.
- Frontend verification: `frontend/tests/publicCms.test.mjs`, `frontend/tests/resultExportDependencies.test.mjs`.
- Dependencies: `frontend/package.json`, `frontend/package-lock.json`.
- Architecture checks: `scripts/repository_files.py`, `verify_f27_boundaries.py`, `verify_no_legacy_platform.py`, `verify_f29_0_10.py`, `verify_f29_31_40.py`, `verify_f30_11_20.py`, `verify_f31_0_10.py`, `verify_f31_11_20.py`, `verify_f31_31_40.py`, `verify_qstash_runtime_cutover.py`.
- Whitespace only: `frontend/src/components/ConnectionsStages.tsx`.
- `.gitignore` and this report.

## Generated output policy

Ignore pytest cache/temp variants, frontend browser artifact directories, Playwright reports/results, coverage output and TypeScript build metadata. Preserve source fixtures, media, content seeds, contract files and reviewed documentation. Existing ignored files are retained on disk. Ignore rules do not remove already tracked files from a commit.

After the rules, 854 existing tracked/nonignored candidates were scanned. The two credential-pattern matches were the previously reviewed dummy private-key redaction fixtures; no credential values were printed. This is pattern-based evidence, not a universal secret guarantee.

## Verification

Confirmed results:

- Ruff full source/test/migration check: passed.
- Both CI formatting checks: passed.
- Full Pyright: zero errors and warnings; the subsequently added repository-scanner test also passed a scoped Pyright check.
- Focused backend checks, including signed Console read contracts: 186 passed.
- Repository-scanner regression: passed in an isolated temporary repository.
- Frontend Node tests: 89 passed.
- Frontend TypeScript: passed.
- Client and public SSR production builds: passed.
- Production dependency audit: zero vulnerabilities.
- All 29 backend CI architecture/security scripts: passed, including offline migration SQL generation. No database migration was applied.
- `git diff --check`: passed.
- Full backend sequential verification with current feature flags: 1,144 passed, 19 warnings, 133.81 seconds.
- Full backend sequential verification with CI feature defaults: 1,144 passed, 19 warnings, 167.29 seconds.
- Staged review: whitespace normalized in seven newly added files with identical non-whitespace tokens; two dummy private-key fixtures explicitly labelled without changing assertions or scanner rules. Staged secret hygiene and whitespace checks passed; focused Ruff passed and 74 fixture regressions passed.

Local runtimes: Python 3.13.15 and Node 24.16.0. CI specifies Node 22; it was not run locally. Existing large-chunk and dependency deprecation warnings remain.

The initial full run hit Windows access denial on an existing pytest temp directory. Verification uses a newly allocated `--basetemp`; it does not modify permissions or clean that existing directory. Concurrent full runs each passed 1,143 tests and failed the same real-browser observation test. That test passed alone and the sequential full suite passed. Resource contention is a likely explanation; the exact concurrent observation failure was not instrumented. No timeout or assertion was weakened.

### PR 119 CI follow-up

The first Linux CI run passed frontend and deployment contracts, backend lint/formatting and Pyright, but reported 1,143 backend tests passed and one failed. The local test runtime rewrote `/workspace` and then `/tmp/`, inadvertently rewriting the already inserted Linux temporary path. The correction uses one substitution pass and preserves the native workspace path. Production code and assertions remain unchanged. Three regression cases cover Linux, Windows and apostrophes; the focused initialization/manifest/recovery suite passed all 37 tests, with focused Ruff, Pyright and whitespace checks passing. The corrected Linux CI result is pending the amended commit push.

### Reproduce in PowerShell

Use the repository's Python 3.13 environment, then run from `backend`:

```powershell
ruff check app tests migrations
ruff format --check app/application/services/data_migration.py app/application/services/cutover_gate.py tests/test_f28_21_26.py
ruff format --check app/execution app/integrations/builtin.py app/api/integration_capability_routes.py tests/test_f29_0_10.py
pyright
$testBase = Join-Path $env:TEMP ('agentgate-check-' + [guid]::NewGuid().ToString())
pytest --basetemp $testBase
```

Run the architecture/security commands listed in `.github/workflows/ci.yml` from `backend`, sequentially. From `frontend`:

```powershell
npm test
npm run typecheck
npm run build
npm audit --omit=dev --audit-level=high
```

Run `git diff --check` from the repository root. These checks do not establish live deployment, migration application, or production end-to-end acceptance.
