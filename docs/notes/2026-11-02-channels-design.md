# channels: a layer for talking to external systems (design draft, 2026-11-02)

Status: **Deleted (2026-11-03).** The GitHub adapter this design produced (`channels/service.py`, `github.py`, `triage.py` and others) has been deleted. This note is kept only as a historical record. For the current channel layer, see [2026-11-02-channel-layer.md](2026-11-02-channel-layer.md). Previous status: phase 1 implemented, paused from 2026-11-02. People interact with myorch only through the local console (`myorch ui`). The text below is the channels design, to be used as the starting point if it is revived. intake has been deleted; wherever the text mentions intake, it is background.

## Why this layer

Today the only thing between myorch and the outside world is intake: a long-running Temporal workflow that polls GitHub in a sandbox every 60 seconds,
triages, starts runs, then polls the run status and posts notifications on the original issue. Problems:

- **GitHub only.** Slack and email would each need their own implementation. Input, output and state are spread over three places: intake, runner/jobs.py and the CLI.
- **The flow engine does communication.** Polling, de-duplication, thread subscriptions and notification wording all live in the Temporal workflow, mixed in with flow orchestration.
- **No streaming output.** The runner reads the log only after each turn ends. People can only wait for the result.
- **Incomplete feedback.** When triage decides `none`, or triage fails, the user gets no reply at all (see the G0 discussion).

Goal: gather everything about "dealing with external systems" into one local layer called **channels**. The flow engine handles only flows: it receives "there is a message" and emits "there is an event".

## Boundaries

```
 Slack ─┐                         ┌──────────────── channels (local process myorch channels) ────────────────┐
 GitHub ┼─ channel adapter ─► inbox ┤ triage (G0) → conversation ↔ run binding → commands     events → render ─┼─ channel adapter ─► outbox
 Email ─┤                         └──────────────────────────┬──────────────────────────▲─────────────────┘
 CLI/Web┘                                                    │ start/reply/approve/…     │ run events, streaming output
                                                             ▼                           │
                                              engine (Temporal: FlowWorkflow + worker) ── event log (events.db)
```

| Belongs to channels | Does not belong to channels |
|---|---|
| Connecting to external systems (polling, websocket, IMAP), sending messages, editing messages | Flow orchestration, retries, approval rules (stay in core / the engine) |
| Recognizing who someone is, whether to respond (Gate), whether to take the request (triage) | Sandboxes, credential injection (stay in OpenShell) |
| The mapping between conversations and runs | The run state itself (stays in Temporal) |
| Turning run events into the right message for each channel, including streaming | Project knowledge, checks (stay in the project pack) |

## Concepts

- **Channel**: an adapter for one external system (`slack`, `github`, `email`, `cli`, later `web`). Interface:
  - `listen() -> async inbound stream` (polling, Socket Mode, IMAP IDLE, chosen per channel)
  - `send(conversation, Outbound) -> MessageRef`, `edit(MessageRef, Outbound)` (streaming and "progress messages" rely on editing)
  - `capabilities`: whether it has threads, can edit, can show buttons, its message length limit, whether it suits streaming.
- **Conversation**: a place for a conversation in a channel, written as a URI: `github://leweii/myorch-java-lab/issues/4`,
  `slack://T123/C456/1730530000.123` (channel + thread), `email://<Message-ID thread>`, `cli://jakobhe`.
  A conversation belongs to one project and can follow several runs. A run can have several conversations (an issue and the PR it opened).
- **Inbound / Outbound**: unified incoming and outgoing messages. Outbound kinds: `ack` (received), `question` (a question, with options),
  `approval` (an approval request, with "where to see the change"), `progress` (progress / streaming), `result` (the result), `declined` (not taken, with a reason), `error`.
- **Identity**: maps each channel's accounts to the same person: `jakobhe` = GitHub `leweii` = Slack `U0…` = `lewei.me@gmail.com`.
  The allow list and the Mandate's initiator and approvers all use this person, not an account on one channel. The channel itself verifies identity (Slack user id, GitHub login),
  which is more trustworthy than the CLI's `--as`.
- **Triage (G0)**: moved over from intake and channel-independent: Gate (our own message? on the list? summoned? is a bound run waiting for it?)
  → fixed commands are parsed directly → everything else goes to Haiku for triage (in a sandbox with no credentials). Along the way, apply the improvements from the G0 discussion:
  - Hand the original request (title, body, the summoning message, the last few messages in the thread) to the run as `docs/external/request.md`. Haiku's one-line summary is only the title.
  - A summon always gets a reply: `none` replies "Not taken: reason, what is missing". A triage failure replies "Summon again later" and retries in the next round.
  - Triage cannot use tools. Data boundaries use random delimiters.

## Interface with the engine

**Commands (channels → engine)**, using the existing Temporal client: `start_run(flow, project, inputs, initiator, source=conversation, request)`,
`reply`, `approve`, `reject` (the approver comes from Identity), `cancel`. The run id is still determined by "channel + message id", so a duplicate message does not start two runs.

**Events (engine → channels)**: today intake queries the run status every 60 seconds and diffs it to work out what to say. Instead, the engine writes events and channels reads them:

- **Event log** `myorch-data/events.db` (SQLite, enough for a single machine): `(seq, run_id, time, kind, step, payload)`.
- Writers:
  - FlowWorkflow writes through a local activity when state changes: `run_started`, `step_started`, `step_finished`,
    `waiting_input` (the question and options), `waiting_approval` (number of people, rule, draft PR or `myorch review`),
    `step_failed_ask` (`on_failed: ask`, which another session is adding), `pr_opened`, `run_finished` (the result).
  - The runner writes `stream` (fragments of agent output) while a step runs.
