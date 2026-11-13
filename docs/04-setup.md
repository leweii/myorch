# 4. Setup

Since 2026-11-05 the everyday environment is docker compose: Temporal, the OpenShell gateway, the worker and the console all run in containers. Run every command from the `myorch/` directory. For how the services relate, see [2. Services and dependencies](02-services.md). For the detailed compose steps, credentials files, Slack, the Claude Code channel, ports and known limits, see [deploy/README.md](../deploy/README.md).

## 4.1 Required software

On the host:

| Software | Local version (tested) | Purpose |
|---|---|---|
| macOS + Docker Desktop | Docker 29.9 | Runs the compose services, the sandbox containers, the volume `myorch-artifacts` and the helper containers. Only run on Apple Silicon so far |
| git, gh | | Clone myorch and the private repository myorch-knowledge. `deploy/bootstrap-mac.sh` requires gh to be logged in |
| uv | 0.13 | The CLI on the host (`uv run myorch ...`), tests, the Claude Code channel. The project requires Python ≥ 3.12 |
| claude CLI | | Creates the Claude credentials (`claude setup-token`) and receives the Claude Code sessions that myorch pushes |

Temporal and OpenShell do not need to be installed on the host:

| Component | Version | Where |
|---|---|---|
| Temporal | image `temporalio/temporal:1.9.1` (`server start-dev`) | compose service `temporal` |
| OpenShell gateway | image `ghcr.io/nvidia/openshell/gateway:0.1.6` | compose service `gateway` |
| openshell CLI | 0.1.6 | Built into the myorch image (`deploy/Dockerfile`). Used by the worker, the console and `providers` |

Python dependencies (`pyproject.toml`): `temporalio`, `pydantic`, `pyyaml`, `starlette`, `uvicorn`, `markdown`, `aiohttp`, `slack-sdk`. Development dependencies: `pytest`, `pytest-asyncio`, `import-linter`. `uv sync` installs all of them.

Docker images:

| Image | Source | Purpose |
|---|---|---|
| `myorch:local` | `deploy/Dockerfile`, built by `docker compose build` | worker, console, `init`, `providers` |
| `openshell-claude-code:latest` | `images/claude-code/` in this repository, built by you | The default sandbox image, and the base of the projects' development images |
| `myorch-dev-<project>:latest` | The project pack's `image/`, built by you | The project's development image. blog also has `myorch-e2e-blog:latest` (browser tests) |
| `alpine/git:latest`, `nvcr.io/nvidia/base/ubuntu:24.04` | Pulled automatically | Helper containers that read and write the volume |

## 4.2 One-time installation

