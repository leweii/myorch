# 1. Overview: directories and core concepts

## In one sentence

You write a **flow**, and start a **run** of it on a **project**. Each **step** in the run executes in a new sandbox. A step can be an agent (claude-code) or a shell script. Steps hand files to each other through a shared **workspace**. When there is a question or an approval, the run stops and waits for a person. You reply in the console, in Slack, or on the command line.

## Directories and storage

myorch keeps its things in four places. The first three are folders in the same parent directory (`deploy/bootstrap-mac.sh` uses `~/orca/projects/` by default); the fourth is a Docker volume.

```
<parent directory>/
├── myorch/              ① Code repository: the platform itself, independent of any project
├── myorch-knowledge/    ② Project pack repository: how each project is done
└── myorch-data/         ③ Local run data: records of Temporal and the worker

Docker volume myorch-artifacts   ④ Runtime files: workspaces, snapshots, caches; everything the sandbox mounts comes from here
```

| # | Location | What it holds | Who writes it | git | When you change computers |
|---|---|---|---|---|---|
| ① | `myorch/` | Code (`src/`), flows (`flows/`), permissions (`profiles/`), sandbox images, helper commands (`tools/`), deployment (`deploy/`), docs | You (changing the platform) | `leweii/myorch` | clone |
| ② | `myorch-knowledge/` | Each project's project pack: repository URL, check scripts, risk rules, project knowledge (`guide/`), development image. Templates for similar projects. Knowledge source registrations | You; the console (registering knowledge sources); proposals adopted from learn, onboard, postmortem | Private repository `leweii/myorch-knowledge` | clone |
| ③ | `myorch-data/` | Temporal database, event log, eval sets, records of the console and Slack | Temporal, worker, console | Not in git | No need to bring it. The new computer starts from scratch |
| ④ | volume `myorch-artifacts` | Each run's workspace, snapshots of project packs and knowledge sources, projects' shared caches, helper commands | worker, sandbox, console, `project sync`, `knowledge sync`, `myorch init` | Not in git | No need to bring it. `myorch init` creates it, `project sync` makes the snapshots again |

Credentials are not in any of these four places. They exist only in the OpenShell gateway and are imported from `deploy/secrets/providers.yaml` (see [deploy/README.md](../../deploy/README.md)).

### How they relate

<!-- diagram: storage-relations.html -->

Diagram file: [diagrams/en/storage-relations.html](../diagrams/en/storage-relations.html).

Data flow in one run:

1. **Start**: the console, Slack, the Claude Code channel, the CLI or a schedule reads the flow file from ① and `project.yaml` and `prompts/` from ②, tailors the flow to the project (`if:`), and fills in `{{project.x}}`. The local directory and git repository knowledge sources the run mounts are first synced into snapshots in ④. Then the run is submitted to Temporal.
2. **Execute**: the worker starts a sandbox for each step and mounts from ④ this run's workspace (read-write), the project pack snapshot, the knowledge source snapshots and the helper commands (read-only). Host actions (for example `project-apply`) are executed directly by the worker, without a sandbox.
3. **Record**: Temporal writes the run state into `temporal.db` in ③; the worker writes events and live output into `events.db` in ③. The console reads these two places.
4. **Learn**: learn, onboard and postmortem make proposals. After you adopt them, the host action `project-apply` changes ②, makes a git commit, and makes the snapshot again. The next run uses them.
5. **Deliver**: after approval, the branch is pushed to **the project's own** GitHub repository (`repo` in `project.yaml`), not to any of these four places. myorch does not open PRs; people open and merge PRs.

Two rules:

- The sandbox can see only ④, not ①, ② or ③. Project packs and knowledge sources are first made into snapshots in ④, then mounted read-only. So after you change a project pack in ②, run `project sync` before runs can see it.
- Snapshots of project packs and knowledge sources are versioned; each sync writes a new directory. When a run starts, the mandate records which version it uses, and every step mounts that version. So later changes to ② do not affect runs already in progress. Exception: when a run itself writes back to ② through `project-apply`, the following steps mount the new version it wrote.

