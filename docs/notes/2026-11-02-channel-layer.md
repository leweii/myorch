# The channel layer: the relay between myorch and each platform (2026-11-02)

Status: **Final as of 2026-11-02. All 4 migration steps are implemented.** The key points are in ARCHITECTURE.md §14e.

This note replaces these parts of [2026-11-02-channels-design.md](2026-11-02-channels-design.md): credentials, where the console sits, and the process layout. For delivery details, see [2026-11-02-delivery-interface.md](2026-11-02-delivery-interface.md).

## What a channel is

A channel is only a relay layer. It connects myorch to each platform (console, GitHub, Slack, Jira, ...). It does not keep run state and does not make extra decisions.

```
 Console (browser) ─┐                                     ┌─ commands ──► Temporal (start / signal)
 GitHub ────────────┼── platform adapter ◄──► channel (relay) ─┼─ events   ◄── myorch-data/events.db (written by the worker)
 Slack, Jira ───────┘                                     └─ content  ◄── the run's workspace (docs/, diff, logs)
```

It relays only three things:

| What it relays | Direction | How |
|---|---|---|
| **Events** | myorch → platform | Reads `events.db` by seq and hands events to the adapters that follow the run. The adapter decides how to present them on its platform |
| **Commands** | platform → myorch | The adapter turns a person's action into a command: `start`, `reply`, `approve`, `reject`, `request_changes`, `cancel`. The channel fills in "who this is" from `identities` in `channels.yaml`, then calls the Temporal client |
| **Content** | myorch → platform | When an adapter needs a run's files, diff or logs, the channel reads them from the workspace and hands them over. Content is not copied |

Everything specific to one platform happens in that platform's adapter:

- How to connect: the console uses HTTP and SSE, GitHub is polled, Slack uses a persistent connection.
- Whose messages to accept: for example GitHub's allow list and summon word (currently `channels/triage.py`).
- How to word things: for example whether an event becomes a GitHub comment or a Slack message.
- Delivery: for example the GitHub adapter opens a PR when it receives `delivery` in `run_finished`.

## Decisions made (user, 2026-11-02)

1. **The console is a platform adapter**, at the same level as GitHub and Slack. The console no longer queries Temporal directly and no longer reads the Docker volume directly. It receives events, sends commands and reads content only through the channel.
2. **Notifications and writes both go through the channel.** myorch's flows and engine only write events. They do not call any external system.
3. **There are two kinds of credentials.**
   - **Channel credentials**: myorch's identity on a platform, used to talk to people. Examples: the Slack bot token, the account that posts comments on GitHub. The channel manages them (the original plan used `channels/secrets.py` and the macOS Keychain; that was deleted on 2026-11-03 together with the GitHub adapter). Adapters connect to the platform directly and can keep a persistent connection.
   - **Work credentials**: the identity used to do the work, for example pushing a branch or opening a PR. They live only in the OpenShell gateway. When an adapter needs such an action (for example opening a PR), it starts a one-off sandbox (`runner/jobs.py`) and the supervisor injects them.
   - Agents cannot reach either kind of credential.
4. **Richer events.** An adapter must be able to rebuild a run's state from events alone, so these events are added (`core/events.py`):

   | New event | data |
   |---|---|
   | `step_started` | step, instance, attempt, turn, model |
   | `step_finished` | instance, status, summary, outputs, error |
   | `approval_progress` | step, needed, approvers (people who approved), rejected_by |
   | `loop_round` | loop, round, status |

   Also remove the unused `draft_pr_url` from the `approval` event. Changes to the workflow add a version marker with `workflow.patched()`.
5. **The CLI does not go through the channel.** It is a development and debugging tool and still calls the Temporal client directly.
6. **Out of scope:** the channel does not keep a read model, does not do a unified Gate, and does not do auditing.

## Process

One process: `myorch serve`. It runs the channel and the enabled platform adapters (the console at 127.0.0.1:8800). It replaces `myorch ui` and `python -m myorch.channels` (the latter was deleted on 2026-11-03).

