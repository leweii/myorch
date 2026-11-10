# myorch

中文版：[README.zh-CN.md](README.zh-CN.md)

An agent platform driven by a flow engine. Temporal orchestrates the flows; each step runs in an OpenShell sandbox, and steps hand artifacts to each other through a shared volume. People start runs, answer questions and approve in the local console (`myorch ui`) or in Slack direct messages. A local Claude Code session can receive run results through a channel, read logs and start follow-up flows (it cannot approve). The whole set of services runs with docker compose; see [deploy/README.md](deploy/README.md) (Chinese).

**Docs: https://leweii.github.io/myorch/** (sources in [docs/en/](docs/en/README.md); Chinese: [docs/](docs/README.md), https://leweii.github.io/myorch/zh/): services and dependencies, architecture, setup, CLI reference, flows, project packs, writing flows, design principles, conventions, troubleshooting, status. What each flow does and what each step does: the [Flow manual](docs/en/workflows/README.md). Design rationale: [ARCHITECTURE.md](docs/en/ARCHITECTURE.md) (Chinese original: [ARCHITECTURE.md](ARCHITECTURE.md)).

**Docs ↔ code.** Paths in the docs link to the files here; every code directory has a generated `README.md` listing the doc sections that describe its files (start at [src/myorch/](src/myorch/)), and every package and flow file carries a `Docs:` link.

## Directories

myorch keeps its things in four places:

```
<parent directory>/
├── myorch/              ① code repository (this repo): src/, flows/, profiles/, images/, tools/, deploy/, docs/. Project-independent
├── myorch-knowledge/    ② project pack repository: one directory per project, templates in _templates/. Improvements from learn and other flows are written back here (private repo leweii/myorch-knowledge)
└── myorch-data/         ③ local run data: temporal.db, events.db, eval sets (not in git)

Docker volume myorch-artifacts   ④ runtime files: workspaces, snapshots of ②, shared caches. Sandboxes mount only this
```

What they are for, how they relate and how data flows: [docs/en/01-overview.md · Directories and storage](docs/en/01-overview.md#directories-and-storage).

## Quick start

Prerequisites: macOS, Docker Desktop, gh (logged in), uv. Temporal and the OpenShell gateway are in compose; no separate install.

On a new Mac, clone this repo, put the credential files into `deploy/secrets/`, then run the install script:

```sh
bash deploy/bootstrap-mac.sh       # clones myorch-knowledge, builds images, starts services, syncs project packs, smoke test
```

When it is already installed:

```sh
docker compose -f deploy/compose.yaml up -d --build   # start the services; also after changing src/ or flows/
uv run myorch list                                    # the CLI on the host talks to Temporal in compose
```

Console: http://127.0.0.1:8800/ . Full steps: [docs/en/04-setup.md](docs/en/04-setup.md) and [deploy/README.md](deploy/README.md).

## Development

```sh
uv sync
uv run pytest          # unit tests, need neither Temporal nor OpenShell
uv run lint-imports    # checks the layer dependencies
```

## License

[MIT](LICENSE)