### ① Directories in the myorch repository

| Directory | Contents |
|---|---|
| `src/myorch/` | Python code, see [3. Architecture](03-architecture.md#code-map) |
| `flows/` | Flows. One per role, shared by all projects: `development`, `review`, `learn`, `postmortem`, `eval`, `sweep`, `onboard`. The differences between projects are in the project packs. For the list of flows see [6. Flows](06-flows.md). `flows/<flow>.md` is the source text of the Flow manual |
| `flows/lib/` | Fragments: `sync-base`, `review-loop`, `e2e`, `deliver`, `choose-proposals` |
| `profiles/` | Permission configuration: `dev-git`, `dev-write`, `dev-read`, `ticket-read` |
| `images/claude-code/` | The default sandbox image (with the claude CLI installed) |
| `openshell/providers/` | Provider profiles imported into the OpenShell gateway (access rules for Claude, GitHub, Jira, Atlassian MCP) |
| `tools/` | Helper commands installed into ④ and mounted into every sandbox: `myorch-ask` (ask a question), `myorch-checks` (run the project's checks), `myorch-stream` (turn the agent's output into a readable log), `myorch-mcp` (connect the agent to MCP knowledge sources, allowing only registered tools), `myorch-steps` (read in parts the manifests and logs of earlier steps in this run) |
| `channels.yaml` | The mapping between people and their accounts on each channel (`identities`: GitHub, Slack), and the watchdog configuration. compose mounts it read-only into the containers |
| `deploy/` | docker compose and the files it uses: the myorch image, gateway configuration, credentials import script, smoke test, Slack app manifest, install script, publish script. Credentials are in `deploy/secrets/` (not in git, not in images). See [deploy/README.md](../../deploy/README.md) |
| `.mcp.json` | Registers myorch as an MCP server and channel for the local Claude Code (`channels/claude_code.py`) |
| `tests/` | Unit tests; they do not depend on Temporal or OpenShell |
| `docs/` | These docs. `docs/workflows/` is the Flow manual, `docs/notes/` holds design notes |
| `ARCHITECTURE.md`, `PLAN.md` | Design document, implementation plan |

### ② Structure of myorch-knowledge

```
myorch-knowledge/
├── README.md
├── _templates/core-ng/      Template for similar projects: Core-NG Java projects
├── blog/                    Personal blog leweii/leweii.github.io
├── mdm/                     Company repository example-org/master-data-management, extends core-ng
└── myorch/                  myorch itself (leweii/myorch)
```

Global knowledge sources are registered in `_sources/<name>.yaml`. This directory does not exist yet.

Files in each project directory (template directories are the same):

| File | Purpose | Who reads it |
|---|---|---|
| `project.yaml` | Repository, base branch, commit identity, provider role mapping, image, branch name template, conventions, approvers, Jira, sweep scope, e2e configuration, settings for the shared flows (`flows:`). With `extends: <template>`, fields and files not given are taken from the template | The CLI and the console read it when starting a run |
| `prompts/` | Project rules added into the shared flows' prompts: `develop.md`, `review.md`, `verify.md`, `fix.md`, `risk.md`. The template's come first, the project's after | Expanding the flow at start |
| `guide/` | Project knowledge: development guide, review guide, writing conventions | Agent steps. learn, onboard and postmortem change it |
| `checks.sh` | The project's checks: reads the list of changes, writes `report.md` and `result.json` | The checks step of the review loop, `myorch-checks` |
| `setup.sh` | Prepares a new checkout, for example by linking preinstalled dependencies | The step that checks out the branch |
| `risk-rules.yaml` | Which changed paths need how many approvers | The `risk-rules` step |
| `security.md` | What the security review focuses on | The `security` reviewer |
| `policy.yaml` | OpenShell network policy for steps that need network access | runner |
| `sources/` | Knowledge sources registered for this project (mdm has them) | Knowledge source mounting. Not in snapshots |
| `providers/` | This project's provider profiles (mdm's private dependency source) | compose's `providers` service imports them into the gateway. Not in snapshots |
| `e2e/` | End-to-end test cases and scripts (blog) | `lib/e2e.yaml` |
| `image/` | Dockerfile and build script for the development image | You build it by hand. Not in snapshots |

