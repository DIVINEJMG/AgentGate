# Plan: fixes from the Oracle live check and the browser setup review

Written 15 October 2026. The findings are in `docs/testing/simulation-findings.md` (F28–F37).
All the work stays on the branch `fix/rejected-approval-finishes` (which already holds the F28 fix)
and is committed once, at the end, after the whole plan is proved.

## 1. Goal

- Every finding from the Oracle live check and the browser setup review is fixed.
- The browser connection works end to end: setting it up, checking it, signing in and running a
  task from a saved job or from chat.
- Nothing that already works changes behaviour:
  - the existing browser job path (for example the "Aud" worker's daily readout);
  - the site, path and network rules;
  - GitHub tasks.

## 2. The findings

| # | Finding | Severity | Phase |
|---|---|---|---|
| F28 | A rejected approval leaves its task waiting (fixed, not committed) | Medium | done |
| F30 | The Activity page crashes on a runtime action | High | A |
| F31 | A merge GitHub carried out is reported as failed; the cause is not logged | High | A |
| F33 | A cut-off planner answer moves straight to the fallback model | Medium | A |
| F32 | Empty reads all say "Found nothing: the result is empty" | Low | A |
| F34 | Browser "Live checks" never open the site or try the login | High | B |
| F35 | The planner is not told which login fields are stored | High | B |
| F36 | A one-time-code secret is suggested but never turned into a code | Medium | B |
| F37 | A job for a login site is created without the sign-in actions | High | B |
| F29 | A one-off browser task from chat asks for a GitHub account | High | C |

## 3. Phase A: correctness and visibility (small, backend and one frontend fix)

**F30, Activity page crash**
- The API returns a complete policy for every action:
  - `winningPolicy: null` and `matchedPolicies: []` when the runtime did not record them
    (`governance_routes._action_record`);
  - the same for approvals.
- The page treats a missing list as empty, so an older record can never crash it again.
- Find the 404 in the same console log while the page is open locally; fix it if it is ours.
- **Proof:**
  - an API test with a runtime-recorded action;
  - a frontend model test;
  - the page opened locally on a runtime action.

**F31, a write that happened reported as failed**
- **Log the cause:** when a provider turns an error into a safe message, log the original
  error's type and the AgentGate lines it passed through (`app_frames`), never values. This
  covers GitHub and the other native providers at their shared error point.
- **Treat it as uncertain, not failed:**
  - An error raised **after** a write request was sent becomes "outcome uncertain" (as a
    failed verification already does), not "failed". The provider records when the write was
    sent.
  - The existing re-check then runs (merges already have one: `merged_sha`), and the person
    sees It happened / It didn't happen / Check again.
  - An error **before** the write is sent stays a plain failure.
- **Proof:**
  - a test where verification raises a missing-key error after the merge: the outcome is
    uncertain, and the re-check finds the pull request merged;
  - a test where the same error before sending stays a failure;
  - the log line names the error type and the lines.

**F33, a cut-off planner answer**
- Record the finish reason (a fixed code such as `none`, `tool_calls` or `length`, never text)
  on the planner attempt's failure details. That table already holds JSON, so no migration.
- An answer that ended with no finish reason at all is a transport fault: ask the same model
  once more before falling back. A refusal or a length cut-off behaves as today.
- **Proof:** streaming tests for each finish reason: retried once on the same model,
  recorded, then the fallback.

**F32, "Found nothing"**
- The step narrator names what was empty ("No reviews", "No check runs") from the operation.
- It keeps a state the record carries ("Status pending; no checks reported").
- **Proof:** narrator tests for the three reads from the merge task.

**Status (15 October): Phase A done.**
- **Tests:** `tests/test_live_check_fixes.py`.
- **Proved in the simulator:**
  - F30 on its 133 runtime actions;
  - F31 with a GitHub write whose response lost a field (uncertain, re-checked, completed);
  - F33 with a dropped stream (asked again, accepted, no fallback);
  - F28 (rejected tasks closed by the sweep).
- **New simulator tools:** the `missing_field` GitHub fault, `"finish_reason": "none"` answers,
  and `sim_replay --faults` and `--cut-planning`.
- **Split while changed:** the stream reader (`collect_response`) and NVIDIA's `_invoke`.

## 4. Phase B: the browser connection end to end

**F34, real live checks**
- **The connection check opens the start page** in a short browser session, through the same
  site, path and network rules as a task. It reports:
  - the page it reached (title and final address);
  - whether that address is inside the allowed sites;
  - how long it took.

  A site that cannot be reached, or a start page the rules block, fails the check with a plain
  reason.
- **When a login is stored,** the check can also try it (decision D1). It fills the stored
  fields, submits, and reports whether the page changed from the login page. It shows no values
  and keeps the session for nothing else.
- **The setup's "Live checks" step** shows these real results instead of "Connected" alone.
- **Proof:**
  - tests with the existing test browser runtime: reachable, unreachable, blocked by the rules,
    login accepted, login refused;
  - a live check on Oracle against a site you choose.

**F35, login field names for the planner**
- The planner sees, with the browser tools, the names of the stored login fields (for example
  `username`, `password`, `company`), never their values.
- `browser.auth.login` refuses a binding to a field that is not stored, before anything is typed.
- **Proof:**
  - a planner prompt test (the names are there, the values are not);
  - an execution test for an unknown field.

**F36, one-time codes** (decision D2)
- A stored `otpSecret` (or another field marked as one) produces the current six-digit
  time-based code at the browser when a binding asks for it.
- The standard algorithm (RFC 6238) is written with the Python standard library, so no new
  dependency.
- The code, like the secret, never reaches the model and is hidden from page reads.
- **Proof:** tests with the RFC's published test values; a login test with a code field.

**F37, sign-in actions for login sites**
- When a worker or job is drafted, it is told which browser connections have a stored login
  (names only).
- A job whose work starts behind that login gets the sign-in action, plus typing and submit
  where the task needs them. Approval follows the existing policies; submitting already asks
  first.
- The job's page lists the browser actions the job has, so a missing one is visible.
- **Proof:**
  - a drafting test: a login connection plus "read the inquiries in the dashboard" gives a job
    with `browser.auth.login`;
  - re-running the Bestworth-style task on a test site reaches past the login.

**Status (15 October): Phase B done.**
- **Tests:** `tests/test_live_check_fixes.py` (40 in all).
- **Proved in the simulator** (the simulator now serves a test site with a plain login and a
  two-step login with a code page; real Chromium):
  - the live check and Test sign-in (F34);
  - `loginFields` recorded on both connections (F35);
  - a one-time-code sign-in that passes with the right secret, and fails plainly with a wrong
    secret or none (F36);
  - the resolver shown the real connection's stored login (F37).
- **Still to prove:** a whole job run behind the login (the Bestworth-style task). It needs a
  scripted model scenario, so it moves to the final proof, or a live check on Oracle.
- **Found while proving, and fixed:** F38, where after a form submit the next page was read
  while still loading.
- **Smart context:** only the sign-in tool carries `loginFields`, so no other planner tool grows.
- **Split while changed:** the browser provider's `_execute_operation` (sign-in, upload and
  download moved out) and the resolver's `resolve` (`_connected`, `_select`).

