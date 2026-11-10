# onboard

Teaches myorch about a project. It reads the repository, tries the project pack's checks on the last few commits, and then proposes additions and fixes to the project pack. You pick which ones to adopt, and it writes them back to `myorch-knowledge/<project>/`. Later development, review and risk verdicts all depend on this knowledge.

Run it once when you take on a new project, and again when the repository structure changes a lot. It pushes nothing to the project repository and only changes the local `myorch-knowledge`. Like the other flows, it is shared by all projects. The difference from [learn](learn.md): learn learns from runs that already happened; onboard looks only at the repository itself and can change more files (checks script, setup script, approval rules, security focus).

```sh
uv run myorch run flows/onboard.yaml --project <project>
uv run myorch run flows/onboard.yaml --project <project> -i focus="database migrations"
```

Console: choose the project first, then the task **Onboard project** (接入项目). The first message is **Focus areas** (要重点看的地方) and can stay empty.

<!-- flow-graph -->

## Before you start

The project pack needs at least a `project.yaml`, and `uv run myorch project sync <project>` must have been run. The project's GitHub provider must be set up and the development image built. See [7. Project packs · Onboarding a new project](../07-projects.md#onboarding-a-new-project) for the steps.

`focus` is appended as is to analyze's task description, for example "frontend checks" or "database migrations", so that it spends more effort there.

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `sync` | Clones the repository, checks out the latest base branch, initialises submodules ([`lib/sync-base.yaml`](../../../flows/lib/sync-base.yaml)) | `repo/` |
| `try-checks` | Runs `setup.sh` first when there is one, then runs `checks.sh` on the last 5 commits as a sample change, and records the results and timings | `docs/onboard/checks/` |
| `analyze` | Reads the repository, the repository's own docs, the current project pack and the trial results, and writes a project overview and change proposals. Changes neither the repository nor the project pack | `docs/onboard/report.md`, `proposals.json`, `proposals.md`, `open-questions.md` |
| `choose` | Lists the proposals and waits for you to reply with numbers. Skipped when there are no proposals ([`lib/choose-proposals.yaml`](../../../flows/lib/choose-proposals.yaml)) | `docs/onboard/accepted.json` |
| `apply` | Host action `project-apply` with `scope: onboard`: validates, writes back to the project pack, commits in `myorch-knowledge`, re-syncs the snapshot. Skipped when nothing was adopted | A commit in `myorch-knowledge` |

### try-checks: trying the checks

- Purpose: see whether `checks.sh` finds the right modules, how long it takes, and which checks fail for reasons unrelated to the change (environment, network, cache).
- The sample change is the last 5 commits of the base branch. When the repository has fewer than 6 commits, it uses the whole history (starting from the empty tree). The changed files are listed in `changed.txt`.
- The checks run under the [`checks.sh` conventions](../07-projects.md#checkssh-convention), with `CHECKS_DIR` set to `docs/onboard/checks/`.
- Failed checks, a failing `setup.sh` or a missing `checks.sh` are only recorded in the results; the step still succeeds. These are exactly what analyze needs to look at.

### analyze: what it needs to find out

| Area | Content |
|---|---|
| Structure | How the modules are divided, their dependencies, which parts are external contracts (interfaces, protocols, database migrations), where the frontend is |
| Build and checks | How to build, how to test only the affected parts, which private dependencies or environment are needed; whether `checks.sh` finds the right modules, misses checks, or fails for unrelated reasons |
| Conventions | Code style, how tests are written, commit and PR conventions, and whether they match what `guide/` says |
| Risk | Changes to which paths need more approvers (whether `risk-rules.yaml` misses anything or catches things it should not), what needs special attention for security |

- Each conclusion names its basis (file paths).
- At most 8 proposals, of type `fill-gap` (add knowledge), `fix-checks` (fix the checks), `fix-rule` (fix the approval rules) or `security` (security focus).
- `guide/` gets only what the repository's own docs do not say and what affects the agent's judgement. The wording is short.
- Questions that need a person to answer go into `open-questions.md`. The flow does not ask you these questions. After reading them, edit the project pack by hand, or run again with a `focus`.

## Where you step in

| When | Console / Slack | Command line |
|---|---|---|
| `choose`: pick which to adopt | Reply with numbers in the conversation, or click `all` or `none` | `uv run myorch reply <run> choose "1,3"` |

- Read `docs/onboard/report.md` and `proposals.md` first: the **Docs** (文档) tab in the console's right panel, or `uv run myorch ws <run> docs/onboard`.
- Reply with the numbers only: `1,3`, `1、3` and `1 3` all work. `all` or `全部` adopts all of them; `none`, `无` or `0` adopts none. When a number does not exist or the reply cannot be understood, it asks again.
- Once the reply is accepted, it starts running and cannot be changed. Without a reply for 24 hours, this step times out and fails.

## Writing back to the project pack

| Can change | Cannot change |
|---|---|
| Files under `guide/`, including new ones | `project.yaml`, `image/`, `policy.yaml`, `providers/`, `sources/` |
| `checks.sh`, `setup.sh` | `checks.env` (learn can change it) |
| `risk-rules.yaml`, `security.md` | Other files, including `prompts/` |

onboard cannot change the development flow's per-project settings (`flows.development` in `project.yaml`, `prompts/`; see [development's "Per-project differences"](development.md#differences-by-project)). A person changes them by hand when needed.

A change can be written in three ways: exact replacement (`edits`; `old` must occur exactly once in the file), append (`append`), whole file (`content`).

- It writes only the project's own directory `myorch-knowledge/<project>/`. The template `_templates/<template>/` stays unchanged.
- `guide/`, `checks.sh` and `setup.sh` inherit by override: a file the project only inherits from the template is first copied from the template on its first change, then changed. So `edits` can quote the original text seen in the snapshot.
- `risk-rules.yaml` and `security.md` inherit by concatenation: the snapshot holds the template's content plus the project's content, and the project's own file holds only the project-specific part. So `security.md` uses only `append`, and `risk-rules.yaml` uses only `content`, writing the complete project rules file (a `rules:` list, optional `default:`). Syncing the snapshot joins it with the template's rules again.
- The other rules are the same as for learn; see [learn's "Writing back to the project pack"](learn.md#writing-back-to-the-project-pack): write only when everything validates, commit only the files it wrote, re-sync the snapshot after writing, `git revert` is possible. The commit message is `onboard(<project>): #1 title; …`, and the body has `myorch-run: <run_id>`.

## Artifacts

All under `docs/onboard/` in the workspace.

```
docs/onboard/
  checks/
    changed.txt        files of the sample change
    report.md          checks.sh's report
    result.json        passed, failed, skipped
  report.md            project overview and conclusions, each with its basis
  proposals.json, proposals.md   proposals
  open-questions.md    questions for a person to answer
  accepted.json        the numbers you adopted
```