For the fields in detail, see [7. Project packs](07-projects.md).

### ③ Contents of myorch-data

| Path | Written by | Contents |
|---|---|---|
| `temporal.db` | Temporal dev server | Workflow state and history. Finished runs are kept for 24 hours (the dev server default); after that they are only in the event log |
| `events.db` | worker | The event log (`events` table), and live output of running steps (`output` table, kept for 7 days). Processes on the host must not open it directly; see [2. Services and dependencies](02-services.md#who-reads-and-writes-files-and-configuration) |
| `evals/` | `myorch eval`, postmortem | Eval cases and expected answers |
| `learn/<project>/takeaways.json` | learn's host action | Which runs' takeaways learn has already learned from |
| `ui/runs/<run_id>.jsonl` | console | What you said in the console |
| `watchdog.json` | The console process (`myorch serve`) | Alerts the watchdog has already reported |
| `slack/state.json` | Slack adapter | The event cursor, and each run's thread and messages in Slack |
| `local-repos.yaml` | You | Locations of local clones; new runs copy code from here (has no effect in compose, see [deploy/README.md](../../deploy/README.md#已知限制)) |
| `reviews/<run_id>.html` | `myorch review` | A single page for looking at the changes before approving |
| `runs/<run_id>/` | `myorch export` | A copy of the workspace exported from the volume. The real workspace is in ④ |

### What to do after changing something

`dc` is short for `docker compose -f deploy/compose.yaml`. `dc up -d --build` interrupts steps that are executing. To publish commits on origin/main, use `deploy/update.sh`; it waits for executing steps to finish before restarting (see [deploy/README.md](../../deploy/README.md#发布-originmain)).

| Changed | What to do |
|---|---|
| `src/`, `flows/`, `profiles/` in ① | `dc up -d --build`. Flow files are built into the image, so changing a flow also needs a rebuild |
| `tools/` in ① | `dc up -d --build` (the `init` service installs `tools/` into the volume on every start) |
| `images/claude-code/` in ① | `docker build -t openshell-claude-code:latest images/claude-code` |
| `channels.yaml` in ① | `dc up -d --force-recreate ui worker` |
| `pyproject.toml`, `uv.lock` in ① | `dc up -d --build`, then rebuild the myorch project's development image: in ②, run `myorch/image/build.sh <path to myorch>` |
| `deploy/secrets/providers.yaml` | `dc run --rm providers` |
| `deploy/secrets/slack.env` | `dc up -d --force-recreate ui` |
| A project pack in ② | `uv run myorch project sync <project>`, then commit in ② |
| `image/` in ② | Run this project's `image/build.sh` |
| Knowledge source contents (local directory or git repository) | Nothing. They are synced automatically when a run starts: local directories every time, git repositories only when the snapshot is more than 1 hour old. You can also run `uv run myorch knowledge sync <name>` by hand |

## Glossary

| Term | What it is | Where |
|---|---|---|
| **flow** | A set of steps, plus their dependencies, loops and approvals. Written in YAML. `role:` at the top states its role. Shared by all projects, tailored by project settings (`flows:`, `prompts/`) and `if:` | `flows/*.yaml` |
| **fragment** | A reusable step or loop. Flows reference it with `use:` | `flows/lib/*.yaml` |
| **run** | One execution of a flow. Its ID is `<flow name>-<8 hex digits>`, for example `development-38b24c5b`, and equals the Temporal workflow ID | Temporal, and `runs/<run_id>/` in the volume |
| **label** | A marker for finding runs, for example the Jira ticket number `MD-18700`. Added automatically for ticket inputs | `labels` in the mandate |
| **step / instance** | A step, and each concrete instance after expansion. For example, `review.r2.x1` is the first reviewer in round 2 | |
| **harness** | How a step executes: `claude-code` (agent) or `script` (bash, alias `shell`) | `src/myorch/runner/harnesses/` |
| **host action** | A deterministic step the worker executes outside the sandbox. Only a few registered actions are allowed, for example `project-apply`, which writes back to the project pack | `src/myorch/runner/actions.py` |
| **profile** | A step's permissions: network policy, which credentials to inject, code read-only or writable | `profiles/*.yaml` |
| **provider** | A credential stored in the OpenShell gateway, for example the Claude token or a GitHub token. myorch knows only the name, never the value | `docker compose -f deploy/compose.yaml exec worker openshell provider list` |
| **project pack** | All of a project's specific information: repository, base branch, accounts, image, check scripts, risk rules, knowledge | `myorch-knowledge/<name>/` |
| **template** | A project pack shared by similar projects. A project inherits it with `extends:` | `myorch-knowledge/_templates/<name>/` |
| **knowledge source** | Read-only knowledge mounted for a run: a local directory, a git repository or an MCP service. The project's `knowledge` field lists which are mounted by default | `myorch-knowledge/_sources/`, `sources/` of a template or project |
| **workspace** | All the files of a run: code, docs, memory, each step's artifacts and logs | `runs/<run_id>/` in the volume. In the sandbox it is `/sandbox/workspace` |
| **manifest** | The `manifest.json` written at the end of each step: status, summary, file list, structured output (`outputs`) | `.myorch/steps/<instance>/out/manifest.json` |
| **outputs** | Values in the manifest for the flow to use, for example `{"blocking": 2}`. Conditions, loops and approver counts read them | |
| **mandate** | The authorization record fixed when the run starts: who started it, inputs, flow version, project pack version, knowledge source versions, labels | `.myorch/mandate.json` in the workspace |
| **console** | The local web page (`myorch ui`). Here you start runs, see conversations and changes, answer questions, and approve | `src/myorch/ui/` |
| **event log** | The SQLite table the worker writes, recording each run's events (start, step start and end, question, approval, end) | `myorch-data/events.db` |
| **channels** | The relay layer between myorch and the platforms: it relays events, commands and run contents. Adapters: the console, Slack, the Claude Code channel | `src/myorch/channels/` |

## Two kinds of moments that need you

| | Who decides to ask | How you respond |
|---|---|---|
| **Question** (`waiting_input`) | The agent decides during execution that it needs you to make a decision. Only steps with `interaction: allowed` can ask questions | Reply directly in the conversation in the console or Slack. The reply is sent unchanged to the step that asked. You can also use `myorch reply <run> <step> "answer"` |
| **Approval** (`waiting_approval`) | The flow requires someone to let this step through before it executes. The number of approvers can be computed by the risk verdict | The **Approve** (批准) and **Reject** (驳回) buttons in the console. When the flow allows it, there is also **Request changes** (提修改意见): the run goes back to an earlier loop and makes changes according to your comments. On the command line: `myorch approve / reject / changes <run> <step>` |

Two other situations also ask you:

- A loop has run out of rounds without converging: run one more round, accept the current result, or stop.
- A step with `on_failed: ask` still fails after its retries are used up: start over, skip, stop, or give a hint so the agent tries again, resuming its last session.

For when each flow needs you, see the [Flow manual](workflows/README.md).

## The lifecycle of a run

<!-- diagram: run-lifecycle.html -->

Diagram file: [diagrams/en/run-lifecycle.html](../diagrams/en/run-lifecycle.html).
