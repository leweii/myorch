# Slack adapter (2026-11-05)

Status: **code and unit tests done; not yet connected to a real Slack.** Installation steps are in "Slack" in `deploy/README.md`.

## What the user decided

- Install it in a personal workspace.
- The first version does four things: notifications, answering questions, approvals, starting runs.
- Messages go to the initiator as a direct message, one thread per run.

This overturns the rule in place since 2026-11-02 of "interaction only in the console": Slack, like the console, is a platform adapter of the channel layer (see [2026-11-02-channel-layer.md](2026-11-02-channel-layer.md)).

## How it connects

- **Socket Mode**: myorch connects to Slack with an app-level token (`xapp-`) and calls the Web API with a bot token (`xoxb-`). The machine needs no open port and no tunnel.
- The adapter runs in the `myorch serve` process (the compose `ui` service). The code is in `src/myorch/channels/slack.py`. It starts only when both tokens are set.
- Both tokens are channel credentials. They live in `deploy/secrets/slack.env`, and only the `ui` service reads them. The worker and sandboxes cannot see them.

## Outbound: events → Slack

| Event | In Slack |
|---|---|
| `run_started` | A root message in the initiator's direct messages: flow, project, run ID, request, and an "Open in console" link |
| `step_started`, `step_finished`, `loop_round` | **One** progress message in the thread, updated in place, not one message each |
| `question` | The question and option buttons. A direct reply in the thread also counts as an answer |
| `replied` | Changes the question message to "Replied: …" and removes the buttons (also for replies from the console or CLI) |
| `approval` | Risk level, who is still needed, the "Conclusion" of `docs/risk.md`, and four buttons: Approve, Request changes, Reject, View in console |
| `approval_progress` | Updates the approval message. Removes the buttons when enough approvers have approved or someone rejects |
| `approval_reminder`, `changes_requested`, `run_resumed`, `pr_opened` | One message in the thread |
| `run_finished` | The result. With `delivery`, it says which branch and commit were pushed (since 2026-11-06 myorch does not open PRs) |

Messages that need a person (question, approval, reminder, finished, PR) are also posted to the main direct-message view (`reply_broadcast`), so they trigger a notification. Progress does not. This follows the principle in `core/events.py`: a run speaks only when it starts, needs a person, opens a PR, and finishes.

A run goes to Slack only if the initiator has a `slack` account registered under `identities` in `channels.yaml`. On its first start the adapter begins at the latest event and does not resend history.

## Inbound: Slack → commands

| Action | Command |
|---|---|
| Reply in the run's thread | `reply`, answering the run's latest question that is still waiting |
| Click an option button | `reply` with that option as the content |
| Click Approve, Reject | `approve`, `reject` |
| Click Request changes and fill in the dialog | `request_changes` |
| `/myorch`, or the "New run" button in the direct messages | Opens the start dialog; submitting it calls `start` |

- **Identity**: the Slack user ID is mapped to a person through `identities` in `channels.yaml`. Commands are sent in that person's name (`via=slack`). The workflow still does the approver check and `require_initiator` as usual.
- **Unregistered people**: the bot replies with their Slack user ID and how to register. It runs no command.
- **Start dialog**: first pick a project, then a task (the task list shows only the flows this project can use; when there are flows that need no project, the project list gets an extra "No project" entry). The dialog then draws fields from the inputs this flow declares (the spec in `core/inputs.py`), like the console's "New run" form. A `message` input is the original request text. A `run` input lists this project's recent runs with the same role. eval is not in the list (postmortem and the CLI start it).

## State

`myorch-data/slack/state.json`: the event cursor, plus for each run the direct-message channel, thread, progress message, unanswered questions and approval message. After a restart it continues from the cursor and resends nothing.

## Not done yet

- Not tested against a real Slack. There are only unit tests with a fake client (`tests/test_slack.py`).
- Knowledge sources cannot be added or removed when starting; the project defaults are used.
- When a run has several questions waiting at once, a reply in the thread answers only the latest one. Answer the others with the buttons or in the console.
