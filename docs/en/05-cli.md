# 5. CLI reference

Every command has the form `uv run myorch <command>` and runs from the `myorch/` directory. The output of `uv run myorch <command> -h` is the authority on arguments. For which services each command needs, see [2. Services and dependencies](02-services.md#which-services-each-command-depends-on).

## Global arguments

Global arguments go **before** the subcommand, for example `uv run myorch --address host:7233 list`.

| Argument | Env var | Default | Purpose |
|---|---|---|---|
| `--address` | `MYORCH_TEMPORAL` | `localhost:7233` | Temporal address |
| `--volume` | `MYORCH_VOLUME` | `myorch-artifacts` | The Docker volume that holds the workspaces |
| `--config` | `MYORCH_CONFIG` | `.` | The myorch code directory. `flows/`, `profiles/` and `channels.yaml` are looked up here |
| `--knowledge` | `MYORCH_KNOWLEDGE` | `<parent of config>/myorch-knowledge` | Project pack directory |
| `--data` | `MYORCH_DATA` | `<parent of config>/myorch-data` | Run data directory. The worker's event log `events.db` is here (`/data` in compose) |

## Where to run

The everyday environment is docker compose (see [4. Setup](04-setup.md)). Below, `dc` is short for `docker compose -f deploy/compose.yaml`.

| Where | How to run | Notes |
|---|---|---|
| In the container | `dc exec worker myorch <command>` | The env vars are all set. Every command works |
| On the host | `uv run myorch <command>` | Connects to the Temporal in compose (`localhost:7233`) and the same volume. Limited in the cases below |

Limits on the host:

| Case | Reason | What to do |
|---|---|---|
| Commands that read the event log: `run` with `-i from_run=...` or with another run's id in an input, the learn flow, `eval report` | Processes on the host read events through the console's `/channel/events.json` and do not open `events.db` directly (for the reason, see the "Event log" section of [11. Troubleshooting](11-troubleshooting.md)) | Start the console first. If the console is not at `http://127.0.0.1:8800`, set `MYORCH_URL` |
| Commands that use a sandbox: `knowledge check`, syncing a git knowledge source | `openshell` on the host connects to the brew gateway by default, which is stopped | Run them in the container |
| `worker`, `ui`, `serve` | They open `events.db` directly | While compose is running, do not start these three commands on the host as well |

## By purpose

| Purpose | Commands |
|---|---|
| Install, start | `init`, `worker`, `ui`, `serve` |
| Start runs | `run`, `expand`, `schedule` |
| View runs | `list` (`runs`), `status`, `review`, `logs`, `out`, `ws`, `export` |
| Intervene in runs | `reply`, `approve`, `reject`, `changes`, `cancel`, `resume` |
| Project packs | `project list / show / sync / github-profile` |
| Knowledge sources | `knowledge list / sync / check` |
| Reviewer evals | `eval collect / list / run / report` |

A run id has the form `<flow>-<8 hex digits>`, for example `development-1f29e33a`. The examples below all use it. Before 2026-11-10, flows were separate per project, and old run ids look like `ck-development-…`.

---

## Install, start

### `init`

```sh
uv run myorch init [--tools tools]
```

1. Creates the volume `myorch-artifacts` and gives it OpenShell's approval label. If the volume exists, it is not recreated and the label is not added.
2. Gives the volume's root directory to uid 1000.
3. Installs the helper commands from the `--tools` directory (default `tools/`) into the volume's `tools/`. In the sandbox they are mounted at `/sandbox/bin`, read-only.

| Helper command | Purpose |
|---|---|
| `myorch-ask` | The agent asks a person a question. The step pauses until the person replies |
| `myorch-checks` | Runs the project's `setup.sh` and `checks.sh` on the files this run changed |
| `myorch-mcp` | Starts the agent with the run's MCP knowledge sources, allowing only the registered tools. `--check` is for `knowledge check` |
| `myorch-stream` | Turns Claude Code's stream-json output into a readable log |
| `myorch-steps` | Lists every step of this run and reads a step's manifest and logs in parts (it can also read runs mounted under `observed/`). The fix, reflect and takeaway prompts mention it; reviewer and verify do not |

Can be run repeatedly. Run it again after changing `tools/`. The compose `init` service runs it on every `up`.

### `worker`

```sh
uv run myorch worker
```

Starts the Temporal worker. It executes all steps: starts sandboxes, waits for results, reads manifests, executes host actions, writes the event log (`events.db` under `--data`). Runs in the foreground; press Ctrl-C to exit.

- **Restart it after changing code under `src/`.** In compose, run `dc up -d --build worker`, or release with `deploy/update.sh`.
- Before deleting a sandbox, it saves the supervisor container's log as the step's `logs/supervisor-<attempt>-<turn>.log`.

### `ui`

```sh
uv run myorch ui [--port 8800] [--host 127.0.0.1]
```

The same command as `serve`; see the next section. Starts the local console at http://127.0.0.1:8800/.

- `--host` defaults to `127.0.0.1` (or the env var `MYORCH_UI_HOST`). Set it to `0.0.0.0` only in the container; compose then publishes the port on the host's `127.0.0.1`.
- The console accepts only requests whose Host is `127.0.0.1:<port>` or `localhost:<port>`. Others get 403 `bad host`. So the port must be the same inside and outside the container.

There is no agent in the console. People start runs, reply and approve here. The conversation in the middle is the selected run; what you write goes to that run unchanged.

| Area | Content |
|---|---|
| Top bar | The run dropdown: filter by project, or search by [tag](#tags-finding-runs-by-ticket) or run id (searches all runs, not only recent ones). Each run is followed by its tags; click one to search by it. **Flows** (流程): all flow definitions. **+ New run** (＋ 新运行). The **Waiting on you** list (等你处理): questions, approvals, loops that ran out of rounds, watchdog reminders. The menu in the top right corner switches the theme |
| New run | First pick the project, then the task (this project's flows), then fill in the fields the flow asks for and the knowledge sources. Finally write the first message and click **Start** (启动). The first message is written verbatim to the run's `docs/external/request.md`; `task` takes its first line |
| Left pane | The run's flow. Steps are laid out in execution order by `needs`, parallel steps side by side; loops can switch between rounds. The step that is running and the steps waiting on you are highlighted |
| Middle | The run's conversation: your request, each step's progress and digest, the agent's questions, your replies, approvals, the pushed branch, the end |
| Right pane | **Step** (步骤): digest, outputs, artifacts, logs. While a step runs, **Live output** (实时输出) shows output as it is produced. **Docs** (文档): the workspace's `docs/`; images (for example ticket screenshots) are shown directly. **Changes** (改动): the diff against base, the review conclusion, the risk verdict |

Where the input box sends its content depends on the run's state; the text above the input box says which:

| Run state | Input box and buttons |
|---|---|
| A step is asking a question | The reply goes to that step unchanged; it continues its earlier session |
| Waiting for approval | **Approve** (批准), **Reject** (驳回). If the approval has `changes` configured, there is also **Request changes** (提修改意见): write the feedback in the input box, and the run goes back to the review loop to make changes. The input box content also serves as the approval note |
| Running | Cannot send. Shows the step that is running. **Cancel this run** (取消这个运行) is at the bottom right |
| Failed or cancelled | Cannot send. **Re-run failed steps** (重新执行失败的步骤) is at the bottom right; it is the same as [`resume`](#resume) |
| Finished | Cannot send. development-type flows have **Continue on this branch** (在这个分支上接着改): it starts a new run with `from_run` set to this run (see [`run`](#run)). To continue other flows, start a new run |

What you say in the console is also recorded in `myorch-data/ui/runs/<run_id>.jsonl`. After the steps move on, the questions and answers are still visible in the conversation. For how the console works, see [3. Architecture](03-architecture.md#how-the-console-works).

### `serve`

```sh
uv run myorch serve [--port 8800] [--host 127.0.0.1]
```

Starts the console, the channel relay and the platform adapters in one process. At startup it prints `myorch serve: console http://127.0.0.1:8800/ · relay /channel/`.

| Path | Content |
|---|---|
| `/` | Console |
| `/channel/...` | The HTTP and SSE endpoints of the channel relay. The list of endpoints is in the notes at the top of `src/myorch/channels/http.py` |

- `myorch ui` is the same as `myorch serve`.
- The watchdog and the Slack adapter run in this process. Slack starts only when both tokens are set (in compose they come from `deploy/secrets/slack.env`).
- Only one `myorch serve` or `myorch ui` can be open at a time.

Design: [notes/2026-11-02-channel-layer.md](../notes/2026-11-02-channel-layer.md).

---

## Start runs

### `run`

```sh
uv run myorch run <flow file> [--project <name>] [-i KEY=VALUE ...] [--label <tag> ...] [--kb <name,...>] [--set flows.KEY=VALUE ...] [--wait] [--as <name>]
```

Starts a flow. The run_id is printed to standard output. The mounted knowledge sources and auto-sync notices are printed to standard error.

| Argument | Purpose |
|---|---|
| `<flow file>` | For example `flows/development.yaml`. Flows are shared by all projects and shaped by `--project` |
| `--project` | Project pack name. Required when the flow uses `{{project.x}}` or `if:` (all current software development flows do). If the flow file has `projects:`, it must be one of them. The inputs the project requires are checked (for example mdm requires `ticket`) |
| `-i KEY=VALUE` (`--input`) | A flow input; can be given several times. Inputs not given take the flow's `defaults` |
| `--label` | Adds one more tag to the run, for example `--label MD-18700`; can be given several times. Ticket inputs and ticket numbers in inputs do not need it; they are tagged automatically, see [Tags](#tags-finding-runs-by-ticket) |
| `--kb` | Adds to or removes from the project's default knowledge sources: `a,b` adds, `-a` removes. The argument is called `--kb` because `--knowledge` is the global argument for the project pack directory |
| `--set` | Only for replays (`-i at=...`): this run uses a different flow setting, for comparison in evals. Only keys under `flows.` can be changed, for example `flows.development.reviewers=1`, or `flows.ablate=nit-cap,entry-points` to remove rules from the prompts (see [8. Writing flows · Rules in prompts](08-flow-syntax.md#rules-in-prompts-rule)). The changed project settings are recorded in the mandate, the run gets a `set:<key>=<value>` tag, and evals group by the tag |
| `--wait` | Waits for the run to finish, then prints the final state (same as `status`) |
| `--as` | The initiator's name, recorded in the mandate. Defaults to the local user name. When approval is needed, the initiator usually must be one of the approvers |

Before submitting, the client does the following in order. If any step fails, the command reports an error and no run is started:

1. Reads the flow's input spec for the project (inputs whose `if:` does not hold become hidden, `required` expressions are evaluated, the prefix comes from the project's Jira key), then reads the inputs. Ticket inputs keep only the number (`MD-18700` and `18700` both become `18700`; the branch name template adds the prefix back).
2. If the flow has `projects:`, checks that `--project` is in it.
3. If the flow has a `runs` input (learn, postmortem) and it is not given, or is given as `auto`, picks the runs automatically; see "learn and postmortem" below.
4. If there is an input with `type: run` (for example development's `from_run`), checks that run; see "Continuing from a run" below.
5. Checks the required inputs (a missing one fails with `请先填：<field name>`, "fill in first") and the inputs the project requires.
6. Syncs and picks the snapshots of the knowledge sources: `dir` knowledge sources are synced every time (nothing is copied when nothing changed); `git` knowledge sources are synced again only when the latest snapshot is more than 1 hour old. If a sync fails, the previous snapshot is used.
7. Reads the flow file → picks parameters with presets → shapes it for the project (`if:`, project values filled into `with:`) → expands `use:` fragments → fills in project values (including the clauses from `prompts/`) → checks.

```sh
uv run myorch run flows/development.yaml --project mdm -i ticket=18700
uv run myorch run flows/development.yaml --project blog -i task="Add a word count to the article page"
uv run myorch run flows/development.yaml --project myorch -i task="..." -i incident=development-1a2b3c4d
uv run myorch run flows/review.yaml --project blog -i pr=13 -i depth=quick
```

**Continuing from a run.** development-type flows have a `from_run` input (`type: run`):

```sh
uv run myorch run flows/development.yaml --project mdm -i from_run=development-1f29e33a -i task="Found in self-testing: ..."
```

- That run must have finished, belong to the same project, and have the development role. Otherwise the command reports an error.
- The new run continues on that run's branch. If `ticket` and `incident` are not given, it takes that run's.
- What people said to that run (questions and answers, change requests, approval notes) is written to the new run's `docs/previous/conversation.md`.
- **Continue on this branch** (在这个分支上接着改) in the console does the same thing.

**learn and postmortem.** When `runs` is not given, or with `-i runs=auto`:

- learn: the finished development and review runs of this project since the last learn. The picked runs are printed to standard error. If there are none, the command reports an error.
- postmortem: this project's most recent development and review runs.
- Both require `--project`. You can also pick by hand: `-i runs=<id>,<id>`.

For each flow's inputs and what each step does, see the [Flow manual](workflows/README.md).

### `expand`

```sh
uv run myorch expand <flow file> [--project <name>] [-i KEY=VALUE ...] [--set flows.KEY=VALUE ...]
```

Prints the full expanded flow (fragments, presets, project values and `prompts/` clauses substituted, shaped by the project's `if:`) without starting anything. After changing a flow or a fragment, check with it first, ideally once for each project (`--project mdm`, `--project blog`, `--project myorch`). Inputs such as `-i depth=deep` select presets. Required inputs are not checked. `--set` is the same as in `run`; use it to preview the prompt with a rule removed. It does not have to be a replay.

### `schedule`: scheduled runs

```sh
uv run myorch schedule add <name> --flow <flow> [--project P] (--cron '0 2 * * *' | --every 1h) [-i k=v ...] [--kb a,b] [--paused] [--allow-overlap] [--as <name>]
uv run myorch schedule list                       # when each fires and when it fires next
uv run myorch schedule pause|unpause|delete|trigger <name>
```

| Argument | Purpose |
|---|---|
| `--flow` | `flows/<name>.yaml` or just `<name>` |
| `--cron` / `--every` | One of the two. cron uses the local time zone. `--every` takes an interval such as `1m`, `6h`, `1d` |
| `--kb` | The knowledge sources to mount, as a complete list (unlike the add/remove form of `run`). If not given, the project's defaults are used |
| `--paused` | Paused after it is created |
| `--allow-overlap` | Starts even if the run started last time has not finished. By default that firing is skipped |
| `--as` | The initiator of the runs it starts. Defaults to the person who created the Schedule |

- It uses a Temporal Schedule. `trigger` fires it once right away.
- The flow is expanded and the knowledge source versions are resolved only when it fires. So changes to `flows/` or newly synced snapshots take effect at the next firing.
- The run's source is recorded as `schedule:<name>`.

---

## View runs

### `list`

```sh
uv run myorch list [--limit 20] [--project <name>] [--flow <flow>] [--label <tag> ...]
```

Recent runs, one per line: time, project, flow, state, run_id, tags, last column. `myorch runs` is the same command.

```
11-10 11:19  mdm       development COMPLETED  development-b3538c83               MD-18633
11-10 10:30  mdm       eval-validate-checks FAILED     eval-validate-checks-dbd70585      MD-18633
11-10 09:02  mdm       development RUNNING    development-1f29e33a               MD-18700     等你处理: human-review
```

- The state is Temporal's state: `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED` and so on. Temporal keeps runs for only 24 hours after they finish; older ones show `UNKNOWN`. The console's list fills in their state from the event log.
- Last column: while a run is running, the steps waiting on you (等你处理: questions, approvals, loops that ran out of rounds). Otherwise the source link; for runs before 2026-11-06 it may be the PR the run opened (myorch does not open PRs now).
- `--flow` takes the full flow name, for example `development`. It matches the name exactly only; for runs before 2026-11-10, use the old name (`ck-development`).
- `--label MD-18700`: lists only runs with this tag (case-insensitive; `18700` alone also works), or runs whose run_id contains it (`--label 1f29e33a`). It searches all runs first, then takes `--limit` of them. When given several times, all must match.

### Tags: finding runs by ticket

Every run carries tags (`labels`), mainly the Jira tickets it belongs to. They are for finding runs. Tags come from:

1. Given explicitly at start: CLI `--label`, `labels` of HTTP `POST /channel/runs`, `labels` of the Claude Code channel's `start` tool; eval replays (`myorch eval run`) carry the case's ticket (if the case has one).
2. Flow inputs: an input with `type: ticket` plus its `prefix` (in mdm's development, `ticket=18700` is `MD-18700`; the prefix is resolved from the run's project, and old `ck-development` runs are resolved by `development.yaml`); ticket numbers written in plain string inputs (`tickets: "MD-1,MD-2"`, `MD-18700` in a branch name). Only uppercase `ABC-123` counts; things like `UTF-8` and `SHA-256` do not. Free text such as `task` is not scanned.
3. The tags of another run referenced in an input, one level only: `from_run`, or an input whose value is exactly a run id (for example `-i run=development-1f29e33a`). A comma-separated batch of runs (learn's `runs`) does not count.

At start, 1, 2 and whatever of 3 can be read are recorded in the mandate (`labels` in `.myorch/mandate.json`) and in the `run_started` event. When listing runs, rules 2 and 3 are applied again, so old runs from before tags existed can also be found. The rules are in `core/inputs.py` (tags) and `runs.py` (derivation when listing).

Where to search: the search box of the console's run dropdown; `myorch list --label`; `GET /api/runs?label=`, `GET /channel/runs?label=` (also accepts `q=`); the `label` parameter of the Claude Code channel's `runs` tool. The console and the endpoints also list old runs that Temporal has already deleted (finished more than 24 hours ago); their state comes from their `run_finished` event.

### `status`

```sh
uv run myorch status <run_id>
```

The full state of one run: the mandate (initiator, time, flow and project pack versions), the approval records and change requests, the state of each step and loop, digests, outputs, errors, the model used. When the run is waiting on you, `status` prints the command to run:

```
run development-1f29e33a  flow=development  status=running
  mandate: 发起人 jakobhe · 2026-11-10T01:02:11+00:00 · flow 2367c7f4 · project mdm@c4f0a3d1
  ...
  ⏸ human-review         waiting_approval
      还需要 1 人批准 → myorch approve development-1f29e33a human-review
      要修改 → myorch changes development-1f29e33a human-review "<修改意见>"
```

```
  ? develop              waiting_input     [opus-5-5]
      question: Should the old API stay compatible?
        1. Keep it compatible
        2. Drop compatibility
      → myorch reply development-1f29e33a develop "<回复>"
```

State icons:

| Icon | State |
|---|---|
| `·` | Pending |
| `▶` | Running |
| `⏸` | Waiting for approval |
| `?` | Waiting for an answer |
| `✓` | Succeeded |
| `✗` | Failed or rejected |
| `-` | Blocked upstream |
| `↷` | Condition not met, skipped |
| `⊘` | Cancelled |
| `⟳` | Loop (followed by the round and the `until` condition) |

A step line may end with `attempts=N` (number of attempts), `turns=N` (number of turns), `exit=N` (non-zero exit code), `[model]`.

`status` reads from Temporal. For a run that finished more than 24 hours ago, Temporal has deleted it and the command reports `workflow not found`. Use the console then, or read its workspace with `ws` or `export`.

### `review`

```sh
uv run myorch review <run_id> [--no-open]
```

Writes what an approval needs to look at into a local web page, then opens it in the browser with macOS `open`. The page contains:

1. The requirements (the agent's understanding) and the original request
2. Changes: the commit list and the diff (against `origin/<base>`)
3. Check results. If the project has end-to-end tests, also the results of the smoke and acceptance cases
4. The verified review findings of the last round
5. The risk verdict
6. The implementation notes

While the run waits for approval, the top of the page says what is still missing and gives the approve / reject commands. The page is written to `myorch-data/reviews/<run_id>.html`, and the command prints this path. `--no-open` only writes and does not open; it is required in the container. The console's **Changes** (改动) tab shows the same content.

### `logs`

```sh
uv run myorch logs <run_id> <instance> [--bytes 20000]
```

The log of an instance's latest turn (the last N bytes): the last turn of the attempt with the highest number. Use the instance ID shown in `status`, for example `review.r2.x1`.

- Can be read while the step runs; it holds the output so far.
- The log of an agent step is a readable activity record: the text the agent writes, tool calls starting with `→`, tool results starting with `←` (only the first few lines), and the final result.
- The raw stream-json events are in `turn-<attempt>-<turn>.jsonl` in the same directory.
- The logs of earlier attempts are also in this directory: `.myorch/steps/<instance>/logs/turn-<attempt>-<turn>.log`; read them with `myorch ws`. Runs before 2026-11-06 have `turn-<turn>.log`, only for the last attempt.

### `out`

```sh
uv run myorch out <run_id> <instance>
```

An instance's manifest and the list of files under `out/`. If there is no manifest, it prints `manifest: (none)`. The manifest of a host action (for example learn's `project-apply`) is also written to `out/`.

### `ws`

```sh
uv run myorch ws <run_id>                         # list the workspace files (3 levels by default)
uv run myorch ws <run_id> --depth 5
uv run myorch ws <run_id> docs/requirements.md    # read one file
```

Views the run's workspace (`runs/<run_id>/` in the volume):

| Path | Content |
|---|---|
| `repo/` | Code |
| `docs/` | Requirements, review records, CI reports. `docs/external/` is external content (request, ticket); `docs/previous/` is the previous run's material when continuing on the same branch |
| `memory/MEMORY.md` | Notes shared by the steps within the same run |
| `.myorch/` | Flow snapshot, mandate, each step's artifacts and logs (`steps/<instance>/`) |
| `.claude/<instance>/` | The agent's session records |

If the file does not exist, it prints `(no such file: <path>)`.

### `export`

```sh
uv run myorch export <run_id> [target directory]
```

Copies the workspace from the volume to a local directory and prints how many files were copied. The default target is `../myorch-data/runs/<run_id>/`. Use it when you want to browse the artifacts in an editor. `repo/.git`, `repo/node_modules`, `repo/_site`, `repo/.jekyll-cache` and `.claude/` are not copied.

---

## Intervene in runs

### `reply`

```sh
uv run myorch reply <run_id> <step or loop> "<reply>"
```

| What is waiting for an answer | Reply |
|---|---|
| An agent's question | The reply is the input of the next turn; the same session continues in a new sandbox |
| A loop that ran out of rounds | `1` (run one more round), `2` (accept the current result and continue with the later steps) or `3` (stop the flow) |
| A failed step (`on_failed: ask`) | `1` (start over, new session), `2` (skip this step and continue with the later ones) or `3` (stop the flow) |
| The `choose` step of learn / onboard | The numbers of the proposals to adopt, for example `1,3,4`, or `all`, `none` |

Accepted only while the target is waiting for an answer (`waiting_input`); otherwise it reports `'<step>' is not waiting for this (status: ...)`. A reply cannot be changed after it is sent.

### `approve` / `reject`

```sh
uv run myorch approve <run_id> <step> [--as <name>]
uv run myorch reject  <run_id> <step> [--as <name>]
```

Approves or rejects a step in `waiting_approval`.

- When several people are needed, each person approves once with a different `--as`. `--as` defaults to the local user name.
- If the approval has `require_initiator` (the default), the initiator must be one of the approvers. `status` shows what is still missing.
- After a rejection, the step fails and the run stops. Downstream steps are not executed.
- `--as` is self-declared and not authenticated. It guards against mistakes, not against forgery.

### `changes`

```sh
uv run myorch changes <run_id> <step> "<change request>" [--as <name>]
```

Neither approves nor rejects: sends the run back from the approval gate with a change request. Only steps whose approval has `changes` configured accept it (for example development's `human-review`: back to the `review-fix` loop, at most 2 more rounds).

- The change request is written to the approval record and handed to the steps in the loop to make the changes. The later steps (including this approval) run again, and the approval comes back after the changes.
- The change request cannot be empty. A step without `changes` configured reports an error; it can only be approved or rejected.
- The **Request changes** (提修改意见) button in the console and the button of the same name in Slack do the same thing.

### `cancel`

```sh
uv run myorch cancel <run_id>
```

Cancels a run. myorch stops the step that is running, deletes its sandbox and does not retry. Unfinished steps are marked `cancelled`.

### `resume`

```sh
uv run myorch resume <run_id> [--step <step> ...] [--wait] [--as <name>]
```

Resumes a failed or cancelled run from the failed steps. Use it when steps failed for external reasons (timeout, token permissions, network) and the cause has been fixed. Prints `<run_id>: running again <instance,...>`.

- Uses the same run ID, the same workspace and the same mandate. Steps that succeeded (including approved approvals) keep their results and do not run again.
- Steps that run again: failed and cancelled steps, and the steps blocked by them. The attempt count continues (2nd attempt, 3rd attempt).
- `--step` re-runs only the given steps. It takes an instance ID (`review.r1.x2`) or a step ID (`review`, i.e. all of its failed copies). Can be given several times.
- A rejected approval is not reopened. A run that has not finished is refused. Runs that Temporal has already deleted (finished more than 24 hours ago) cannot be resumed.
- `--as` is recorded in the `run_resumed` event; defaults to the local user name.
- You can also do this in the console. For a failed or cancelled run, the **Re-run failed steps** (重新执行失败的步骤) button is at the end of the conversation and at the bottom right of the input box. A failed step's detail page has a **Re-run from this step** (从这一步重新执行) button.

The run writes a `run_resumed` event (who, which steps), and a new `run_finished` when it ends.

---

## Project packs

```sh
uv run myorch project list                                 # all projects and templates, whether synced, repository and base branch
uv run myorch project show <name>                          # the full configuration after merging templates (JSON)
uv run myorch project sync <name>                          # writes the project pack snapshot into the volume (required after changing a project pack)
uv run myorch project github-profile <name> [--read-only]  # generates this project's OpenShell GitHub provider profile
```

- `sync` prints `project <name>: N files synced (sha256:...)`. Each sync is a new version. New runs use the latest version, and the mandate records which one.
- `github-profile` by default allows push and API reads for this repository, and read-only clones of submodules. `--read-only` removes push, PRs and comments; that is enough for onboard and for reading PRs.

Details: [7. Project packs](07-projects.md).

---

## Knowledge sources

```sh
uv run myorch knowledge list [--project <name>]         # registered knowledge sources, latest snapshot, problems
uv run myorch knowledge sync <name> [--project <name>]  # snapshots a dir or git knowledge source into the volume
uv run myorch knowledge check <name> [--project <name>] [--image <image>] [--call <tool> [--args '<JSON>']]
```

- Without `--project`, only the shared knowledge sources (`myorch-knowledge/_sources/`) are included. With it, the knowledge sources of this project and its templates are included too.
- Each line of `list` is: name, type (`dir`, `git`, `mcp`), level, latest snapshot, location. The reason a source cannot be used is on the next line, starting with `!`.
- Knowledge sources are synced automatically when a run starts, so `sync` is usually not needed (see step 6 of [`run`](#run)). Use `sync` to sync in advance, or to see why a sync fails. When nothing changed, it prints `unchanged`.
- When `sync` finds a suspected secret, it does not write the snapshot and lists "file:line". After confirming it is a false positive, add it to `allow_findings` in the registration file.
- Syncing a git knowledge source happens in a sandbox (named `kb-<source>-<hash>`), so run it in the container.

**`knowledge check`** checks only `mcp` knowledge sources, the same way a step uses them:

1. Starts a sandbox `kcheck-<random>` with the project's image (or `--image`), the knowledge source's provider and `/sandbox/bin`.
2. Runs `myorch-mcp --check` in it: connects to the server and compares its tools with the registered tools.
3. With `--call`, also calls this registered tool once, with the JSON from `--args` as arguments (default `{}`).

The exit code is 0 when everything is fine. The token does not leave the gateway. It needs a sandbox, so run it in the container:

```sh
dc exec worker myorch knowledge check atlassian --project mdm --call getJiraIssue --args '{"issueIdOrKey": "MD-18785"}'
```

For how to register one, see [7. Project packs · Knowledge sources](07-projects.md#knowledge-sources).

---

## Reviewer evals

```sh
uv run myorch eval collect                                    # finds candidate cases in the round-1 review findings of past runs
uv run myorch eval list                                       # cases, and whether they are confirmed
uv run myorch eval run [--depth quick --depth standard] [--case <id> ...] [--as <name>]
uv run myorch eval report [--batch <B>]                       # how many each configuration caught, missed and over-reported
```

1. `collect` finds candidate cases. In the console under **Flows → Eval set** (流程 → 评测集), tick the findings that really hold to confirm a case.
2. `run` starts one replay run for each confirmed case and each `--depth` (`quick`, `standard`, `deep`; default `standard`). `--case` runs only the given cases. Prints the batch number.
3. `report` summarizes. `--batch` shows only that batch; it can also be the ID of a postmortem run, to show only its two replays.

`report` reads the event log; on the host, start the console first. For the steps of the eval flow, see the [Flow manual](workflows/README.md).

---

## Other common commands

Run `openshell` in the container (`dc exec worker openshell ...`). `openshell` on the host connects to the brew gateway, which is stopped.

| Command | Purpose |
|---|---|
| `dc exec worker openshell provider list` | See which credentials the gateway has |
| `dc exec worker openshell sandbox list` | See which sandboxes exist now |
| `dc exec worker openshell sandbox delete <name>` | Delete a leftover sandbox |
| `dc restart gateway` | Restart the gateway |
| `dc logs <service>` | Logs of a compose service, for example `dc logs worker`, `dc logs ui` |
| Temporal UI http://localhost:8233 | See workflow history, events, error stacks |

Sandbox names:

| Name | What it is |
|---|---|
| `<flow>-<step>-<hash>` | One turn of a step. For example `dev-deve-3f2a9` is development's develop. Flow abbreviations: `development` → `dev`, `review` → `rev`, `learn` → `lrn`, `postmortem` → `pm`, `sweep` → `swp`, `eval` → `evl`, `onboard` → `onb`; old `ck-development` runs are `ck-dev`. The name does not contain the project; to see the project, find the run with the label `myorch.run`. The step is its first 4 letters; the hash is taken from the run, step, attempt and turn |
| `kb-<source>-<hash>` | Syncing a git knowledge source |
| `kcheck-<random>` | `myorch knowledge check` |
| `job-<hash>` | Other one-off jobs (for example the watchdog's judgment) |

OpenShell limits names to 19 characters, so the names are abbreviated. The full run and step are in the sandbox labels `myorch.run`, `myorch.step`, `myorch.attempt`, `myorch.turn`. The container name in Docker is `openshell-default--<name>-<uuid>`.
