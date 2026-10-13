# Phase 4: approvals

Written 13 October 2026, for the [improvement plan](../agentgate-improvement-plan.md).

**The owner's decisions:**
- **Expiry:** 24 hours, with a reminder at 12. On expiry, cancel the action and finish the task as
  "needs attention".
- **"Always allow this kind":** yes, narrow and confirmed.
- **Editing before approving:** yes.

**Before:** approvals offered approve or decline only, never expired, never reminded anyone, and
showed the action's input only as raw JSON under "Technical detail".

## What changed

1. **See what will happen.**
   - **Inbox:** each approval shows "What will happen", the action's input field by field in
     plain text.
   - **Chat card:** shows the same fields and the expiry time.
2. **Edit, then approve.**
   - **What can change:** only the input fields, never the tool, resource or policy.
   - **Checks:** the edit is checked against the tool's own input rules before it is accepted.
   - **What runs:** the edited version.
   - **Record:** the previous input and who changed it are kept, in the action and in the audit log
     (`approval.action_edited`).
   - **Safety:** an approval is bound to the exact action approved. With an edit, the runtime
     re-binds it to the edited input only while that input still matches the hash of what the
     person approved. Anything else is refused and not performed.
3. **Approvals expire.**
   - **12 hours:** a reminder is posted in the conversation the approval belongs to.
   - **24 hours:** the approval expires. The action is cancelled, nothing is performed, the
     conversation is told, and the task finishes as "needs attention" with what it already did.
   - **When it runs:** the runtime sweep does this every 10 minutes.
   - **Settings:** `APPROVAL_REMINDER_HOURS` and `APPROVAL_EXPIRY_HOURS`.
   - **What people see:** the expiry time and reminder state on each approval.
4. **"Always allow this kind."**
   - **Who:** offered to people who may manage policies.
   - **What it creates:** one ALLOW policy for exactly this worker, this resource and this kind of
     action, placed just above the policy that required approval.
   - **Confirmation:** the checkbox states exactly what it allows, and the button reads "Approve and
     always allow".
   - **Refused when:** policy now denies the action, or the tool's actions always need approval.
   - **Removal:** the policy can be removed in Policies like any other.
5. **Approve several at once.** When a task is waiting on several approvals, "Approve all N for
   this task" decides them together. Each one is decided exactly as on its own, and any that
   cannot be decided are reported.

## Proof

- **Offline benchmark:** three new tasks run through the real runtime.
  - `approve-with-edit`: the provider receives the corrected value.
  - `approval-expires`: nothing is written, and the result needs attention.
  - `always-allow`: a later write of the same kind runs without asking, and exactly one narrow
    policy is created.
  - **No regressions:** all earlier tasks are unchanged.
- **[test_approvals_phase4.py](../../backend/tests/test_approvals_phase4.py)** covers the rules:
  - edits that are invalid, unchanged, or attached to a decline;
  - the narrow policy and its priority, and the refusals;
  - batch decisions and failures;
  - one reminder, then expiry;
  - the record fields;
  - the runtime refusing a tampered edit.
- **Workspace:** the Inbox approval panel was checked in the browser preview at desktop and phone
  widths, and the 20 workspace checks pass.

## Deploying

- **No migration.**
- **New optional settings:** `APPROVAL_REMINDER_HOURS=12` and `APPROVAL_EXPIRY_HOURS=24`, in
  `.env.example`.
- **New endpoint:** `POST …/approvals/decisions` for batch decisions.
- **Existing pending approvals:** they start their 24 hours from when they were requested. Any
  older than 24 hours expire on the first sweep after deploying, so decide or note them first.