**On a new Mac, use the install script `deploy/bootstrap-mac.sh`.** The steps are in [deploy/README.md, "New machine"](../deploy/README.md#new-machine). The script can be run again and again; it skips the steps that are already done. It does the following:

1. Checks git, Docker Desktop and gh (logged in).
2. Puts three directories under the same parent directory (default `~/orca/projects/`, change it with `MYORCH_ROOT`): clones myorch and myorch-knowledge, and creates `myorch-data`. For what each one is for, see [1. Overview · Directories and storage](01-overview.md#directories-and-storage).
3. Checks the credentials file `deploy/secrets/providers.yaml` (template `deploy/providers.example.yaml`). If you use Slack, also `deploy/secrets/slack.env`.
4. Builds the images: `openshell-claude-code`, then the development images of all projects.
5. Writes into `deploy/.env` the user that the console and the CLI act as (`MYORCH_USER`, must be a person in `channels.yaml`. `channels.yaml` names real accounts and is not in git; when it is missing, it is created from `channels.example.yaml` with just you in it), then runs `docker compose -f deploy/compose.yaml up -d --build`.
6. Syncs all project packs (`myorch project sync`).
7. Smoke test: `docker compose -f deploy/compose.yaml exec worker myorch run deploy/smoke.yaml --wait`. Two script steps; no model is called.

Manual installation has the same steps as the script. The commands for each step are in [deploy/README.md, "Installation"](../deploy/README.md#installation).

After installation, install the dependencies for the CLI on the host too:

```sh
uv sync
uv run myorch list      # connects to the Temporal in compose and lists the recent runs
```

## 4.3 Starting the day

The services are set to `restart: unless-stopped`. They come up when Docker Desktop starts; no terminal is needed.

```sh
docker compose -f deploy/compose.yaml ps            # all running: temporal, gateway, worker, ui
uv run myorch list                                   # recent runs, and which ones are waiting on you
```

- Console: http://127.0.0.1:8800/ . Temporal UI: http://127.0.0.1:8233/ .
- Temporal keeps finished runs for only 24 hours (the dev server default, checked on 2026-11-10 with `temporal operator namespace describe default`). For older runs, the console reads the state and the conversation from the event log.
- After changing `src/` or `flows/`: `docker compose -f deploy/compose.yaml up -d --build`. This interrupts the steps that are running. To release the commits on origin/main, use `deploy/update.sh`; it waits for the running steps to finish before it restarts (see [deploy/README.md](../deploy/README.md#release-originmain)). For what to do after other changes, see [1. Overview · What to do after a change](01-overview.md#what-to-do-after-changing-something).

Check that the services are up:

```sh
docker compose -f deploy/compose.yaml ps -a                          # gateway-init, init, providers are exited (0)
docker compose -f deploy/compose.yaml exec worker openshell provider list   # claude and every project's credentials are there
docker compose -f deploy/compose.yaml logs providers                 # look here when importing credentials fails
```

Nothing needs to be done before shutting down. See [2. Services and dependencies · What happens when a service stops](02-services.md#what-happens-when-a-service-stops).

Without compose, you can also open three terminals on the host: `temporal server start-dev --db-filename ../myorch-data/temporal.db`, `uv run myorch worker`, `uv run myorch ui`, and start an OpenShell gateway as well (same configuration as `deploy/gateway.toml`). This needs the Temporal CLI and OpenShell installed on the host. It has not been run this way since 2026-11-05 (to be verified).

## 4.4 Optional configuration

### GitHub credentials for a project

Each project names the provider it uses in `providers.github` of `project.yaml`. Write the credentials into `deploy/secrets/providers.yaml`, then run `docker compose -f deploy/compose.yaml run --rm providers`:

```yaml
github_profiles: [blog, mdm, myorch]   # generate a GitHub profile for each of these projects that allows only its own repository
providers:
  github-blog:                         # the value of providers.github
    type: github-blog                  # github-<project>, i.e. the generated profile
    credentials:
      GITHUB_TOKEN: "..."
```

For each project in `github_profiles`, the `providers` service runs `myorch project github-profile <project>` and imports the generated profile into the gateway (updating it if it exists). Then it creates the provider or updates its token. For the file format, see [deploy/README.md, "Installation"](../deploy/README.md#installation).

Current providers:

| provider | Account | Allowed scope | Used for |
|---|---|---|---|
| `claude` | Your Claude subscription (`claude setup-token`, type `claude-code-oauth`), or a GCP service account (Vertex AI, type `claude-code-vertex`, plus `MYORCH_VERTEX_PROJECT`) | Claude API, or Vertex AI's `aiplatform.googleapis.com` | All agent steps. The name must be `claude` |
| `github-blog` | You, `leweii` | Only `leweii/leweii.github.io` | blog |
| `github-mdm` | You, `leweii` | mdm's repository; submodule repositories read-only | mdm |
| `github-myorch` | You, `leweii` | Only `leweii/myorch` | myorch |
| `github-knowingyou-english` | You, `leweii` | Only `leweii/knowingyou-english-learning` | knowingyou-english |
| `github-knowingyou-voice` | You, `leweii` | Only `leweii/knowingyou-voice-record` | knowingyou-voice |
| `azure-artifacts` | The company's private Maven / npm feed (PAT, Packaging Read only) | Private dependency feed | mdm (`providers.artifacts`) |
| `jira-mdm`, `atlassian-mcp` | Your own Atlassian API token | Jira read-only, Atlassian MCP read-only | mdm (reading tickets, knowledge sources) |

The generated rules allow clone, fetch, push, API reads and PR creation for this repository (myorch does not open PRs now). Submodules are read-only. Posting comments is not allowed. For a read-only project, add `--read-only` to `myorch project github-profile`.

About tokens:

- GitHub has no API for creating tokens. Tokens can only be created on the web (Settings → Developer settings). Experimental projects can reuse the token from `gh auth token`. That token can access all your repositories, but the gateway allows only the repositories in the profile.
- A fine-grained token needs Contents **added by hand** under Repository permissions, set to Read and write. Without Contents, the API returns 404. The review flow reads PRs, so it also needs read access to Pull requests.

### Project development images

The build scripts are in the project pack's `image/`. Run them from `myorch-knowledge/`, after building `openshell-claude-code:latest` first:

```sh
_templates/core-ng/image/build.sh               # myorch-dev-core-ng:latest (JDK 21, Node 22, yarn, gh)
mdm/image/build.sh                              # myorch-dev-mdm:latest (adds JDK 25, Node 24, pnpm 9 on top of core-ng)
blog/image/build.sh <path to the blog repository>     # myorch-dev-blog:latest and myorch-e2e-blog:latest
myorch/image/build.sh <path to the myorch repository> # myorch-dev-myorch:latest (dependencies from uv.lock)
_templates/swift-apple/image/build.sh [swift version]  # myorch-dev-swift-apple:latest (swift-frontend for a syntax parse; knowingyou-english, knowingyou-voice)
```

Rebuild when the dependencies change (for example the blog's Gemfile.lock, myorch's uv.lock). Everything in the image **must be installed under `/usr`**, because OpenShell's Landlock does not allow reading `/opt`.

### Syncing project packs

Runs use the snapshot in the volume. After a project pack changes (edited by hand or via git pull), you must sync it:

```sh
uv run myorch project sync <project>
uv run myorch project list            # shows which projects are synced
```

### Local directory knowledge sources

For a knowledge source of type `dir`, the folder must be under the knowledge root directory on the host (`MYORCH_KB_ROOT`, default `~/orca/kb`). compose mounts this directory read-only into the console and the worker at the same absolute path. In a registered path, `~` means the host's home. This way, runs started from the console, Slack or a schedule can mount it just like runs started from the CLI on the host. The whole home is not mounted, because it contains files such as `~/.ssh`. For how to register a knowledge source, see [7. Project packs · Knowledge sources](07-projects.md#knowledge-sources).

## 4.5 Development environment

```sh
uv run pytest          # unit tests (about 380, about 20 seconds); need neither Temporal nor OpenShell
uv run lint-imports    # checks the layer dependencies
```

After changing `src/`: run the tests, then rebuild and restart the worker and the console. When you change the workflow's deterministic logic (`engines/temporal/workflow.py` or `core/run.py`), old runs that have not finished may fail to replay. See [10. Conventions](10-conventions.md#code) and [11. Troubleshooting](11-troubleshooting.md).

## 4.6 Running with docker compose

For the services in compose, who calls whom and the start order, see [2. Services and dependencies](02-services.md#services-in-docker-compose). Sandboxes are still containers on the host's Docker. Only used on macOS; Linux hosts are not tested yet.

How the commands on the host and compose divide the work:

- `uv run myorch ...` on the host works as usual: it connects to the Temporal published at `127.0.0.1:7233`, reads and writes the volume through the host's Docker, and reads the event log through the console.
- Commands that need OpenShell (for example `myorch knowledge check`, `openshell provider list`) run in the container: `docker compose -f deploy/compose.yaml exec worker <command>`. The host has no gateway configuration.
- The console and the CLI in the container act as `MYORCH_USER` from `deploy/.env` (the host's `$USER` when it is not set), not as root.
- Paths, ports, volume names and other settings are in `deploy/.env` (template `deploy/.env.example`). The defaults suit a new machine.
