# 11. Troubleshooting

`dc` is short for `docker compose -f deploy/compose.yaml`, run in the `myorch/` directory. On the host, `openshell` connects by default to the brew gateway (which is stopped), so run `openshell` commands in a container (`dc exec worker openshell ...`).

## Where to look first

| To find out | Look at |
|---|---|
| Whether all services are up | `dc ps -a`: `gateway-init`, `providers` and `init` are `exited (0)`, the others are `running`. More in [4. Setup · Starting the day](04-setup.md#43-starting-the-day) |
| Which step a run is stuck on, the error message | The step details in the console's left and right columns, or `uv run myorch status <run>` |
| What an agent or script printed | The log in the console's right column, or `uv run myorch logs <run> <instance>` |
| Artifacts, manifests | `uv run myorch out <run> <instance>`, `uv run myorch ws <run> [path]` |
| Problems at the workflow level (replay failures, activity stack traces) | The Temporal UI at http://localhost:8233 → find the run → History |
| Whether sandboxes were left behind | `dc exec worker openshell sandbox list`. The naming rule is in [5. CLI reference · Other common commands](05-cli.md#other-common-commands), e.g. `dev-deve-3f2a9` |
| Whether the credentials are there | `dc exec worker openshell provider list` |
| Whether the project pack is synced | `dc exec worker myorch project list` |
| What the attempts before a retry did | `.myorch/steps/<instance>/logs/turn-<attempt>-<turn>.log` (`myorch ws <run> <path>`, or the Claude Code channel's `log` tool with `attempt=A`). Retries do not overwrite them, and the session transcripts (`.claude/<instance>/projects/*/<session ID>.jsonl`) are kept too |
| Which session a retry after a platform interruption continues | The `continues the session` line in the worker log (`dc logs worker`). The retry's prompt starts by saying that the previous attempt was interrupted by the platform (上一次执行被平台中断了) |
| After change requests re-run a step, the log numbers do not match | The re-run's attempts continue the earlier numbering (`attempts_before`), while the console's "attempt N" (第 N 次尝试) counts again from 1. To find the newest, take the largest attempt in the file names |
| Network blocked, DNS, credentials not injected | The step's supervisor log: `.myorch/steps/<instance>/logs/supervisor-<attempt>-<turn>.log` (`myorch ws <run> <path>`, or the Claude Code channel's `log` tool with `which=supervisor`) |
| Logs of compose services | `dc logs <service>`, e.g. `dc logs ui` (console, Slack, watchdog), `dc logs worker`, `dc logs providers` |

## Common problems

### Services and connections

| Symptom | Cause and fix |
|---|---|
| Commands cannot connect to `localhost:7233` | Temporal is not up. Check `temporal` with `dc ps`, then `dc up -d temporal`. Without compose, run `temporal server start-dev --db-filename ../myorch-data/temporal.db` |
| The console does not open | The console connects to Temporal at startup. See why with `dc logs ui`. Without compose, start Temporal first, then `uv run myorch ui` |
| `uv run myorch ui` on the host says the port is in use | The compose `ui` already holds 8800. While compose runs, do not also start `ui`, `serve` or `worker` on the host: they open `events.db` directly; see the Event log section below |
| A browser or script gets 403 `bad host` from the console | The console accepts only requests whose Host is `127.0.0.1:<port>` or `localhost:<port>`. Open `http://127.0.0.1:8800/`, not a LAN IP or another host name. From another computer, use SSH port forwarding (see `deploy/README.md`) |
| A script POSTing to the console gets 403 `missing X-Myorch-UI header` | The console's write endpoints need the request header `X-Myorch-UI: 1`, against cross-site requests |
| Neither the console nor runs respond, and `dc ps` shows nothing | Docker is not running. Open Docker Desktop; the services start on their own |
| `run` printed a run_id but stays `pending` | The worker is not up. Check `worker` with `dc ps`; it waits for `init` and `providers` to succeed (see the next section). Without compose, run `uv run myorch worker` |
| `unrecognized arguments: --config .` | Global arguments (`--config`, `--address`, `--data` etc.) go before the subcommand: `uv run myorch --config . worker`. When you run in the `myorch/` directory you do not need it |
| A change to `src/` has no effect | Rebuild and restart: `dc up -d --build worker ui`. Without compose, restart `uv run myorch worker` and `uv run myorch ui` |
| After changing workflow code an old run hangs and Temporal reports nondeterminism | The old run's history does not match the new code. Cancel it and start it again. Prevention: [10. Conventions](10-conventions.md#code). When `deploy/update.sh` sees changes to `src/myorch/engines/` or `src/myorch/core/run.py`, it waits for all unfinished runs to end before releasing |
| `status` or `resume` reports `RPCError: workflow not found for ID: ...` | Temporal keeps runs only for 24 hours after they end. For older runs, use the console (the status comes from the event log), or read the workspace with `myorch ws` or `myorch export` |
| Old runs show `UNKNOWN` in `myorch list` | Same as above: Temporal has deleted them. The console's list shows the final status from the event log |

### Event log

| Symptom | Cause and fix |
|---|---|
| A command on the host reports `读不到事件日志：控制台（http://127.0.0.1:8800）没有响应` (cannot read the event log: the console at http://127.0.0.1:8800 is not responding) | Processes on the host read events through the console and do not open `events.db` directly. Commands that use the event log: `run` with `from_run` or with another run's id in an input, the learn flow, `eval report`. Start the console first; if the console is at another address, set `MYORCH_URL`. Or run the command in a container (`dc exec worker myorch ...`) |
| The console returns 500 and the log shows `sqlite3.OperationalError: disk I/O error` | See "Console 500: disk I/O error" below |

### docker compose

| Symptom | Cause and fix |
|---|---|
| `ui` stays `Restarting` | Reading the configuration failed at startup; see `dc logs ui`. A common cause is a mistake in `channels.yaml`: in a YAML flow mapping a colon needs a space after it, `{github: leweii, slack: U...}`, not `{github:leweii}` |
| A change to `channels.yaml` has no effect | It is read at startup. Run `dc up -d --force-recreate ui worker` (the containers must be recreated, because editors replace the file when saving) |
| The worker does not start and `providers` is `exited (1)` | The credentials file is wrong: only some values filled in, or a type that does not exist. See why with `dc logs providers`, fix it, run `dc run --rm providers`, then `dc up -d` |
| Runs started from the console have `root` as initiator | The container does not have the host's user name. Compose passes in `USER`. When you start compose in an environment without a login shell, set `MYORCH_USER` in `deploy/.env` |
| After changing `flows/`, the console, Slack and scheduled triggers still use the old version | Flow files are baked into the image. Run `dc up -d --build worker ui`. `uv run myorch run` on the host reads `flows/` from the working tree, so the two can differ |
| After `deploy/update.sh` released, local changes are missing | It builds the image from a clean checkout of origin/main. Changes that are not committed and pushed do not get into the image |
| `deploy/update.sh` says the last release failed the smoke test and was rolled back (上次发布冒烟失败、已回滚) | The commit is recorded in `deploy/.failed`. It is not released again until origin/main has a new commit. To see why the smoke test failed: `dc exec worker myorch run deploy/smoke.yaml --wait` |
| `deploy/update.sh` says runs were still going after waiting ...s, so nothing was released (等了 ...s 仍然有运行在跑，没有发布) | Steps kept running (or, when workflow code changed, there were unfinished runs). Wait for them to end, or raise `WAIT_MAX` |
| Downloading openshell times out while building an image | The build downloads the openshell CLI from `github.com`. First check that this machine can reach github.com |
| New runs clone fully from GitHub instead of using the local clone | `local-repos.yaml` holds host paths, which do not exist in the container. This is a known limitation; see `deploy/README.md` |

### Slack

| Symptom | Cause and fix |
|---|---|
| The bot replies that your Slack account (`U...`) is not registered (你的 Slack 账号（`U...`）没有登记) | Write the member ID from the reply (it starts with `U`) into `channels.yaml`: `jakobhe: {github: leweii, slack: U...}`, then `dc up -d --force-recreate ui worker` |
| Registered, but it still does not work | You may have registered a channel ID (starts with `D`, the direct-message channel) instead of the member ID. Only what profile ⋮ → Copy member ID copies starts with `U` |
| `/myorch` does nothing | The adapter is not connected. `dc logs ui` should show `slack: connected (Socket Mode)`. If it shows `SLACK_BOT_TOKEN must start with xoxb- ...; Slack stays off`, the two tokens are swapped or wrong. If neither appears, check that `deploy/secrets/slack.env` exists and that the app has Socket Mode turned on |
| The run started, but there is no thread in the direct messages | The initiator has no registered `slack` account; this run does not go to Slack |
| After clicking start in the start dialog, the dialog stays open and says to fill in this task's fields first, then click start (先填这个任务的字段，再点启动) | You submitted too fast, before the dialog had drawn this task's fields. Fill in the fields, then click start again |
| The start dialog reports that it did not start (没有启动：...) | What follows is the reason, the same as the CLI's submission errors; see "Submission errors" below |
| You reply in the thread and the bot says this run is not waiting for a reply now (这个运行现在没有在等回复) | The run is not asking a question. To continue on a finished run's branch, click **Continue on this branch** (在这个分支上接着改) in the console |
| Slack runs on two machines and events arrive only sometimes | When two machines use the same token, Slack hands events randomly to either one. Keep `slack.env` on one machine only |

### Claude Code channel

| Symptom | Cause and fix |
|---|---|
| There is no `Channels (experimental) messages from server:myorch` below the startup banner | The session was not started with `claude --dangerously-load-development-channels server:myorch`, or there is no `.mcp.json` in the start directory (to use it from another directory, register it as a user-level MCP server; see `deploy/README.md`) |
| No pushes arrive at all; Claude Code's MCP log (`~/Library/Caches/claude-cli-nodejs/<project path>/mcp-logs-myorch/*.jsonl`) shows `Channel notifications skipped: channels are not available on third-party providers` | Claude Code runs on Vertex AI, Bedrock or Foundry (e.g. the env var `CLAUDE_CODE_USE_VERTEX`). Channels work only with a claude.ai account or a Console API key. myorch sends as usual (the cursor moves forward); the client drops the notifications. Start the session with Anthropic authentication (`env -u CLAUDE_CODE_USE_VERTEX ... claude ...`), or skip the channel and poll `GET /channel/events.json?after=<seq>` (e.g. with Monitor on a timer) |
| The channel is registered, but no pushes arrive | Check `myorch`'s status with `/mcp`. Start with `claude --debug ...` and read `~/.claude/debug/<session-id>.txt`. The cursor in `~/.cache/myorch/claude-code-<person>.json` should move forward as runs progress |
| Only your own runs arrive | The default scope is `mine`. To see all runs, set `MYORCH_SCOPE=all` in `.mcp.json` |
| The agent says it cannot approve | This is by design: only people approve, reject and request changes, in the console, Slack or the CLI. The interface rejects these commands from `claude-code` (403) |

### Submission errors

`run` checks before submitting and does not start the run when something is wrong. The console, Slack and scheduled triggers report the same errors.

| Symptom | Fix |
|---|---|
| `--input expects KEY=VALUE` | `-i` must be followed by `name=value` |
| `流程 X 只给项目 ... 用，--project 要写其中一个` (flow X is only for projects ...; --project must name one of them) | The flow file's `projects:` limits the projects. Add the right `--project` |
| `请先填：<字段名>` (fill in first: `<field name>`) | A required input of the flow is missing. For example, mdm's development needs `-i ticket=18700` |
| `project X needs inputs: ticket (-i ticket=...)` | The project requires this input. Add it, or fill it in in the console |
| `project 'X' not found: create ...` | The project pack directory does not exist. Check the name, or the directory `--knowledge` points to |
| `{{project.x}}` is used without `--project`, or `... which depends on the project: run it with --project <name>` | Add `--project <name>`. Shared flows are trimmed per project (`if:`), so the project must be known |
| `project 'X': .../prompts/<file> is not a prompt slot` | Files in the project pack's `prompts/` may only be named `develop.md`, `review.md`, `verify.md`, `fix.md`, `risk.md`. Rename or delete the file |
| `flow references project.<field>, which the project does not define` | The flow refers to a field the project does not have. Check the spelling, or add it to `project.yaml` |
| The run fails right at the start with `project not synced: X` in the error | Run `uv run myorch project sync X` |
| Errors in fragment parameters or presets | Fix what the error says. Reproduce with `uv run myorch expand <flow> ...` |
| `runs=auto needs --project` | When learn or postmortem picks runs automatically, it must know the project. Give `--project` |
| `project X: 上次 learn ... 之后没有新的已结束的 development / review run` (no new finished development / review run since the last learn ...) | There are no new runs to learn from. Or pick runs by hand with `-i runs=<id>,<id>` |
| `没有这个运行：<id>` (no such run: `<id>`) | The run in `from_run` does not exist; check the run id |
| `<id> 不是 development 运行，不能接着它做` (`<id>` is not a development run, cannot continue from it) | `from_run` can only name a run of the same role (development) |
| `<id> 属于项目 A，不是 B` (`<id>` belongs to project A, not B) | The `from_run` run must belong to the same project as `--project` |
| `<id> 还没有结束。等它结束，或者先取消它` (`<id>` has not ended yet; wait for it to end, or cancel it first) | The run you continue from must have ended |

### Knowledge sources

| Symptom | Cause and fix |
|---|---|
| `知识源 X 没有登记，或者项目 P 不能用它` (knowledge source X is not registered, or project P cannot use it) | The name is misspelled, or it is a project's knowledge source and `--project` is missing. See what exists with `myorch knowledge list --project P` |
| `知识源 X 没有快照（同步没有成功）` (knowledge source X has no snapshot (sync did not succeed)) | The automatic sync at run start failed. The `myorch knowledge sync ...` command given in the run's error shows why |
| `知识源 X 里有疑似密钥，没有同步` (knowledge source X contains suspected secrets, not synced) | Delete the listed content. Once you have confirmed a false positive, add "file:line" to `allow_findings` in the registration file |
| `目录不存在：<路径>` (directory does not exist: `<path>`) | The `path` of a `dir` knowledge source is wrong, or it does not exist on this machine |
| `控制台和 worker 只能读宿主机 ... 下的目录` (the console and worker can only read directories under ... on the host) | The containers mount `MYORCH_KB_ROOT` (default `~/orca/kb`) read-only. Put the folder under it, or start the run from the command line on the host. In the container, `~` refers to the host's home |
| `... 是一个 Obsidian vault（有 .obsidian/），不能直接挂` (... is an Obsidian vault (has .obsidian/), cannot be mounted directly) | Vaults often contain secrets. Mount a folder you have curated |
| `路径 ... 里的环境变量没有设置` (an environment variable in path ... is not set) | `path` uses an environment variable that is not set |
| `git 知识源 X 需要一个带 git 和 python3 的镜像` (git knowledge source X needs an image with git and python3) | Write `image` in the registration file, or name a project with `--project` (its image is used) |
| At run start a notice says `知识源 X 同步失败，用上一次的快照` (sync of knowledge source X failed, using the previous snapshot) | The git knowledge source could not be cloned this time; the run starts as usual. When run on the host, the cause is most likely that `openshell` cannot reach the gateway; run it in a container instead |
| `knowledge check` reports `X 是 dir，只有 mcp 知识源能检查连接` (X is dir; only mcp knowledge sources can check the connection) | Check `dir` and `git` with `myorch knowledge sync` |
| `knowledge check` says it needs to know which image to use (要知道用哪个镜像) | Add `--project <project>` or `--image <image>` |

### Sandbox creation and mounts

| Symptom | Cause and fix |
|---|---|
| `lstat ... no such file or directory` | A mounted subdirectory does not exist. Normally `prepare_step` creates it. When running by hand, create the directory first |
| `not admitted by required labels` | The volume lacks the admission label. `myorch init` labels only newly created volumes, not existing ones. Check the labels in `docker volume inspect myorch-artifacts` and `resource_admission` in `deploy/gateway.toml` |
| The workspace cannot be written, Permission denied | The volume is not owned by uid 1000. Run `myorch init` again (or `dc up -d`; the `init` service runs it) |
| A tool in the image reports Permission denied | The tool was installed in `/opt`, which Landlock does not allow reading. Install it under `/usr` instead and rebuild the image |
| Mounting to a path outside `/sandbox` fails | Landlock blocks it. Put all mount points under `/sandbox/` |
| `image not found` | The image was not built. Run `docker build -t openshell-claude-code:latest images/claude-code`. For project images see [4. Setup](04-setup.md#project-development-images) |
| `sandbox create <name> failed: ...` | The gateway is not up, or there is a problem with the image or a provider. Read the reason that follows, and `dc logs gateway` |
| Sandboxes left behind | Sandboxes are normally deleted at the end of each turn, on failure and on cancel. Delete leftovers with `dc exec worker openshell sandbox delete <name>` |

### GitHub and delivery

| Symptom | Cause and fix |
|---|---|
| The run succeeded, but there is no PR | This is by design: myorch only pushes approved commits and opens no PR (since 2026-11-06). The conversation and Slack say which branch was pushed; people open the PR themselves |
| The API returns 404 | The fine-grained token lacks the Contents permission (the new UI grants nothing by default), or the repository is not in the provider's rules |
| git push or clone returns 401 | git must pass the placeholder in a Basic auth header (see the comments in `openshell/providers/github-push.yaml`). The `*-sync-base.yaml` and `*-deliver.yaml` in `flows/lib/` already do this |
| The gateway rejects a request | The provider profile does not let this path through. Change the profile and update it with `resource_version` |
| The bot cannot access someone else's repository | A fine-grained token can only access repositories owned by its owner. When the bot is a collaborator, use a classic token |
| `deliver` exit code 3: HEAD changed after approval (HEAD 在审批之后变了) | The branch got more commits after approval. Start the run again and approve again |
| `deliver` exit code 3: the current branch is the base branch (当前分支是 base 分支) | myorch never pushes the base branch. Check the flow's branch settings |

### Approvals and waiting

| Symptom | Cause and fix |
|---|---|
| Approved, but the step still waits, and `status` says initiator X must be one of the approvers (发起人 X 必须是审批人之一) | The approval requires the initiator to approve too (`require_initiator`, on by default). Have the initiator approve once. On the CLI, give the right name with `--as` |
| `approve` / `reply` reports `'<step>' is not waiting for this (status: ...)` | This step is not waiting for an approval or an answer. Use `status` to see its current state and the step IDs |
| `changes` says change requests are not accepted (no approval.changes configured) (不接受修改意见（没有配置 approval.changes）) | This approval can only be approved or rejected |
| `resume` says the run has not ended yet (运行还没有结束) | A run that is still running cannot be resumed. When a step is waiting on you, reply or approve directly |
| `resume` says only failed or cancelled runs can be resumed (只有失败或取消的运行可以接着执行) or that there is no step to re-run (没有可以重新执行的步骤) | The run succeeded or was rejected, or the step given with `--step` did not fail. A rejected approval does not reopen |
| Waiting for an answer failed on a timeout (`等待用户回复超时`, timed out waiting for the user's reply) | `input_timeout` defaults to 24 hours. Each time you come back, check the **Waiting on you** list (等你处理) first |
| An approval failed on a timeout (`审批超时：... 内没有人处理`, approval timed out: nobody handled it within ...) | The approval has a `timeout` (7 days for development). When nobody handles it in time, the run fails and nothing is delivered. Start it again if you still want it |
| The Waiting on you list shows **No new output** (没有新输出), **Going in circles** (原地打转), **Stuck** (卡住了) or **Off task** (偏离了任务) | The watchdog judged that this step may not be making progress. myorch does not act on its own: read the log and decide to cancel, wait, or reply when it asks a question |

### Agents and checks

| Symptom | Cause and fix |
|---|---|
| The step succeeded, but its status is `manifest_missing` | The agent wrote no manifest or a broken one; the platform generated a fallback, and downstream steps run as usual. If it happens often, change the output requirements in the prompt |
| A step with `interaction: never` failed and its status contains a question | The agent insisted on asking. Give it more context, or change it to `allowed` |
| A loop ran out of rounds | Look at `docs/review/round-N/` and the reflect step's proposal. Reply in the console, or `myorch reply <run> <loop> 1/2/3` |
| Checks "passed" but actually checked nothing | A bug in `checks.sh`. When a tool fails to start, it must count as a failure (fixed in the core-ng template) |
| Chinese file names report `invalid byte sequence in US-ASCII` | The image has no UTF-8 locale. Set `LANG=C.UTF-8` |
| Java networking reports `BindException: Permission denied` | The policy does not let this address through. Or Java went over IPv6 and needs `-Djava.net.preferIPv4Stack=true` (handled in the core-ng image) |
| Java TLS handshake fails | Outbound TLS is re-signed by the supervisor's CA, and Java does not read `SSL_CERT_FILE`. The `java` launcher script in the core-ng image generates a truststore |
| onboard's `try-checks` fails on a new repository | With fewer than 5 commits, `git rev-list --skip=5` is empty. It now falls back to the empty tree (all history) |
| learn's `project-apply` reports that the proposals could not be applied cleanly and the knowledge base was not changed (建议无法干净地应用，知识库未改动) | The adopted proposals conflict with the current content of `myorch-knowledge`. The knowledge base was not changed. Run learn again |
| learn's `project-apply` reports `... is not a git repository; run git init there first` | The `myorch-knowledge` directory is not a git repository. learn makes one commit per proposal and needs git |

## A step failed for an external reason

When a step failed for an external reason (timeout, token permissions, network) and you do not want to redo the whole run:

1. Remove the cause (e.g. replace the token, change the provider profile).
2. Run `uv run myorch resume <run>`, or click **Re-run failed steps** (重新执行失败的步骤) in the console. Steps that already succeeded keep their results; see [5. CLI reference · resume](05-cli.md#resume).

A flow can give a step `on_failed: ask`: on failure the run stops and asks you. In the console, choose **Start over** (重新开始), **Skip** (跳过) or **Stop** (终止), or run `myorch reply <run> <step> 1/2/3`.

## Console 500: disk I/O error

**Symptom**: the console returns 500, and `dc logs ui` shows `sqlite3.OperationalError: disk I/O error`.

**Cause**: the event log `myorch-data/events.db` is SQLite in WAL mode, and the worker and the console open it in containers. If a process on the host also opens this file directly, its locks and shared memory are not shared with the containers. When it closes, it thinks it is the last connection, deletes `events.db-shm` and empties `events.db-wal`. After that, every read and write in the containers reports `disk I/O error`, and events are no longer recorded (this happened on 2026-11-09).

**Fix**: wait until no step is running, then stop the console and the worker **together** and start them together (`docker stop myorch-ui-1 myorch-worker-1 && docker start myorch-worker-1 myorch-ui-1`). Restarting only one of them does not work: each side would use its own shared memory, which can corrupt the database.

**Prevention**:

- `myorch` commands on the host read events through the console's `/channel/events.json` (`HttpEventLog` in `storage/events.py`) and do not open `events.db` directly.
- The exceptions are `worker`, `ui` and `serve`: they open the file directly. While compose runs, do not start these three commands on the host.
- The same holds for your own scripts: use the interface, and do not open the file with `sqlite3` on the host. For a read-only look, copy it and open the copy.
- Do not set `MYORCH_EVENTS_DIRECT=1` on the host; it makes commands open the file directly.
