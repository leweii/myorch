# 2. Services and dependencies

For installation steps, see [4. Setup](04-setup.md).

## Service list

Since 2026-11-05, the everyday environment is docker compose (`deploy/compose.yaml`). The services start automatically once Docker Desktop is running. `dc` is short for `docker compose -f deploy/compose.yaml`.

| Service | How to start it | Address | Data | Required |
|---|---|---|---|---|
| **Docker Desktop** | Open the Docker Desktop app | The local Docker socket | volumes `myorch-artifacts`, `myorch_gateway-state`, images | Yes |
| **temporal** | compose service (`temporalio/temporal:1.9.1`, `server start-dev`) | gRPC `127.0.0.1:7233`, UI http://127.0.0.1:8233 | `myorch-data/temporal.db`. Finished runs are kept for 24 hours | Yes |
| **gateway** (OpenShell) | compose service, official image `ghcr.io/nvidia/openshell/gateway:0.1.6` | `127.0.0.1:17671`. Inside containers: `http://gateway:17671` | providers (credentials), provider profiles, sandbox records, in the volume `myorch_gateway-state` | Yes |
| **worker** | compose service (`myorch worker`) | Listens on no port. Takes tasks from the Temporal task queue `myorch` | Writes `myorch-data/events.db` | Yes |
| **ui** (console, `myorch serve`) | compose service | http://127.0.0.1:8800/, the channel interface at `/channel/` | Writes `myorch-data/ui/`, `slack/`, `watchdog.json`. The Watchdog and the Slack adapter also run in this process | Yes. Only one can run at a time |
| gateway-init, init, providers | Run once in compose, then exit | — | gateway-init: generates the signing key if there is none. init: creates the volume, installs `tools/`. providers: writes `deploy/secrets/providers.yaml` into the gateway | Run automatically on `up` |
| Slack adapter | In the ui process; starts when `deploy/secrets/slack.env` exists | Connects out to Slack (Socket Mode), listens on no port | `myorch-data/slack/state.json` | No |
| Claude Code channel | Started by a local Claude Code session (`.mcp.json`) | Connects to `127.0.0.1:8800` | `~/.cache/myorch/claude-code-<person>.json` (the position read so far) | No |