- Readers: channels reads incrementally by seq (once a second; local SQLite is cheap) and remembers per conversation how far it has read.
  `myorch watch <run>` and the future web page read the same log.
- Benefits: the engine does not need to know which channels exist. After a channel disconnects and reconnects, it continues from its seq, with nothing lost and nothing duplicated.

## Streaming output

1. **Source**: the runner starts reading output while the step runs, instead of waiting for a turn to end.
   - claude-code switches to `--output-format stream-json`: this gives structured events (the agent's text, which tools it called, tool results).
     They are easier to read than the raw log, and allow relaying only "what it is doing" (for example "running checks.sh", "editing OrderService.java"). The manifest is still read from a file and is unaffected.
   - Script steps: read the log file incrementally.
   - How to read: a helper container that mounts the volume read-only runs `tail -F` on the step's log (one per running step, stopped when the step ends), or reads
     the directory that viewer option B syncs to the Mac (if that is done first).
2. **Each channel decides how to present it** (attention principle: stream only where a person is watching):

| Channel | Presentation |
|---|---|
| CLI (`myorch watch <run>`), the future web page | Full streaming |
| Slack | One "progress" message in the thread, edited every few seconds (only the current step and the last few actions). Questions, approvals and results are sent as new messages |
| GitHub | No streaming. Comments only at milestones (received, question, approval, result), as today |
| Email | No streaming. Mail only when a person needs to act and when there is a result |

## How each channel connects (all local, no public address needed)

| Channel | Receive | Send | Notes |
|---|---|---|---|
| GitHub | Poll REST (with ETag, to save quota), or use the notifications API to fetch only mentions of myorch | issue / PR comments | Webhooks cannot reach the local machine, so it is still polling. Latency about 60 seconds |
| Slack | Socket Mode (websocket, no public address needed) | Messages, threads, buttons, edits | Approvals and options use buttons. Whoever clicks is the approver |
| Email | IMAP IDLE | SMTP | Threads are matched by Message-ID / In-Reply-To |
| CLI | Local socket | Terminal | `myorch watch`, later `myorch chat` |

## Configuration

`channels.yaml` replaces `intake.yaml`:

```yaml
identities:                      # who is who
  jakobhe: {github: leweii, slack: U0XXXX, email: lewei.me@gmail.com}
channels:
  github: {poll_interval: 60}    # which repositories to watch, the summon word and identities still come from each project pack
  slack:
    conversations:               # which channel belongs to which project
      "#myorch-blog": blog
      "#mdm-dev": mdm
  email: {address: …, projects: {mdm: "subject prefix [mdm]"}}
mode: observe                    # new content without a summon is only recorded (as today)
assess_model: claude-haiku-5-5
```

## Credentials: to be decided first

Today GitHub polling and comment posting both run in one-off sandboxes, and credentials never leave OpenShell. channels is a long-running local process that must hold the Slack
websocket and IMAP connections. Starting a sandbox each time is not practical. Two options:

- **A (recommended)**: channels runs in a local process. Each channel's token is stored in the macOS Keychain, each with the least privilege
  (GitHub: read issues + post comments only; Slack bot: only in the specified channels). channels never executes any external content. Triage still runs in a sandbox with no credentials.
  Cost: credentials are no longer all in OpenShell. The principle "credentials never leave OpenShell" changes to "agents cannot reach credentials".
- **B**: channels itself runs in a long-running OpenShell sandbox. Credentials are injected by a provider and outbound traffic is restricted by policy.
  Cost: the sandbox must reach the local Temporal and event store, and the long-running sandbox's lifecycle must be managed by us. Much more complex.

## How to remove intake

1. Move the Gate, command parsing, triage decisions and notification wording from `core/intake.py` to `channels/triage.py` and `channels/render.py`, together with their tests.
2. Delete `engines/temporal/intake.py` (IntakeWorkflow), POLL / NOTIFY in `runner/jobs.py`, `intake.yaml` and the `myorch intake` command.
   Keep the triage job (ASSESS); channels calls it.
3. Import intake's state (cursors, processed events, thread subscriptions) into SQLite once, or simply start fresh (no runs are in progress now).
4. The Mandate's `source` becomes the conversation URI. "PR subscription" becomes binding on receipt of the `pr_opened` event.

## Phases

| Phase | What | Acceptance |
|---|---|---|
| 1 | Event log (engine writes lifecycle events) + channels skeleton + GitHub channel (equivalent to today's intake, plus the G0 improvements). Delete intake | java-lab: issue summon → draft PR → approval → result, end to end through channels only. `none` gets a reply |
| 2 | Streaming: the runner reads output during the run (stream-json), `stream` events, `myorch watch <run>` | Every step of a development run visible live in the terminal |
| 3 | Slack channel: Socket Mode, threads, button approvals, progress messages | Post a request in Slack → options and approval via buttons → result |
| 4 | Email channel | Send a request by email, approve by replying |
| 5 | The web page (viewer) as a channel, reading the same event log | Merged with ARCHITECTURE §12 |

## For you to decide

1. Where credentials go: A (local Keychain, recommended) or B (long-running sandbox).
2. Which Slack workspace to use (company or personal), and whether we can install our own Slack app (Socket Mode needs one).
3. Which mailbox to use for email, and whether it is worth doing (after Slack).
4. Is a local SQLite (`myorch-data/events.db`) acceptable for the event log?
5. In phase 1, GitHub is still polled every 60 seconds. Is that acceptable? (The local machine has no public address, so it cannot receive webhooks.)