## Migration

More than one session edits the same files. Before each step starts, agree on who edits which files.

| Step | What | Files |
|---|---|---|
| 1 | `deliver` step: the workflow only pushes the branch, `run_finished` carries `delivery` (development workflow related, in progress) | `flows/`, `core/events.py` |
| 2 | Richer events | `core/events.py`, `engines/temporal/workflow.py` |
| 3 | channel: relay events (pushed to the console over SSE), relay commands, relay content. `myorch serve`. Delivery moves from `ui/delivery.py` to an adapter | `src/myorch/channels/`, `cli.py` |
| 4 | The console uses the channel and no longer touches Temporal or the volume directly (ui related) | `src/myorch/ui/`, `channels/serve.py` |

## Implementation log (2026-11-02)

- **Step 2: richer events.** `derive_events(detail=True)` in `core/events.py` produces `step_started`, `step_finished` (including skipped), `approval_progress` (with `missing`; the rule text is computed by `core/run.py`), `loop_round` (with `until`, and with `question` when it hits the round limit), and `changes_requested`. The workflow records accepted replies in the signal and emits them as `replied` events, so replies sent from the CLI are visible too. `run_started` carries the inputs, the initiator and the path of the flow snapshot (`.myorch/flow.yaml`), not the whole flow structure. The `approval` event dropped `draft_pr_url` and gained `missing`.
  - Version marker `event-detail-v1`: only runs started after this marker emit the new events. The last 40 run histories were replayed with the new code and 39 passed. The one that failed, `development-javalab8-issue8`, already failed replay before this change.
  - After the worker restarts, only newly started runs have the new events.
- **Step 3: the channel relay layer.**
  - `channels/relay.py`: `Relay` relays events (`events`, `follow`), commands (`start`, `reply`, `approve`, `reject`, `request_changes`, `cancel`, with the person filled in from identities) and content (`list_files`, `read_file`, `step_files`, `changes`). `runs()` takes the status from Temporal, because a run terminated from outside does not write `run_finished`.
  - `channels/http.py`: the HTTP and SSE interface mounted at `/channel`. The list of endpoints is at the top of the file. It accepts only a local Host, and POST requests must carry the `X-Myorch-UI` header.
  - `channels/serve.py` and `myorch serve`: one process runs the relay layer, the console and, optionally, the GitHub adapter (`--github`). The GitHub adapter and `--github` were deleted on 2026-11-03.
- **Step 4: the console uses the channel** (ui related session).
  - Read: `ui/service.py` no longer calls the Temporal client directly and no longer reads the Docker volume. `ui/runview.py` rebuilds run state from `relay.events`. The flow structure comes from `relay.read_file(".myorch/flow.yaml")`. The run list uses `relay.runs()`. Files, diffs and step outputs use the relay's content interface.
  - Write: start, reply, approve, reject, request_changes and cancel all go through the relay, with `via="web"` and the local user.
  - Live updates: the browser subscribes to `/channel/events` (SSE). Polling is only a fallback: the run list refreshes every 30 seconds. When SSE is disconnected, or when viewing an old run, the details refresh every 5 seconds.
  - Delivery: `ui/delivery.py` moved to `channels/delivery.py` and is started directly by `channels/serve.py`. The state file path is unchanged (`myorch-data/ui/deliveries.json`).
  - `myorch ui` is now the same as `myorch serve`.
- **Fallbacks during migration** (delete once the old runs are gone):
  - For runs whose `run_started` has no `flow_file` (no `event-detail-v1` marker), the console still rebuilds state with a Temporal query. This code is only in `Service._engine_view`.
  - When an old run lacks fields such as the initiator, they are filled in from the mandate in the volume (`_legacy_records`).
- **Not done yet:** `relay.start` imports `_load_project`, `_flow_inputs`, `_auto_runs` and `_run_records` from `cli.py`. These should move to a shared module later.
