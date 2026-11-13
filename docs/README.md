# myorch docs

myorch is a flow-driven agent platform. Flows are written in YAML, for example "requirements analysis → implementation → multi-reviewer review → risk approval → push the branch". Temporal handles scheduling, retries and waiting for people. Each step runs in its own OpenShell sandbox, and the sandbox holds no real credentials. Project-specific information (repository, identity, check scripts, knowledge) lives in the project pack, so one flow can be used for many projects. People interact with myorch in the local console (`myorch ui`) or in Slack direct messages. A local Claude Code session can receive run results through a channel and then start follow-up flows. The whole set of services runs with docker compose (`deploy/`).

## Directories: four places

| # | Location | What it is | git |
|---|---|---|---|
| ① | `myorch/` | Code repository: the platform itself (code, flows, permissions, images, helper commands, docs), independent of any project | `leweii/myorch` |
| ② | `myorch-knowledge/` | Project pack repository: how each project is done (repository URL, check scripts, risk rules, project knowledge). Proposals adopted by learn and other flows are written back here | A separate git repository (private `leweii/myorch-knowledge`). myorch only commits locally and does not push |
| ③ | `myorch-data/` | Local run data: `temporal.db`, `events.db`, eval sets, the Slack adapter's state | Not in git |
| ④ | Docker volume `myorch-artifacts` | Runtime files: each run's workspace, snapshots of ②, shared caches. This is the only place the sandbox can see | Not in git |

