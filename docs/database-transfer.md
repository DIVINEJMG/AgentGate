# Database transfer during local development

Neon public network transfer includes results returned to a local backend. AI token
budgets do not control this allowance. Review project usage and its reset date in
the Neon Console; code inspection alone cannot attribute monthly usage to queries.

## Progress delivery

- Visible active conversations refresh task activity every 10 seconds.
- Conversations with no active task refresh activity every 60 seconds.
- Realtime events refresh activity immediately; the feed owns the sole polling timer.
- Hidden tabs pause activity polling and refresh on visibility restoration.
- Conversation and approval fallback polling runs every 30 and 60 seconds respectively.
  WebSocket/SSE events retain immediate delivery. Hidden fallback polls are skipped;
  returning to the tab refreshes current state.
- Activity SQL selects presentation fields: scope, path, changed paths, references,
  verification, status and timestamps. File bodies, prompts, planner proposals and
  workspace evidence remain in their canonical records, available to execution.
- Activity fetches only the latest run per task and the latest planning decision
  and attempt for its current display. Preparation reads exclude saved request
  context; only the current decision pointer is needed. Audit history is retained.
- Read authorization and live ownership checks still run on each activity refresh.

No migration or Neon setting change is required. Restart the backend and refresh
the frontend after installing this change. Stopping local development processes
when they are unused also avoids background recovery and queue queries.

## Verification and remaining measurements

Regression tests cover selected SQL expressions, tenant/resource visibility,
historical progress, failed refresh recovery, overlapping requests, idle cadence
and visibility restoration. These checks use no production database.

Compare the Console's transfer growth over equivalent usage windows to measure
savings. Other execution/context/history queries still require data to perform
their features; their contribution needs measurement before further changes.
Do not delete evidence or reset database statistics to reduce network usage.