For running without compose, see the last paragraph of [4.3](04-setup.md#43-starting-the-day).

## Service dependency diagrams

The relations between services are shown in three diagrams:

1. The overall diagram: which services exist and who calls whom. OpenShell is drawn as a single box in this diagram.
2. The services in docker compose: each container, its mounts and its ports.
3. Inside OpenShell: only OpenShell's own components. It shows how the gateway and the supervisor work together, and how a request from inside the sandbox passes through the policy to reach third-party platforms such as GitHub, Jira and the Claude API. Slack is not among them: the Slack adapter in `myorch serve` connects to it directly, and its credentials do not go through OpenShell.

The diagrams in the docs are drawn as white cards on a grey background. Only status and emphasis use color: blue marks outbound traffic and key components, red marks untrusted parts. The diagrams follow the theme in the top right of the page (light, dark, follow the system).

| Drawing | Meaning |
|---|---|
| Thin grey arrow | A call. The arrow points from the caller to the callee; the text next to the line says how it calls |
| Thick blue arrow | An outbound request allowed by the policy |
| Light grey dashed arrow | Reading or writing data |
| Card with a blue border | The key component of this diagram |
| Card with diagonal hatching | A component related to isolation or outbound traffic |
| Card with a dark border | A person, or a place that needs you |
| Card with a black background | An untrusted agent process |
| Card in monospace text | Data |
| Red dashed box, light red background | The untrusted side (the workload container) |

The diagrams are HTML files under `docs/diagrams/`. The web version `docs/index.html` embeds them in this section; you can also open them directly in a browser.

### Overall diagram

<!-- diagram: services-overview.html -->

Diagram file: [diagrams/en/services-overview.html](../diagrams/en/services-overview.html).

The console, the CLI and channels in the "Entry points" area call the same set of Python functions (`engines/temporal/client.py`, `storage/docker_volume.py`), so they share one set of lines in the diagram.

- The console and the CLI only call Temporal; they never call the worker directly. The worker takes tasks from the Temporal task queue `myorch` on its own.
- Only the worker creates step sandboxes. Syncing git repository knowledge sources and `myorch knowledge check` also start one-off sandboxes, started by the process that triggered them.
- When the console, the CLI and the worker read or write the volume, they use one-off helper containers with `--network none`.
- When the CLI on the host reads the event log, it goes through the console's `/channel/events.json` and does not open `events.db` directly (see [Who reads and writes files and configuration](#who-reads-and-writes-files-and-configuration)).

The channel layer now has three platform adapters: the console, Slack, the Claude Code channel. Interaction on GitHub (summoning in comments, replying, approving) is paused. New platforms get an adapter written on the relay; see [notes/2026-11-02-channel-layer.md](../notes/2026-11-02-channel-layer.md) and [3. Architecture · Other platform adapters](03-architecture.md#other-platform-adapters).

### Services in docker compose

<!-- diagram: compose-services.html -->

Diagram file: [diagrams/en/compose-services.html](../diagrams/en/compose-services.html).

- You deal with only two places: the console in a browser, and `myorch` commands in a terminal (`uv run myorch` on the host, or `docker compose exec worker myorch`).
- The services in compose are on the same network and reach each other by service name: the console and the worker connect to `temporal:7233`; the worker, the console and `providers` connect to `http://gateway:17671`.
- Only `providers` mounts the credentials file. It uses the `openshell` CLI to write provider profiles and credentials into the gateway. Only `ui` reads `deploy/secrets/slack.env`. Neither the worker nor the sandboxes can get these two files.
- The worker and ui mount the same host directories: `myorch-data/` (`/data`), `myorch-knowledge/` (`/knowledge`), `channels.yaml` (read-only), and the root directory of local directory knowledge sources (read-only, `~/orca/kb` by default, with the same path inside and outside the container).
- The gateway and the worker both operate the host's Docker through `/var/run/docker.sock`. The gateway creates the two containers of a sandbox. The worker, the console and `init` start helper containers to read and write the volume `myorch-artifacts`. Mounting docker.sock gives these containers root on the host, so this set of services is not itself an isolation boundary.
- The supervisor is not on the compose network (`supervisor_network_mode` can only be `auto`, `host` or `bridge`). It connects back to the gateway through `host.docker.internal:17671`, so the gateway's port 17671 must be published on the host. This is the only connection that loops from the host back into compose.
- `gateway-init` writes the signing key into the volume `gateway-state`. The gateway reads the key from there and also keeps its own database there.

### Inside OpenShell

<!-- diagram: openshell-internals.html -->

Diagram file: [diagrams/en/openshell-internals.html](../diagrams/en/openshell-internals.html). Based on the [OpenShell architecture docs](https://docs.nvidia.com/openshell/about/architecture).

- The gateway is the control plane: the long-running `gateway` service in compose. It stores the providers' real credentials, the policies and the sandbox records, and issues JWTs. It creates sandboxes through the docker driver.
- Each step instance's sandbox is two containers on Docker, visible in `docker ps`. The name is made of the flow, the step and a short hash, for example `dev-deve-3f2a9` (develop in development). The supervisor container's name ends in `-supervisor` and uses the `bridge` network. The workload container's network is `none`.
- The supervisor is on the trusted side of the isolation boundary. It connects back to the gateway through `host.docker.internal`, authenticates with the gateway JWT, fetches the policy and credentials, and pushes logs.
- The agent process is in the workload container on the untrusted side. Landlock restricts the paths the agent can read and write. seccomp intercepts the agent's TCP connections and DNS queries and hands them to the supervisor over an authenticated Unix socket. This socket is the agent's only outbound path.
- The supervisor judges each request. When the policy allows it, the supervisor replaces the placeholder with the real credential and then makes the real connection. The compute driver only builds the boundary; it does not judge requests.
- myorch passes only provider names (for example `claude`, `github-blog`). The worker, the console, the CLI and the agent never get the real credentials.

## Startup order

<!-- diagram: compose-startup.html -->

Diagram file: [diagrams/en/compose-startup.html](../diagrams/en/compose-startup.html).

compose orders services by `depends_on`. When `providers` fails, the worker does not start. The console does not wait for the gateway or `providers`.

## Which services each command depends on

For how to use the commands themselves, see [5. CLI reference](05-cli.md). The "Console" column refers to commands executed on the host: they read the event log through the console. When executed inside a container, they read the file directly and do not need the console.

| Command | Temporal | worker | Docker | OpenShell | Console |
|---|---|---|---|---|---|
| `expand`, `project show`, `project github-profile` | | | | | |
| `init`, `project list`, `project sync`, `knowledge list` | | | ✓ | | |
| `knowledge sync` | | | ✓ | git repository knowledge sources | |
| `knowledge check` | | | | ✓ (executed in the container) | |
| `logs`, `out`, `ws`, `export` | | | ✓ | | |
| `run` | ✓ | Needed to advance the run | When there are knowledge sources | When syncing git repository knowledge sources | When continuing from a run, or for learn |
| `reply`, `approve`, `reject`, `changes`, `cancel`, `resume` | ✓ | Needed to advance the run | | | |
| `status` | ✓ | | | | |
| `list`, `review` | ✓ | | ✓ | | |
| `schedule` | ✓ | Needed to start runs when the time comes | | | |
| `eval` | ✓ | Needed by `eval run` | ✓ | | `eval report` |
| `worker` | ✓ | — | ✓ | ✓ | |
| `ui`, `serve` | ✓ | Needed to advance the run | ✓ | When syncing git repository knowledge sources | — |

"Needed to advance the run" means the command itself succeeds (for example, `run` can print the run_id), but without the worker the run does not move forward.

## What happens when a service stops

| Stopped service | What you see | After it comes back |
|---|---|---|
| Temporal | The CLI and the console cannot connect to `127.0.0.1:7233`. The console does not start | The state is in `temporal.db` and is not lost. Runs continue |
| worker | New runs stay `pending`. Executing steps count one failure after the heartbeat timeout | Runs continue once the worker is up. Whether the failed attempt is re-run depends on the step's `retries` |
| OpenShell gateway | Creating sandboxes fails, so steps fail | Steps are re-run according to their `retries`. Without retries the run fails; use `myorch resume <run>` to continue from the failed step |
| providers (credentials import failed) | The worker does not start (compose dependency) | Fix `deploy/secrets/providers.yaml`, run `dc run --rm providers`, then `dc up -d` |
| Docker | All compose services stop | Once Docker Desktop is up, the services start automatically (`restart: unless-stopped`). Steps that were executing at the time are handled as when the worker stops |
| Console | Runs are not affected. Things waiting on you keep waiting until they time out (questions: 24 hours by default). Slack gets no notifications. Commands on the host that need to read the event log fail (see the table above) | Just open it again. The Slack adapter continues from its cursor and does not resend |
| Claude Code session | Gets no pushes | The next time the channel starts, what happened in the meantime is combined into one digest push |

Nothing needs to be done before shutting down. The state of runs waiting on you is stored in Temporal, and they continue the next time the services are up.

## External dependencies

| Dependency | Who accesses it | Through what |
|---|---|---|
| `api.anthropic.com` | The claude CLI in the sandbox | OpenShell injects the token of the provider `claude` |
| `github.com`, `api.github.com` | Steps with the `dev-git` profile (clone, fetch, push); the review flow reads PRs | OpenShell injects the project's GitHub provider. The rules allow only this project's repository; submodules are read-only |
| Jira (`example.atlassian.net`) | Steps with the `ticket-read` profile (read tickets and attachments) | OpenShell injects the provider `jira-mdm`. The rules allow only reads |
| Atlassian MCP (`mcp.atlassian.com`) | Agent steps that mount the Atlassian MCP knowledge source | OpenShell injects the provider `atlassian-mcp`. `myorch-mcp` allows only registered tools |
| The project's dependency sources (Maven, npm, private sources) | Steps with `dev-write` or `dev-git` | Allowed by the project pack's `policy.yaml`. Private sources use the `artifacts` provider |
| Docker image registries | Docker: helper images `alpine/git`, `nvcr.io/nvidia/base/ubuntu:24.04`. compose's `temporalio/temporal`, `ghcr.io/nvidia/openshell/*`, `alpine`. For building the myorch image: `python:3.12-slim`, `docker:cli`, `ghcr.io/astral-sh/uv` | The local network |
| Releases on `github.com` | Downloading the openshell CLI when building the myorch image | The local network |
| Slack (`slack.com`, Socket Mode) | The Slack adapter in the ui process | Bot token and app-level token, in `deploy/secrets/slack.env` |

## Who reads and writes files and configuration

| File | Read by | Written by |
|---|---|---|
| `flows/`, `profiles/` | The CLI, the console (expand before submitting), the worker (profiles, schedules starting runs) | You |
| `channels.yaml` | The CLI, the console, the Slack adapter, the worker (schedules): `identities`, who is who, aliases of the person who starts a run, who may act in Slack | You |
| `deploy/secrets/providers.yaml` | Only compose's `providers` service | You |
| `deploy/secrets/slack.env` | Only compose's `ui` service | You |
| `myorch-knowledge/` | The CLI, the console (fill in project values before submitting), the worker (schedules, host actions), `project sync`, the `providers` service (the project's provider profiles) | You; the worker's `project-apply`; the console (registering knowledge sources, committed automatically) |
| volume `myorch-artifacts` | All components | The worker (sandboxes, helper containers), the console and the CLI (knowledge source snapshots, `project sync`), `init` |
| `myorch-data/events.db` | The channel layer in the console process (the console, Slack, the Claude Code channel and the Watchdog all go through it), the CLI inside containers | worker |
| `myorch-data/temporal.db` | Temporal | Temporal |
| `myorch-data/slack/state.json` | Slack adapter | Slack adapter |

The worker writes `events.db` into the data directory (`--data` or `MYORCH_DATA`; `/data` in compose).

`events.db` is opened only by processes inside containers. Processes on the host (for example `uv run myorch run`) read it through the console's `GET /channel/events.json`; when the console is not running they report an error and do not fall back to reading the file directly. The reason: processes on the host do not share SQLite's locks with the containers, and when such a process closes its connection it deletes `events.db-shm`, after which every query in the containers fails with `disk I/O error` (seen on 2026-11-09; for the fix see [11. Troubleshooting](11-troubleshooting.md)). `MYORCH_EVENTS_DIRECT=1` forces reading the file directly; use it only when no container has the file open.