①②③ sit in the same parent directory. The sandbox cannot see ①②③: the project pack is first turned into a snapshot in ④ with `project sync`, then mounted read-only. Work credentials exist only in the OpenShell gateway and are entered through `deploy/secrets/providers.yaml`. For the relationship diagram, the contents of each directory, and what to do after changing something, see [1. Overview · Directories and storage](01-overview.md#directories-and-storage).

## Platform features

| # | Feature | How | Status |
|---|---|---|---|
| 1 | **Portable** | All configuration is files: flows (`flows/*.yaml`), permissions (`profiles/*.yaml`), project packs (`myorch-knowledge/<project>/`), channels (`channels.yaml`). The framework code contains nothing project-specific. The flow engine is confined to the `engines/` layer and the scheduling logic is in `core/`, so the engine can be replaced. The whole set of services (including the OpenShell gateway) is packaged as docker compose. Moving to another machine takes only `docker compose up` and one credentials file | Local runs and debugging: implemented. docker compose: implemented (macOS). Multi-user in the cloud: design goal; only a single machine is supported now |
| 2 | **Decoupled from the agent harness** | Each kind of agent plugs in through a harness adapter (`runner/harnesses/base.py`). The adapter defines how to build the command line, which model credentials it needs, and whether it can resume a session. A flow only says `harness: <name>`, so changing the agent does not change the flow | Integrated: claude-code, script. codex, pi, opencode: not yet integrated |
| 3 | **The flow engine maintains the development flows** | Development flows (requirements analysis, implementation, review, approval, delivery) are written in YAML and scheduled, retried and paused for people by Temporal. One flow per role, shared by all projects. Project-specific information is all in the project pack; the flow only reads `{{project.x}}`. Differences in flow shape (review strength, whether there is a takeaway) are set in `flows:` in `project.yaml`, project-specific prompt rules are in `prompts/`, and flows use `if:` to tailor themselves per project. Similar projects can share a project pack template (`_templates/`) | Implemented |
| 4 | **OpenShell as the sandbox framework** | Each step runs in a new OpenShell sandbox, which is deleted after use | Implemented |
| 4.1 | Isolated credential management | Work credentials exist only in the OpenShell gateway. The sandbox holds only placeholders; when a request goes out, the supervisor outside the sandbox replaces them with the real credentials. The agent cannot touch any credential | Implemented |
| 4.2 | Least privilege for sandboxes | Each step gets the least permissions according to its profile: image, network policy, credentials to inject, code read-only or writable. Steps that can reach GitHub do not run repository code; steps that run code have no GitHub credentials | Implemented |
| 5 | **Mountable knowledge** | Knowledge sources are registered in `myorch-knowledge/`. There are three kinds: local directory, git repository, MCP service. At the start of each run, a snapshot version of each knowledge source is pinned and mounted read-only into the agent steps' sandboxes (`/sandbox/workspace/knowledge/<name>/`). MCP knowledge sources are not mounted as directories; the agent queries them through MCP and OpenShell replaces the token. The project pack also holds the `guide/` that myorch maintains and the repository's own docs (`repo_docs`). The learn flow proposes improvements based on past runs; after you adopt them, they are written back to `guide/` | Implemented |
| 6 | **The platform changes itself** | The project `myorch` uses the shared development flow (with the `incident` input) to change myorch's own code, flows and docs. When given a run that went wrong, it first decides whether the cause is in the platform | Implemented, not yet run for real |
| 7 | **Several entry points** | The console, Slack direct messages (start, answer, approve), and a local Claude Code session (gets notifications through a channel, reads logs and starts flows with tools, cannot approve) all connect to the same channel layer | Implemented. The Claude Code channel is a research preview |

## Reading order

| # | Chapter | When to read it |
|---|---|---|
| 1 | [Overview: directories and core concepts](01-overview.md) | First time reading, when you want to know what each directory does and how they relate, or when you forget what a term means |
| 2 | [Services and dependencies](02-services.md) | When you want to know which services to start, what depends on what, and what happens when a service stops |
| 3 | [Architecture](03-architecture.md) | When you want to understand how the system fits together, or before changing code |
| 4 | [Setup](04-setup.md) | Setting up a new machine, and starting services at the start of each day |
| 5 | [CLI reference](05-cli.md) | When you want to know what a `myorch` command does and what arguments it takes |
| 6 | [Flows](06-flows.md) | When you want to know which flows exist. Each flow's inputs, steps, and when it needs you are in the [Flow manual](workflows/README.md) |
| 7 | [Project packs](07-projects.md) | Onboarding a new project, or changing a project's checks, risk rules or knowledge |
| 8 | [Writing flows: YAML syntax](08-flow-syntax.md) | When you write a new flow or change one |
| 9 | [Design principles](09-principles.md) | When you want to know why the system is designed this way, before making trade-offs |
| 10 | [Conventions](10-conventions.md) | The rules for credentials, approvals, changing code and maintaining docs |
| 11 | [Troubleshooting](11-troubleshooting.md) | When things don't start, get stuck, or report errors |

## Quick reference

```sh
# Services (all from the myorch/ directory). They start automatically once Docker Desktop is running
docker compose -f deploy/compose.yaml ps              # temporal, gateway, worker, ui are all running
docker compose -f deploy/compose.yaml up -d --build   # after changing src/ or flows/
docker compose -f deploy/compose.yaml run --rm providers   # after changing deploy/secrets/providers.yaml

# A local Claude Code session receives myorch notifications (from the myorch/ directory)
claude --dangerously-load-development-channels server:myorch

# Start runs, answer questions and approve in the console. The command line can do the same:
uv run myorch run flows/development.yaml --project blog -i task="..."
uv run myorch list                       # recent runs, and which ones are waiting on you
uv run myorch status <run_id>            # a run's progress, questions, approvals
uv run myorch reply <run_id> <step> "…"  # answer an agent's question
uv run myorch approve <run_id> <step>    # approve
```

| Interface | Address |
|---|---|
| Console | http://127.0.0.1:8800/ |
| Temporal UI | http://127.0.0.1:8233/ |
| Slack | Send myorch a direct message, or use `/myorch` |

## Other documents

- [ARCHITECTURE.md](../ARCHITECTURE.md): the full design document. This directory covers "how to use it"; ARCHITECTURE.md covers "why". When the two conflict, the code is right; then fix the docs.
- [PLAN.md](../PLAN.md): the implementation plan and the measured results for each item.
- [Flow manual](workflows/README.md) (web version `docs/workflows/index.html`): what each flow does, its inputs, step graph, what each step does, and when it needs you. The text is in `flows/<flow>.md`; regenerate after changing it: `uv run --with markdown python docs/workflows/build.py`.
- `myorch-knowledge/README.md`: the description of the project pack directory.
- [deploy/README.md](../deploy/README.md): docker compose installation, the install script for new machines, the credentials file, Slack, the Claude Code channel, publishing origin/main, ports, known limitations.
- `docs/notes/`: records of individual designs, for example the channel layer, the delivery interface, the research on moving the gateway into Docker, the Slack adapter, the Claude Code channel.
- `docs/index.html`: the web version of this directory and the docs' home page (single page, navigation on the left). Regenerate after changing any chapter: `uv run --with markdown python docs/build-html.py`.
