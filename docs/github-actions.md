# GitHub action catalog

Generated from `assembled_catalog()`; regenerate after catalog changes. Exact grants and organization policy apply in addition to these defaults. No unrestricted HTTP or GraphQL action is exposed.

Reads are safe to retry within runtime budgets. Writes require canonical verification; uncertain writes reconcile first or pause. Coding command replay is never inferred safe from a missing process result. See [operations and rollout](github-integration.md).

| Scope | Permission | Inputs (required marked *) | Approval default | Verification / reconciliation |
|---|---|---|---|---|
| `github.repository.metadata.read` | `metadata:read` | none | policy only | bounded observed response |
| `github.repository.readme.read` | `contents:read` | ref | policy only | bounded observed response |
| `github.repository.contents.read` | `contents:read` | **path***, ref | policy only | bounded observed response |
| `github.repository.tree.read` | `contents:read` | **sha***, recursive | policy only | bounded observed response |
| `github.repository.branches.read` | `contents:read` | page, per_page | policy only | bounded observed response |
| `github.repository.branch.read` | `contents:read` | **branch*** | policy only | bounded observed response |
| `github.repository.tags.read` | `contents:read` | page, per_page | policy only | bounded observed response |
| `github.repository.commits.read` | `contents:read` | page, per_page, sha, path | policy only | bounded observed response |
| `github.repository.commit.read` | `contents:read` | **sha*** | policy only | bounded observed response |
| `github.repository.compare.read` | `contents:read` | **base***, **head*** | policy only | bounded observed response |
| `github.repository.code.search` | `contents:read` | page, per_page, **query*** | policy only | bounded observed response |
| `github.repository.branch.create` | `contents:write` | **branch***, **sha*** | policy only | canonical object read; branch_sha |
| `github.repository.commit.create` | `contents:write` | **branch***, **baseSha***, **message***, **changes*** | policy only; required for deletion/workflow files | canonical object read; commit_marker |
| `github.repository.branch.update` | `contents:write` | **branch***, **sha***, **expectedSha*** | policy only | canonical object read; branch_sha |
| `github.repository.issues.read` | `issues:read` | none | policy only | bounded observed response |
| `github.repository.issues.list` | `issues:read` | page, per_page, state, labels | policy only | bounded observed response |
| `github.repository.issues.search` | `issues:read` | page, per_page, **query*** | policy only | bounded observed response |
| `github.repository.issue.read` | `issues:read` | **number*** | policy only | bounded observed response |
| `github.repository.issues.create` | `issues:write` | **title***, body, labels | policy only | canonical object read; body_marker |
| `github.repository.issue.update` | `issues:write` | **number***, title, body, state, labels, assignees, milestone | policy only | canonical object read; exact_fields |
| `github.repository.issue.comments.read` | `issues:read` | page, per_page, **number*** | policy only | bounded observed response |
| `github.repository.issue.comment.create` | `issues:write` | **number***, **body*** | policy only | canonical object read; body_marker |
| `github.repository.issue.labels.set` | `issues:write` | **number***, **labels*** | policy only | canonical object read; exact_fields |
| `github.repository.labels.read` | `issues:read` | page, per_page | policy only | bounded observed response |
| `github.repository.label.create` | `issues:write` | **name***, **color***, description | policy only | canonical object read; named_object |
| `github.repository.label.update` | `issues:write` | **name***, new_name, color, description | policy only | canonical object read; exact_fields |
| `github.repository.milestones.read` | `issues:read` | page, per_page | policy only | bounded observed response |
| `github.repository.milestone.create` | `issues:write` | **title***, description, due_on | policy only | canonical object read; body_marker |
| `github.repository.milestone.update` | `issues:write` | **number***, title, description, state, due_on | policy only | canonical object read; exact_fields |
| `github.repository.pull_requests.read` | `pull_requests:read` | none | policy only | bounded observed response |
| `github.repository.pull_requests.list` | `pull_requests:read` | page, per_page, state, head, base | policy only | bounded observed response |
| `github.repository.pull_request.read` | `pull_requests:read` | **number*** | policy only | bounded observed response |
| `github.repository.pull_request.files.read` | `pull_requests:read` | page, per_page, **number*** | policy only | bounded observed response |
| `github.repository.pull_request.create` | `pull_requests:write` | **title***, body, **head***, **base*** | policy only | canonical object read; body_marker |
| `github.repository.pull_request.update` | `pull_requests:write` | **number***, title, body, state | policy only | canonical object read; exact_fields |
| `github.repository.pull_request.reviewers.request` | `pull_requests:write` | **number***, reviewers, team_reviewers | policy only | canonical object read; exact_fields |
| `github.repository.pull_request.ready` | `pull_requests:write` | **nodeId*** | policy only | canonical object read; exact_fields |
| `github.repository.pull_request.branch.update` | `pull_requests:write` | **number***, **expected_head_sha*** | policy only | canonical object read; pr_head |
| `github.repository.pull_request.merge` | `contents:write` | **number***, **sha***, merge_method | required | canonical object read; merged_sha |
| `github.repository.reviews.read` | `pull_requests:read` | page, per_page, **number*** | policy only | bounded observed response |
| `github.repository.review.comments.read` | `pull_requests:read` | page, per_page, **number*** | policy only | bounded observed response |
| `github.repository.review.comment.create` | `pull_requests:write` | **number***, **body***, **commit_id***, **path***, **line***, **side*** | policy only | canonical object read; body_marker |
| `github.repository.review.submit` | `pull_requests:write` | **number***, **commit_id***, **body***, **event*** | required; COMMENT is routine | canonical object read; body_marker |
| `github.repository.review.thread.resolve` | `pull_requests:write` | **threadId*** | policy only | canonical object read; exact_fields |
| `github.repository.workflows.read` | `actions:read` | page, per_page | policy only | bounded observed response |
| `github.repository.workflow.runs.read` | `actions:read` | page, per_page, branch, event | policy only | bounded observed response |
| `github.repository.workflow.run.read` | `actions:read` | **runId*** | policy only | bounded observed response |
| `github.repository.workflow.jobs.read` | `actions:read` | page, per_page, **runId*** | policy only | bounded observed response |
| `github.repository.workflow.logs.read` | `actions:read` | **jobId*** | policy only | bounded observed response |
| `github.repository.workflow.artifacts.read` | `actions:read` | page, per_page, **runId*** | policy only | bounded observed response |
| `github.repository.workflow.artifact.read` | `actions:read` | **artifactId*** | policy only | bounded observed response |
| `github.repository.workflow.dispatch` | `actions:write` | **workflowId***, **ref***, **expectedSha***, inputs | required | canonical object read; workflow_run |
| `github.repository.workflow.rerun` | `actions:write` | **runId***, **expectedSha***, **expectedAttempt*** | required | canonical object read; run_attempt |
| `github.repository.workflow.cancel` | `actions:write` | **runId***, **expectedSha***, **expectedAttempt*** | required | canonical object read; run_state |
| `github.repository.checks.read` | `checks:read` | page, per_page, **sha*** | policy only | bounded observed response |
| `github.repository.status.read` | `statuses:read` | **sha*** | policy only | bounded observed response |
| `github.repository.check.create` | `checks:write` | **name***, **head_sha***, **status***, conclusion, **output*** | policy only | canonical object read; external_id |
| `github.repository.releases.read` | `contents:read` | page, per_page | policy only | bounded observed response |
| `github.repository.release.read` | `contents:read` | **releaseId*** | policy only | bounded observed response |
| `github.repository.release.create` | `contents:write` | **tag_name***, **target_commitish***, **name***, body, prerelease | policy only | canonical object read; tag_marker |
| `github.repository.release.update` | `contents:write` | **releaseId***, name, body | policy only | canonical object read; exact_fields |
| `github.repository.release.publish` | `contents:write` | **releaseId***, **expectedTag***, **expectedBodyHash*** | required | canonical object read; release_state |
| `github.repository.release.asset.upload` | `contents:write` | **releaseId***, **artifactId***, **sha256***, **name*** | policy only | canonical object read; asset_digest |
| `github.repository.dependabot.read` | `dependabot_alerts:read` | page, per_page | policy only | bounded observed response |
| `github.repository.code_scanning.read` | `security_events:read` | page, per_page | policy only | bounded observed response |
| `github.repository.deployments.read` | `deployments:read` | page, per_page | policy only | bounded observed response |
| `github.repository.deployment.statuses.read` | `deployments:read` | page, per_page, **deploymentId*** | policy only | bounded observed response |
| `github.repository.discussions.search` | `discussions:read` | **query***, after | policy only | bounded observed response |
| `github.project.read` | `organization_projects:read` | after | policy only | bounded observed response |
| `github.project.item.add` | `organization_projects:write` | **contentId*** | policy only | canonical object read; project_content |
| `github.project.item.update` | `organization_projects:write` | **itemId***, **fieldId***, **value*** | policy only | canonical object read; exact_fields |
| `github.project.item.archive` | `organization_projects:write` | **itemId*** | policy only | canonical object read; exact_fields |
| `github.repository.discussions.read` | `discussions:read` | after | policy only | bounded observed response |
| `github.repository.discussion.read` | `discussions:read` | **number***, after | policy only | bounded observed response |
| `github.repository.discussion.categories.read` | `discussions:read` | none | policy only | bounded observed response |
| `github.repository.discussion.create` | `discussions:write` | **categoryId***, **title***, **body*** | policy only | canonical object read; body_marker |
| `github.repository.discussion.comment.create` | `discussions:write` | **discussionId***, **body*** | policy only | canonical object read; body_marker |
| `github.repository.discussion.update` | `discussions:write` | **discussionId***, **title***, **body*** | policy only | canonical object read; exact_fields |
| `github.repository.workspace.open` | `contents:read` | **sha*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.read` | `contents:read` | **path*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.search` | `contents:read` | **query*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.edit` | `contents:read` | **path***, **content*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.patch` | `contents:read` | **patch*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.diff` | `contents:read` | none | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.command.start` | `contents:read` | **command*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.command.status` | `contents:read` | **commandId*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.command.cancel` | `contents:read` | **commandId*** | policy only | isolated state / durable command evidence; no GitHub publication |
| `github.repository.workspace.close` | `contents:read` | none | policy only | isolated state / durable command evidence; no GitHub publication |