## 5. Phase C: browser tasks from chat (F29)

- **Today:** a one-off chat request goes to task preparation, which only knows the newer
  native integrations; browser connections are deliberately outside that system.
- **Fix (decision D3):** when interpretation recognises a one-off browser task for an existing
  worker, it is carried out through the path that already reaches the browser. That is the
  existing job path ("add a job to this worker" sets up browser access the same way the Aud
  worker got it), created as a one-time job that starts now. The person sees the same messages
  and result in the chat.
- **Never ask for an account of a provider the person did not name:** a preparation that ends
  with an integration the request did not mention is refused and explained instead.
- **Proof:**
  - a conversation test: the Bestworth message creates a one-time browser job that starts, and
    asks for no GitHub account;
  - a simulation replay against a test site;
  - the live check on Oracle.

**Status (16 October): Phase C done.** It came with F40 (your own file to upload), downloaded
files in chat, and two fixes found while proving it:
- F43: AI-managed browser work now uses the connection the person set up for the sites their
  message names. A private-network connection is used only for a site they named.
- F44: browser steps are described correctly.
- **Proved in the simulator:** scenario s8 (see the findings). A chat request became a one-time
  job; the run used the person's connection, downloaded report.csv, uploaded the attached file
  ("Received stock-update.csv (237 bytes)") and finished. The chat result carries report.csv as a
  file to open.
- **Still to prove on Oracle:** the Bestworth request from chat, and a browser job behind a login.

## 6. How everything is proved

- **Every fix:** its own tests, the full backend suite run as CI runs it (no `.env`), the
  frontend tests, typecheck and build, ruff, the format checks, pyright, the changed-code
  quality guard and every CI verify script.
- **Simulation replays:**
  - scenarios 1 and 3 again (nothing that worked changes);
  - a new browser scenario against a controlled test site, with and without a login.
- **Oracle, at the end and only with your go-ahead:**
  1. the Bestworth browser request from chat;
  2. a browser job behind a login;
  3. a merge (the outcome is now correct, or uncertain with It happened);
  4. the Activity page;
  5. a browser connection set up from Connections with its live checks.

## 7. Dependencies, environment and database (to be kept up to date)

**Expected:**
- no new dependency (the one-time codes use the standard library);
- no environment change;
- no migration (the finish reason goes in the existing JSON failure details; policy fields are
  filled in when records are read).

If any of this changes during the work, it is written here, in the commit message and in the
handoff.

## 8. Decisions needed from you

| # | Question | Recommended |
|---|---|---|
| D1 | Should the connection check actually try the stored login? It signs in to the real site once. | Yes, as a separate **Test sign-in** button in the live checks, not automatic. |
| D2 | One-time codes: build them (standard library, no new dependency), or remove the example for now? | Build them. |
| D3 | A one-off browser task from chat: run it as a one-time job of the worker (the path that already works), or bring browser connections into the newer integration system? | One-time job: much smaller, reuses what works. The bigger move can come later. |

**Decided (15 October):** D1 yes, as a **Test sign-in** button; D2 build the one-time codes;
D3 run a chat browser task as a one-time job of the worker.

## 9. Order and size

1. **Phase A:** four small fixes. They fix wrong outcomes and the crash first.
2. **Phase B:** the browser connection, the largest part: live checks, login field names,
   one-time codes, sign-in actions.
3. **Phase C:** chat browser tasks; it needs Phase B's sign-in work for login sites.
4. **Proof, then one commit** (F28–F37), the push commands, and an Oracle handoff with the
   release notes.
