# Phase 5: policies

Written 14 October 2026, for the [improvement plan](../agentgate-improvement-plan.md).

**Owner's decision (starting policies):** reads and ordinary writes run without asking, as before.
Actions that cannot be undone need approval unless the request explicitly asked for them.

## What changed

1. **Rules in people's terms.**
   - **New targets:** besides worker, resource and scope IDs, a policy can now target a
     provider (for example `github`) and resource names by pattern (for example `acme/*`, which
     ignores case).
   - **Lasting:** these keep working when a connection or resource is re-created with a new ID.
   - **In the editor:** the Policies editor has both fields.
   - **Existing policies** match exactly as before.
2. **Every decision is explained.**
   - **Where:** each policy decision now carries one plain sentence.
   - **Examples:**
     - "Needs a person's approval because of the policy 'Review merges'."
     - "No policy allows Ada to merge on acme/api, so it is blocked by default. Add a policy, or
       use 'Always allow' on an approval, to permit it."
   - **Who sees it:** people, in the evaluation panel, which also keeps the technical reason
     underneath.
   - **The AI:** its list of blocked tools uses the same sentences, so it can explain to the
     person why it cannot do something.
3. **Try before saving.**
   - **Testing a draft:** the policy editor has "Test before saving". It shows what the draft
     rule would change for the worker, resource and action chosen in the evaluation panel. Nothing
     is saved.
   - **Endpoint:** `POST …/policies/simulate`.
   - **Already there:** the evaluation panel itself already answered "what happens now".
4. **Starting policies.**
   - **Before:** creating a worker gave it standing permission for everything its jobs need,
     except critical actions.
   - **Now:** actions that cannot be undone (side effects at high or critical risk, such as
     browser form submission or file upload) need approval, unless the request asked for them.
   - **How "asked for" is proven:** the AI quotes the request's words for the action, checked as
     in the [understanding review](understanding-review.md).
   - **Unchanged:** reading and ordinary writes run as before. Native integration actions that
     must always be approved still are.
5. **Policies are read once per tool build.** This was already in place (phase 1's tool-list
   saving and the existing cache), so nothing further was needed.

## Proof

- **[test_policies_phase5.py](../../backend/tests/test_policies_phase5.py), 13 tests:**
  - provider and name-pattern matching, with existing policies unchanged;
  - every explanation case;
  - the simulator with and without a draft, saving nothing;
  - starting policies: browser reads and typing allowed, and form submission asking unless
    explicitly requested.
- **Offline benchmark:** across all 12 tasks the only change is the wording of the reasons given
  to the AI for blocked tools. Tools, steps, provider calls, approvals and results are identical.
- **Full suite:** all 1,372 backend tests pass; frontend tests, build and the 20 workspace checks
  pass.

## Deploying

- **No migration** and no new settings.
- **Existing workers keep their current policies.** Starting policies apply to workers and jobs
  created or provisioned from now on.

## Update after the first live test (14 October 2026)

**Owner's decision:** writes other people can see ask for approval, unless the request asked for
them. These include comments, issues, labels, pull requests, messages and emails.

- **"Asked for":** each tool request in the task draft carries the person's exact words asking
  for that write, checked against what they wrote. "Comment on issue #2 saying I'm looking into
  it" runs; a comment the AI decides to add asks first.
- **Integration tasks:** a "require approval" policy for those scopes outranks the task's allow
  policy.
- **Worker creation:** the same rule as for irreversible actions.
- **Unchanged:** reads, and changes inside the task's own isolated workspace, run as before.
- **Proof:** in [test_live_test_fixes.py](../../backend/tests/test_live_test_fixes.py).
