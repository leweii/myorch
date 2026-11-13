# Running myorch with docker compose

For how the services call each other and the order they start in, see the diagram in [2. Services and dependencies](../docs/02-services.md#services-in-docker-compose).

Status: **the everyday environment since 2026-11-05 (macOS, Docker Desktop)**. The smoke test passes. All 8 credentials were tested from a sandbox (including git reads from github.com). Linux hosts are not tested yet. For the investigation, see [docs/notes/2026-11-04-gateway-in-docker.md](../docs/notes/2026-11-04-gateway-in-docker.md).

compose has these services:

| Service | What it does |
|---|---|
| `temporal` | `temporal server start-dev`. Its database is `temporal.db` in the data directory |
| `gateway-init` | Runs once on every `up`: if there is no key yet, it generates the Ed25519 key the gateway uses to sign sandbox credentials |
| `gateway` | The official OpenShell gateway image, docker driver |
| `providers` | Runs once on every `up`: imports provider profiles and credentials from the credentials file (`deploy/provision.py`) |
| `init` | Runs once on every `up`: runs `myorch init`, which creates the volume, adds the approval labels and installs `tools/` |
| `worker` | `myorch worker`. Starts only after `init` and `providers` succeed |
| `ui` | `myorch ui`: the console, plus the Slack adapter and the watchdog |

worker and ui mount the host's `myorch-data/` (`/data`), `myorch-knowledge/` (`/knowledge`), `channels.yaml` (read-only), and `MYORCH_KB_ROOT`, the root of local directory knowledge sources (read-only, default `~/orca/kb`, same path inside and outside the container).

Sandboxes are still containers on the host's Docker, next to these services. The gateway and the worker control the host's Docker through the mounted `/var/run/docker.sock`; this is not Docker-in-Docker. Mounting docker.sock gives these containers root on the host. Sandbox isolation is not affected, but this set of services is not an isolation boundary itself.

## Installation

Run every command from the `myorch/` directory.

### ① Directories

Put `myorch/`, `myorch-knowledge/` and `myorch-data/` in the same parent directory, as in [4. Setup](../docs/04-setup.md). If they are somewhere else, copy `deploy/.env.example` to `deploy/.env` and change the paths in it. In `deploy/.env` you can also set the ports, the volume name, the time zone, `MYORCH_KB_ROOT`, and on whose behalf the console and the CLI act (`MYORCH_USER`, which must be a person in `channels.yaml`; if unset, the host's `$USER` is used).

### ② Credentials file

Credentials go in `deploy/secrets/providers.yaml`. On every `docker compose up`, the `providers` service reads this file and writes it into the gateway:

```sh
mkdir -p deploy/secrets
cp deploy/providers.example.yaml deploy/secrets/providers.yaml
chmod 600 deploy/secrets/providers.yaml
```

Fill in the tokens with an editor. For the format, see the comments in `deploy/providers.example.yaml`:

```yaml
github_profiles: [blog]              # projects to generate a GitHub profile for
providers:
  claude:                            # the name must be claude
    type: claude-code-oauth
    credentials:
      CLAUDE_CODE_OAUTH_TOKEN: "..."   # the token from claude setup-token
  github-blog:
    type: github-blog
    credentials:
      GITHUB_TOKEN: "..."
```

- The keys of `credentials` are the environment variable names declared in the provider profile (`env_vars`).
- `deploy/secrets/` is in `.gitignore` and `.dockerignore`. It is never committed and never built into an image. Only the `providers` service mounts it; the worker, the console and sandboxes cannot see it.
- Values reach `openshell` only through a child process's environment variables. They never appear in command-line arguments or logs.
- These provider profiles are always imported, whether or not the file lists them: `openshell/providers/*.yaml` and `myorch-knowledge/<project>/providers/*.yaml`. Profiles already in the gateway are updated from the files.
- Providers that are in the gateway but not in the file are left unchanged. The log lists them.
- If the file does not exist, the `providers` service does nothing.
- After changing a token, run `docker compose -f deploy/compose.yaml run --rm providers` to apply it. Other services do not need a restart.
- A provider whose values are all empty is skipped. The log says `skipped, no value yet`. You can fill it in later.
- When a provider's `type` changes (for example `claude` from `claude-code-oauth` to `claude-code-vertex`), the old one is deleted first and then created with the new type.
- For a provider with `refresh:`, the gateway obtains short-lived tokens itself. `service_account_json:` points to a GCP service account JSON key in the secrets directory. Its `client_email` and `private_key` are handed to the gateway as refresh material (the private key stays only in the gateway), and a token is obtained once right away. If the key is wrong or lacks permissions, `providers` fails at this point.
- With Vertex AI (`claude` has the type `claude-code-vertex`), also set `MYORCH_VERTEX_PROJECT` in `deploy/.env` (and `MYORCH_VERTEX_REGION`, default `global`). See `deploy/providers.example.yaml` for how.
- If the file is wrong (only some values filled in, a type that does not exist), `providers` exits with a failure and the worker does not start. Use `docker compose -f deploy/compose.yaml logs providers` to see why.

The gateway's state (credentials, provider profiles) is in the volume `<project name>_gateway-state` (default `myorch_gateway-state`).

### ③ Build the images

```sh
docker build -t openshell-claude-code:latest images/claude-code    # default sandbox image
docker compose -f deploy/compose.yaml build                          # myorch image
```

A project's development image is built from `image/` in its project pack; see [4. Setup · Project development images](../docs/04-setup.md#project-development-images).

### ④ Start

```sh
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps -a     # gateway-init, providers, init are exited (0); the others are running
```

### ⑤ Smoke test

It needs no credentials and calls no model:

```sh
docker compose -f deploy/compose.yaml exec worker myorch run deploy/smoke.yaml --wait
```

Both steps are `succeeded`, and the second step's summary is `upstream said: hello from ...`.

### ⑥ Project packs and knowledge sources

The commands below use a shell function (works in bash and zsh):

```sh
mo() { docker compose -f deploy/compose.yaml exec worker "$@"; }
mo myorch project sync <name>
```

Knowledge sources do not need a manual sync: they are synced when a run starts. To sync ahead of time, use `mo myorch knowledge sync <source> --project <name>`.

Then open the console: http://127.0.0.1:8800/

## New machine

For a machine that runs for a long time, for example a Mac mini.

A new machine starts from scratch. It does not bring the old machine's history (workspaces of earlier runs, eval cases, Temporal records). learn and eval build up again from the start on the new machine.

### First, on the old machine

1. myorch and myorch-knowledge are both pushed to GitHub (myorch-knowledge is the private repository `leweii/myorch-knowledge`). Run `git status` once in each directory and check that there are no uncommitted changes.
2. Have the credentials file `deploy/secrets/providers.yaml` ready (and `slack.env` if the new machine will run Slack). Copy them over with AirDrop or `scp`, not through chat or email.

### On the new machine

1. Install Docker Desktop, start it, and turn on "Start when you sign in" in its settings. If there is no git, run `xcode-select --install`. Install Homebrew, then `brew install gh`, then `gh auth login` (account `leweii`).
2. Clone myorch and put the credentials files into `deploy/secrets/`:
   ```sh
   mkdir -p ~/orca/projects && gh repo clone leweii/myorch ~/orca/projects/myorch
   mkdir -p ~/orca/projects/myorch/deploy/secrets   # put providers.yaml (and slack.env) here
   ```
3. Run the install script:
   ```sh
   bash ~/orca/projects/myorch/deploy/bootstrap-mac.sh
   ```
   It clones myorch-knowledge, creates `myorch-data`, builds all images (the first time downloads several GB), starts the services, syncs the project packs and runs the smoke test. The three directories go under `~/orca/projects/` by default; change that with `MYORCH_ROOT`. Where a step needs you to act, it stops and says so. You can run it again and again: steps that are done are skipped. `FORCE=1` forces the images to be rebuilt.
4. If the login name on the new machine is not `jakobhe`, follow the script's hint and run it again with `MYORCH_USER=jakobhe`. The value is written to `deploy/.env`, and the console and the CLI act on behalf of this person.

### Running for a long time

- **No sleep**: System Settings → Energy, turn on "Prevent automatic sleeping".
- The services are set to `restart: unless-stopped`, so they come up when Docker Desktop starts.
- **Slack may run on one machine only**: when two machines connect to Slack with the same token, Slack hands events to either side at random. Before the new machine starts Slack, stop it on the old machine: delete the old machine's `deploy/secrets/slack.env`, then run `docker compose -f deploy/compose.yaml up -d --force-recreate ui`.
- **The new machine's myorch-knowledge is the source of truth**: suggestions adopted by learn, onboard and postmortem are committed to the copy on the new machine. Push to GitHub from the new machine; everywhere else only pulls.

### Using it from another computer

myorch listens only on `127.0.0.1` and accepts only requests whose Host is `127.0.0.1:8800` or `localhost:8800` (against DNS rebinding). Do not open it up. On the new machine, turn on "Remote Login" (System Settings → General → Sharing). Then open an SSH port forward on the computer you use:

```sh
ssh -N -L 8800:127.0.0.1:8800 <user>@<new machine>.local
```

After that, http://127.0.0.1:8800/ on this computer is the new machine's console. The Claude Code channel works the same way: it runs on the computer you use (it needs the myorch repository and uv), and the `127.0.0.1:8800` it connects to reaches the new machine through the forward.

## Slack

The Slack adapter (`src/myorch/channels/slack.py`) runs in the console process: it sends direct messages to initiators, and handles answers to questions, approvals, and `/myorch` to start a run. For the design, see [docs/notes/2026-11-05-slack-adapter.md](../docs/notes/2026-11-05-slack-adapter.md).

1. Create the app: open https://api.slack.com/apps → Create New App → From a manifest → pick the workspace → paste `deploy/slack-app-manifest.yaml`.
2. Install it: OAuth & Permissions → Install to Workspace. Copy the **Bot User OAuth Token** (starts with `xoxb-`).
3. Create an app-level token: Basic Information → App-Level Tokens → Generate, with the scope `connections:write`. Copy it (starts with `xapp-`).
4. Write the tokens:
   ```sh
   cp deploy/slack.env.example deploy/secrets/slack.env && chmod 600 deploy/secrets/slack.env
   ```
   Fill in the two tokens with an editor. `deploy/secrets/` is not in git and not in images. Only the `ui` service reads it.
5. Register yourself: in Slack, open your profile → ⋮ → Copy member ID, and write it into `channels.yaml`: `jakobhe: {github: leweii, slack: U...}`.
6. Apply: `docker compose -f deploy/compose.yaml up -d --force-recreate ui`. `docker compose -f deploy/compose.yaml logs ui` shows `slack: connected (Socket Mode)`.
7. Try it: send myorch a direct message in Slack, or type `/myorch`.

When someone who is not registered messages the bot, the bot replies with their member ID and does nothing else.

## Claude Code

A local Claude Code session can receive myorch's results (succeeded, failed, questions, approvals), and read logs and start flows with tools. For the design, see [docs/notes/2026-11-05-claude-code-channel.md](../docs/notes/2026-11-05-claude-code-channel.md).

1. Start the session in the `myorch/` directory (`.mcp.json` registers the server `myorch`):
   ```sh
   claude --dangerously-load-development-channels server:myorch
   ```
   The first time there are two confirmations: the warning about development channels (choose I am using this for local development), and the new MCP server (choose Use this MCP server).
2. When `Channels (experimental) messages from server:myorch ...` appears below the startup banner, the channel is active. `/mcp` shows `myorch` and its tools.
3. When a run finishes, asks a question or needs an approval, the session receives a `<channel source="myorch" ...>` message.

- Set the subscription scope with `MYORCH_SCOPE` in `.mcp.json`: `mine` (default), `session`, `all`. `MYORCH_PROJECTS=blog,mdm` limits it to some projects.
- To use it from other directories too, add it to the user-level configuration: `claude mcp add --scope user myorch -- uv run --quiet --project <absolute path of myorch> python -m myorch.channels.claude_code`. The start command stays the same.
- Agents cannot approve. Approve in the console or in Slack.
- **Channels only work with Anthropic authentication** (a claude.ai account or a Console API key), not with Vertex AI, Bedrock or Foundry: with those providers the client silently drops the notifications. Use polling instead: read `GET http://127.0.0.1:8800/channel/events.json?after=<seq>` on a timer and keep the cursor yourself. Do not use the channel and polling at the same time, or each event is handled twice.

## Common commands

```sh
docker compose -f deploy/compose.yaml logs -f worker            # worker log
docker compose -f deploy/compose.yaml up -d --build worker ui   # after changing src/
docker compose -f deploy/compose.yaml exec worker myorch list   # run any myorch command like this
docker compose -f deploy/compose.yaml down                      # stop. Data stays in the data directory and the volumes
```

- For runs started from inside a container, the initiator is `MYORCH_USER` from `deploy/.env` (if unset, the host's `$USER`), not root. To start a run on someone else's behalf, add `--as <name>`.

## Release origin/main

```sh
deploy/update.sh --check   # which commits would be released, and what it would wait for
deploy/update.sh           # fast-forward main → wait until no step is running → rebuild and restart worker and console → smoke test → tag deploy-<time>
```

- Runs only on the `main` branch, fast-forward only.
- A restart interrupts the steps that are running, so it first waits until no step is running (at most `WAIT_MAX` seconds, default 6 hours). Runs waiting for an answer or an approval may stay. It reads the state of the runs through the console's API (`MYORCH_URL`, default `http://127.0.0.1:8800`).
- When `src/myorch/engines/` or `src/myorch/core/run.py` changed, it waits until no run is unfinished (Temporal replays unfinished runs against the new code).
- The images are built from a clean checkout of the target commit (a temporary git worktree), not from the work tree: uncommitted or staged changes someone has in the work tree do not end up in the image. When `images/claude-code/` changed, the sandbox image is rebuilt too.
- If the smoke test fails, the previously released commit is rebuilt automatically. The failed commit is recorded in `deploy/.failed` and is not released again until origin/main has a newer commit.
- The development-eval orchestrator agent also releases with it after it merges a whitelisted change.
- Temporal UI: http://127.0.0.1:8233/

## Ports

All are published on `127.0.0.1` only.

| Port | Service | Can it change |
|---|---|---|
| 8800 | Console | `MYORCH_UI_PORT`. The same port is used inside and outside the container, because the console accepts only requests whose Host is `127.0.0.1:<its own port>` or `localhost:<its own port>` (against DNS rebinding) |
| 7233 / 8233 | Temporal / Temporal UI | `TEMPORAL_PORT` / `TEMPORAL_UI_PORT` |
| 17671 | gateway | Fixed. Sandbox supervisors connect back to this port through the host, and `grpc_endpoint` in `gateway.toml` hard-codes it. To change it, change both places |

The gateway has no TLS and no user authentication. Anyone who can reach 17671 can control it, so it may only be published on `127.0.0.1`.

## Known limitations

- **Seeding from local clones has no effect.** `myorch-data/local-repos.yaml` holds host paths (for example `~/Github/...`), which do not exist in the container. So the seed is skipped and `sync-master` clones fully from GitHub. A failed seed does not affect the run. To use seeding, mount the clone into the worker at the same absolute path as on the host, because `seed_repo` hands that path to the host's Docker to resolve (`seed_repo` in `src/myorch/storage/docker_volume.py`).
- **Knowledge sources of kind `dir`** must have their folder under `MYORCH_KB_ROOT` (default `~/orca/kb`). compose mounts only this directory into the console and the worker; folders elsewhere are reported as a problem in the console.
- **Linux hosts are not tested.** The supervisor connects back to the gateway through `host.docker.internal`. On Linux this points to the docker0 bridge, and ports published on `127.0.0.1` cannot be reached from the bridge. See "How the supervisor connects back to the gateway" in the [investigation note](../docs/notes/2026-11-04-gateway-in-docker.md#3-how-the-supervisor-connects-back-to-the-gateway).
- `myorch review` calls macOS `open`, which does not exist in the container. Use `--no-open`.
- **Processes on the host must not open `myorch-data/events.db` directly**: it makes queries in the container fail with `disk I/O error`. The CLI on the host reads events through the console; see [2. Services and dependencies](../docs/02-services.md#who-reads-and-writes-files-and-configuration).
- The sandbox image and project development images are not in compose and must be built separately.
