# 7. Project packs

Flows contain nothing project-specific. The repository, branch, check commands, risk rules and project knowledge all live in the project pack `../myorch-knowledge/<name>/`. `myorch-knowledge` is itself a git repository: every change that learn or onboard writes back is one commit.

Flows are shared by all projects, one per role. See [6. Flows](06-flows.md). The differences in flow shape between projects are set in `flows:` in `project.yaml`. Project-specific prompt clauses go in `prompts/`. See "Project settings for the shared flows" and "Prompt slots". For the steps of each flow, see the [Flow manual](workflows/README.md).

## Current projects

| Project | Repository | base | Extends | Identity | Flow settings (`flows.development`) and `prompts/` | Status |
|---|---|---|---|---|---|---|
| `blog` | `leweii/leweii.github.io` | master | — | the owner, `leweii` (before 2026-11-05 the bot `leweiibot`) | `reviewers: 1`, `max_rounds: 1`, `security: false`, `on_exhausted: accept`. `default: 1` in `risk-rules.yaml`. prompts: develop, review | In use. Has e2e (Playwright). onboard has been run |
| `mdm` | `example-org/master-data-management` | master | core-ng | the owner, `leweii` | Defaults, plus `takeaway: true` and `open_pr: true`. Has Jira. prompts come from the template | In use. Provider created, onboard completed once (2026-11-01). Reads Jira tickets |
| `myorch` | `leweii/myorch` | main | — | the owner, `leweii` | Defaults, plus `incident: true`. prompts: develop, risk | The platform itself (created 2026-11-05). Checks run offline, image `myorch-dev-myorch`. The project pack's README says it has not had a real run yet |
| `_templates/core-ng` | — | develop | — | — | prompts: develop (Gradle dependencies, `*-interface` modules), review (`*-interface` contracts) | Core-NG template (Java/Gradle multi-module + React). Checks, caches, policy and image were tried on a trial project |

The three projects use the same set of flows (development, review, learn, postmortem, eval, sweep, onboard).

## Directory layout

```
myorch-knowledge/
  _sources/                 optional: knowledge sources registered globally (see "Knowledge sources")
  _templates/core-ng/       template: shared by one tech stack
  <project>/
    project.yaml            required: repository, identity, providers, image, settings for the shared flows (flows:), etc. (see below)
    prompts/                optional: project clauses inserted into the shared flows' prompts: develop.md, review.md, verify.md, fix.md, risk.md
    guide/                  knowledge maintained by myorch: development method, review guide, risk notes. learn and onboard change it
    checks.sh               the project's checks (CI)
    checks.env              optional: settings for checks.sh (core-ng's checks.sh reads it)
    setup.sh                optional: preparation after a fresh checkout (link preinstalled dependencies, configure caches)
    risk-rules.yaml         changed paths → how many approvers are needed
    security.md             focus areas for the security review
    policy.yaml             optional: OpenShell network policy for steps that need network access
    e2e/                    optional: end-to-end tests (serve.sh, run.sh, tests/, README.md). Needs e2e.image in project.yaml
    <other directories>/    optional: material for agents to read, e.g. blog's profile/. Its own README explains how to use it
    image/                  Dockerfile and build.sh for the development image (not in the snapshot)
    providers/              optional: OpenShell provider profiles this project uses (not in the snapshot)
    sources/                optional: knowledge sources registered by this project (not in the snapshot)
    README.md               notes for people (status, steps to enable)
```

## project.yaml fields

The final authority for the fields is the `Project` model in `src/myorch/project.py`.

| Field | Required | Description |
|---|---|---|
| `extends` | | Name of the template to extend (`_templates/<name>/`) |
| `repo` | ✓ | `owner/name` |
| `base` | | Base branch, default `main` |
| `submodules` | | Other repositories (`owner/name`) to clone along with it. The GitHub provider has read-only access to them |
| `identity.account` | ✓ | The GitHub account to act as (used when generating the GitHub provider profile) |
| `identity.git_name` / `git_email` | ✓ | Author of commits. In the sandbox these are `MYORCH_GIT_NAME` and `MYORCH_GIT_EMAIL` |
| `identity.self_is_operator`, `identity.trigger` | | Reserved fields (how myorch was summoned in the days of the GitHub channel). No code uses them now |
| `providers` | | Role → provider name in the gateway. The roles are `github` (used by dev-git), `artifacts` (private dependency feed, used by dev-git and dev-write) and `jira` (reads tickets, used by ticket-read). `null` means not used. Leaving out `artifacts` or `jira` equals `null`. If `github` is left out, the provider named `github` is used |
| `image` | ✓ | Development image, e.g. `myorch-dev-blog:latest` |
| `e2e.image` | | Image that holds the end-to-end test tools, e.g. `myorch-e2e-blog:latest`. Empty (default): no end-to-end tests. See "e2e/ convention" |
| `e2e.profile` | | Permissions of the end-to-end test steps, default `dev-read` (repository read-only, no credentials, no internet) |
| `e2e.acceptance` | | Default `true`: the agent writes test cases from the acceptance criteria and runs them. `false`: only the fixed cases run |
| `jira.url` | | Jira site, e.g. `https://example.atlassian.net`. Empty (default): tickets are not read. When set, development shows the ticket inputs (`ticket`, `at` for replays, `ticket_as_of`, `ticket_comments`), `task` is no longer required, and `fetch-ticket` reads the ticket and its screenshots before develop and writes them to `docs/external/ticket.md` and `docs/external/attachments/`. Read-only, nothing is written back |
| `jira.key` | | Prefix added when a ticket is given as a number only, e.g. `MD`: `18700` → `MD-18700` |
| `jira.auth` | | `basic` (default, Atlassian Cloud: email plus API token) or `bearer` (personal access token on Data Center) |
| `jira.email` | | With `basic`: the email of the account that owns the token |
| `branch` | | Branch name template, default `myorch/{{run.id}}`, rendered at run time. Can use `{{inputs.x}}`. If rendering fails (e.g. the flow has no such input), it falls back to `myorch/<run_id>`. When you continue (`from_run`), the original run's ID is used, so the branch is the same as the original run's |
| `conventions` | | Conventions for commits and PRs, written into the prompt of every agent step |
| `pr_template` | | Path of the PR template in the repository. deliver puts the template's content in its output `delivery.pr_template`, for whoever opens the PR |
| `repo_docs` | | Knowledge the repository carries itself (`CLAUDE.md`, `.claude/skills/`, etc.). The prompt points the agent to it |
| `required_inputs` | | Inputs this project requires, e.g. `[ticket]`. Applies only to flows that declare the input |
| `knowledge` | | Which knowledge sources this project's runs mount by default (a list of names). You can add or remove some when you start a run. See "Knowledge sources" |
| `sweep` | | Which files the sweep flow looks at: `paths` (directories or files; leave out for the whole repository), `include`, `exclude` (globs, `**` allowed), `chunk` (files per batch, default 20, 1–200) |
| `policy` | | Name of the policy file in the project pack. Used when a profile says `policy: project` |
| `shared` | | Read-write directories (caches) shared across runs and mounted in every step, e.g. `[gradle]` → `/sandbox/workspace/shared/gradle`. The data is in `shared/<project>/gradle` on the volume |
| `flows.development` | | How the shared development flow runs: `reviewers`, `max_rounds`, `security`, `on_exhausted`, `takeaway`, `incident`, `open_pr`. See "Project settings for the shared flows" |
| `approvers` | | People who can approve changes in this project. The number of approvers that development's risk verdict asks for is capped at the size of this list. Empty means no cap |
| `review` | | Only one value, `local` (default): changes do not leave the sandbox before approval. Can be left out |
| `allow` | | Reserved field (GitHub accounts that can command myorch). No code uses it now |

Flows read these values with `{{project.x}}`, e.g. `{{project.base}}`, `{{project.e2e.image}}`. There are also two computed values: `{{project.repo_docs_list}}` (one `- repo/<path>` per line) and `{{project.submodules_list}}` (space-separated). `{{project.prompts.<slot>}}` comes from `prompts/`, not from `project.yaml` (`prompts` written in `project.yaml` is ignored). `myorch project show <name>` shows all the merged values.

Example (mdm, excerpt):

```yaml
extends: core-ng
repo: example-org/master-data-management
base: master
submodules: [example-org/protos]
identity:
  account: leweii
  git_name: Jakob He
  git_email: <email used for commits>
  self_is_operator: true
providers:
  github: github-mdm
  artifacts: azure-artifacts
  jira: jira-mdm
jira: {url: "https://example.atlassian.net", key: MD, auth: basic, email: <email of the account that owns the token>}
image: myorch-dev-mdm:latest
policy: policy.yaml
branch: "MD-{{inputs.ticket}}-myorch-{{run.id}}"
required_inputs: [ticket]
flows:
  development: {takeaway: true}
approvers: [jakobhe]
repo_docs: [CLAUDE.md, openspec/specs/]
```

### Connecting Jira

1. In `openshell/providers/jira-read.yaml`, replace the host with your Jira site, import the profile, and create the provider with your token (the commands are at the top of the file). The token is stored only in the OpenShell gateway. The sandbox sees only a placeholder. The provider allows only GET. To download a ticket's screenshots, also allow `GET /rest/api/2/attachment/content/**`. See `jira-read-example.yaml`, which has the site already filled in.
2. In the project's `project.yaml`, write:

```yaml
providers:
  jira: jira-<site>
jira: {url: "https://<site>", key: MD, auth: basic, email: <email of the account that owns the token>}
```

3. After that, development's form has the ticket inputs, and runs with a `ticket` read the ticket first. The step that reads the ticket uses the `ticket-read` profile (only the `jira` credentials, repository read-only).

## Project settings for the shared flows (flows:)

Flows are shared by all projects. The project decides their shape. `flows.development` in `project.yaml` decides how `flows/development.yaml` runs (`Development` in `src/myorch/project.py`). You do not choose anything when you start a run. The project's settings are applied automatically.

| Field | Default | Range | Effect |
|---|---|---|---|
| `reviewers` | 3 | 1–8 | How many reviewers run in parallel per round (review-loop's `reviewers`) |
| `max_rounds` | 3 | 1–10 | Maximum number of review rounds |
| `security` | `true` | | Runs an extra security reviewer in each round |
| `on_exhausted` | `ask` | `ask`, `fail`, `accept` | When blocking issues remain after the last round: ask you, fail, or accept the current result and continue (unresolved issues then need human approval through `risk-rules`) |
| `takeaway` | `false` | | Commits the task information and takeaway to `myorch/<date>-<run_id>/` in the project repository. learn reads them from the base branch. Only turn it on in repositories that welcome such a directory |
| `incident` | `false` | | Shows the `incident` input: a run that went wrong (in any project), mounted read-only for develop. How to use it is written in the project's `prompts/develop.md` |
| `open_pr` | `false` | | After approval, `deliver` also opens the pull request with the project's GitHub provider (or reuses the branch's open one) and outputs `pr_url`. The console shows it in the run's conversation, and Slack and the run list show it too. The provider needs `POST /repos/<repo>/pulls` (`myorch project github-profile` allows it). Off: people open the PR themselves |

- Templates can set these. The project overrides the template (`project.yaml` is deep-merged).
- Flows pass `{{project.flows.development.x}}` to fragment parameters (passed with its type, see [8. Flow syntax · Fragments](08-flow-syntax.md#fragments-use)), and use expressions such as `if: "project.flows.development.takeaway"` to decide whether some steps and inputs exist (see [8. Flow syntax · Per-project trimming](08-flow-syntax.md#per-project-trimming-if)).
- Not every difference is in `flows:`. Whether the project has Jira (`jira.url`) decides the ticket inputs and `fetch-ticket`. Whether it has an e2e image (`e2e.image`) decides whether the review flow runs end-to-end tests. `risk-rules.yaml` decides the number of approvers.
- When an old mandate or a hand-written project dictionary has no `flows` or `prompts`, `project.with_defaults` fills in the defaults.
- `flows.ablate` (empty by default): prompt rules removed when the flow is expanded. See [8. Flow syntax · Rules in prompts](08-flow-syntax.md#rules-in-prompts-rule). It is for evals. It is usually set temporarily during a replay with `myorch run --set flows.ablate=...`, not written in the project pack.

## Prompt slots (prompts/)

Project-specific prompt clauses go in `prompts/<slot>.md` in the project pack. When the flow is expanded, the text is inserted verbatim into the matching step's prompt (`{{project.prompts.<slot>}}`). It is not left for the agent to read on its own. The inserted text is visible in the expanded flow (`.myorch/flow.yaml` in the workspace).

| Slot | Inserted into |
|---|---|
| `develop` | development's `develop` |
| `review` | review-loop's `review` (development, review, eval) |
| `verify` | review-loop's `verify` |
| `fix` | review-loop's `fix` |
| `risk` | development's `classify-risk` |

- With a template, the template's text comes first, then the project's, with a blank line between them. If neither exists, the value is an empty string.
- If a file name is not one of these five slots (or is not `.md`), reading the project pack fails with an error, so that text under a misspelled name is not silently dropped. Files starting with `.` and subdirectories are ignored.
- Only put requirements specific to this project or this tech stack here. General engineering requirements go in the flow, so every project benefits.
- Examples: the core-ng template's `review.md` asks to check the `*-interface` modules when interface behavior changes. myorch's `develop.md` explains how to analyze an `incident`, which platform matters to consider in the design, and "what to do after the merge". blog's `develop.md` asks for short documents.
- As with `project.yaml`, the files in myorch-knowledge are read directly when a run starts, so changes take effect at once. Flows do not change `prompts/` (no `project-apply` scope includes it). After editing by hand, commit in myorch-knowledge.

## Template inheritance rules

When a project sets `extends: core-ng`:

- `project.yaml`: deep-merged, the project's values win.
- Other files: overridden file by file. If the project has the file, the project's is used. Otherwise the template's. `guide/` is the files of both combined.
- The rules in `risk-rules.yaml`, and `security.md`: concatenated, the template's first. `default` in `risk-rules` takes the last value that sets it.
- `prompts/<slot>.md`: concatenated per slot, the template's first.
- The first time learn or onboard changes a file that is only inherited from the template, the file is first copied into the project and then changed (copy-on-write). The template stays unchanged.
- Knowledge sources registered in the template's `sources/` can be used by every project that extends it.

`myorch project show <name>` shows the merged `project.yaml`.

## checks.sh convention

The convention is the same for all projects. The review loop's `ci` step, `/sandbox/bin/myorch-checks` (called by agents) and onboard's `try-checks` all call it:

- Input: the environment variable `BASE_REF` (the starting point for the comparison) and the file `$CHECKS_DIR/changed.txt` (the list of changed files). The current directory is the repository root.
- Output: `$CHECKS_DIR/report.md` (a report for people and agents) and `$CHECKS_DIR/result.json`: `{"passed": true/false, "failed": [...], "skipped": [...]}`. It can add an `environment` list for failures caused by the test environment (not by the code), e.g. those matched by core-ng's `ENV_FAILURE_PATTERNS`.
- Once the report is written, `exit 0`. Failed checks go in `result.json`, not in the exit code.
- **Do not report a pass when nothing was checked**, e.g. when a tool fails to start. That counts as a failure, with the log attached.
- Project-specific settings go in `checks.env`, which checks.sh reads itself (e.g. mdm's `FRONTEND_SCRIPTS`, `GRADLE_JVMARGS`).

`myorch-checks` first runs `setup.sh` (if present), then `checks.sh`. Its exit code is 0 when `passed` is true. The changed files it computes include commits since base, staged changes, unstaged changes and new untracked files. So when the agent runs the checks before committing, new files are checked too.

## e2e/ convention (optional)

When `e2e.image` in project.yaml is not empty, the review flow runs the end-to-end test fragment `flows/lib/e2e.yaml` after the review (the `use:` entry has `if: "project.e2e.image"`). The review flow only tests and does not fix: `fix: false`, 1 round. The development flow does not run end-to-end tests at the moment.

The fragment only defines the steps: fixed cases + acceptance cases → (optional) fix → next round. The project decides which tools to test with:

- `e2e:` in `project.yaml`: the image, the permissions, and whether to write acceptance cases.
- The `e2e/` directory in the project pack: how to start the application, how to run the cases, how to write cases.

To connect a system with a different tech stack (e.g. a mobile app), change only these two places, not the flow.

The `e2e/` directory provides:

| File | Convention |
|---|---|
| `serve.sh` | Builds and starts the application, prints how to reach it (e.g. a URL). Can be called repeatedly: the same commit is built only once, and an application that is already running is reused |
| `run.sh` | Input: the environment variable `E2E_DIR` and the optional argument `--cases <dir>`. Without arguments it runs `tests/`. Output: `$E2E_DIR/report.md`, `$E2E_DIR/result.json` (same format as checks.sh, can add an `environment` list), and optionally `$E2E_DIR/screenshots/`. Once the report is written, `exit 0` |
| `tests/` | The fixed smoke test cases |
| `README.md` | For the agent that writes acceptance cases: which tools to use, how to write cases, how to name files, what limits the test environment has |

Runtime environment: the `e2e.image` image, with the permissions of `e2e.profile`. The current directory is the repository root, and the repository may be read-only. When `e2e.acceptance` is `true`, the image also needs Claude Code, because an agent writes the acceptance cases. The fix step (when `fix` is on) runs in the project's development image, which has no test tools. The results are in `docs/e2e/round-<N>/`.

`environment` in `result.json` lists problems with the test environment, e.g. the browser did not start, or the sandbox refused a system call. These cases were not tested. They are not code failures. When `run.sh` can tell the difference, it writes them in `environment`, not in `failed`. When `failed` is empty and there is only `environment`, the loop ends: no fix step runs, and no further round.

Some systems cannot run in the sandbox. The sandbox is a Linux container. The iOS simulator runs only on macOS. The Android emulator needs hardware acceleration, which the sandbox most likely does not allow. For such projects, `run.sh` hands the cases to an external device testing service and fetches the results. `e2e.profile` is switched to a profile with network access and the credentials for that service (an OpenShell provider). The flow stays the same.

For an example, see `blog/e2e/`, which uses Playwright and headless Chromium. In the OpenShell sandbox, Chromium needs `--no-zygote` and `--disable-dev-shm-usage`: the sandbox does not allow writing to `/dev/shm`, and does not allow the zygote to create a user namespace.

## risk-rules.yaml

```yaml
rules:
- paths: [.github/*, Gemfile*, ...]     # fnmatch: * also matches /
  approvers: 2
  reason: high-risk paths (deployment, dependencies...)
- added_with_text:                       # a new file whose content contains some text
    paths: [_posts/*]
    text: 'published: true'
  approvers: 1
- changed_with_text:                     # in a changed file, an added or removed line matches the regex
    paths: ["*.sql"]
    pattern: "(?i)\\b(DROP|ALTER|TRUNCATE)\\b"
  approvers: 2
default: 0
```

On sync, `risk-rules.yaml` is also saved as `risk-rules.json`, so scripts do not need to parse YAML.

development uses it: the `risk-rules` step takes the maximum of `default` and all matching rules. At least 1 person is needed when: the review still has unresolved Important issues, the checks failed, or some commits were not reviewed. The `classify-risk` agent judges independently once more (counting at most 2 people). The number of approvers is the larger of the two, capped at the size of the `approvers` list. blog has `default: 1`, so every change needs human approval. For each flow's specific rules, see the [Flow manual](workflows/README.md).

## Knowledge sources

A knowledge source is read-only knowledge mounted into a run. It is not part of the project pack snapshot. It is synced and versioned separately. The final authority for the fields is the `Source` model in `src/myorch/knowledge.py`.

Registering: fill in the form in the console under **Flows → Knowledge → + Register knowledge source**, or write the YAML by hand.
- The form first runs **Check**: it reports missing fields. For the folder type it also lists which files will be collected and any suspected secrets found by the scan.
- **Register** writes it into `myorch-knowledge` and makes one git commit. A registered knowledge source can be changed with **Edit** or removed with **Unregister** on its page. Unregistering does not delete existing snapshots.
- Knowledge sources in templates are read-only in the console.
- An Obsidian vault (a folder with `.obsidian/`) cannot be registered directly. Vaults contain secrets. Only register a folder you have curated.

Where they are registered (with the same name, the project wins, then the template, then global):

| Scope | File |
|---|---|
| Global | `myorch-knowledge/_sources/<name>.yaml`. If `projects` is set, only those projects can use it |
| Template | `_templates/<template>/sources/<name>.yaml` |
| Project | `<project>/sources/<name>.yaml` |

Three kinds:

| Kind | Required fields | Optional fields | How the agent gets it |
|---|---|---|---|
| `dir` | `path` (`${ENV}` and `~` allowed) | `include`, `exclude`, `index`, `projects`, `max_size`, `allow_findings` | The snapshot is mounted read-only at `/sandbox/workspace/knowledge/<name>/` |
| `git` | `repo` (`owner/name`), `provider` (an OpenShell provider that can clone the repository) | `ref` (default `main`), `image` (an image with git and python3; the project's image by default), plus the optional fields of `dir` | Same as above |
| `mcp` | `url` (`https://`), `provider`, `token_env` and `tools` must be set. With `auth: basic`, `email` is required | `auth` (`bearer` by default, or `basic`), `index`, `projects`, `token_read_only` | No directory is mounted. See "MCP allows only registered tools" below |

What the optional fields mean:

- `include`, `exclude`: globs, `**` crosses directories. When `include` is empty, all files are collected. `.git`, `.obsidian`, `.DS_Store`, `.trash` and symlinks are never collected. A `.myorchignore` file in the folder (one glob per line) also counts as `exclude`.
- `index`: the file the agent reads first. For MCP, how to use the tools. Written into the prompt.
- `max_size`: upper limit of the total size after filtering, default 50MB. It can be written like `50MB`. Sync fails when it is exceeded.
- `allow_findings`: secret scan results confirmed as false positives, written as `file:line`.

Where to put host folders (`dir`):

- The snapshot is taken in the process that starts the run. The CLI on the host can read any directory. Runs started from the console, Slack and scheduled jobs are started in containers and can only read folders under the **host knowledge root**.
- The knowledge root is `~/orca/kb` by default. Change it with `MYORCH_KB_ROOT` in `deploy/.env` (an absolute path). compose mounts it read-only into the console and the worker, at the same path as on the host. A registered `~` is also expanded to the host's home inside the containers.
- So folders you want to mount from the console go under `~/orca/kb/<name>/`, registered as `path: ~/orca/kb/<name>`. If a folder is elsewhere, the console's **Check** says why directly.
- Do not mount the whole home: it contains secrets such as `~/.ssh`.

Who gets them:

- The project's `knowledge` sets which ones are mounted by default. You can add or remove some when you start a run: on the command line with `myorch run … --kb a,-b` (`a` added, `-b` removed), in the console by ticking them in **New run**.
- By default they are mounted only in agent steps. An agent step with `knowledge: false` does not get them. A script step gets them only with `knowledge: true`. MCP knowledge sources are only given to agent steps.
- When a run starts, the mandate records the snapshot version of each knowledge source. If a knowledge source changes later, this run still sees the same content.

### MCP allows only registered tools

When an agent step starts, `/sandbox/bin/myorch-mcp` runs before claude:

1. It builds the `Authorization` header from the provider's placeholder variable. `bearer` is `Bearer <token>`. `basic` is the base64 of `email:<token>`, e.g. for an Atlassian personal API token. The gateway replaces the placeholder with the real token on the way out.
2. It asks each MCP server for its tool list. Every tool not in `tools` is disabled with `--disallowedTools`. When permission prompts are skipped, this is the only kind of rule claude enforces.
3. If the tool list cannot be read, the whole server is disabled, and no tools are allowed.
4. It generates the configuration and starts claude with `--mcp-config` and `--strict-mcp-config`. The repository's own `.mcp.json` is not loaded.

So `tools` is an allowlist, not just a hint. Register only tools that read. The token itself should also be read-only. `token_read_only: true` records that the registrant checked this; it is optional, and the source mounts without it. What blocks writes is the `tools` allowlist.

After registering, try it once with `myorch knowledge check <name> --project <project>`: it connects to the server in the same kind of sandbox as a step, checks the tools against the allowlist, and can call one tool once.

Jira example (Atlassian Rovo MCP; an organization admin must first enable API token access):

```yaml
kind: mcp
url: https://mcp.atlassian.com/v1/mcp
provider: atlassian-mcp            # OpenShell provider; the endpoint allows only mcp.atlassian.com
token_env: ATLASSIAN_TOKEN
auth: basic                        # personal API token; a service account's API key uses bearer, without email
email: me@example.com
tools: [getJiraIssue, searchJiraIssuesUsingJql, getConfluencePage, searchConfluenceUsingCql]
token_read_only: true
index: Read tickets with getJiraIssue. Find related tickets with searchJiraIssuesUsingJql.
```

Tool names are whatever the server returns. The startup log lists tools that are "registered but not on the server".

### Snapshots

Sync happens automatically when a run starts. There is no sync button.
- Folders are synced every time. When the content has not changed, the hash is the same and nothing is copied. When the path does not exist, suspected secrets are found, no files remain after filtering, or `max_size` is exceeded, the run does not start and the error is shown directly.
  The rules for suspected secrets are in `knowledge.SECRET_RULES`. development's takeaway uses them too. Three were added on 2026-11-09: `Authorization: Basic/Bearer` headers with literal credentials, `https://user:password@` addresses, and Azure DevOps PATs (52 lowercase base32 characters; pure hex hashes do not count). If a registered folder contains such content, the next sync reports it. Use `allow_findings` to let through what is really not a secret.
- git repositories are synced when they have never been synced or the latest snapshot is older than 1 hour. The clone happens in a one-off sandbox, and the token stays in the gateway. If the clone fails but a snapshot exists, the previous snapshot is used and a line is logged.
- MCP has no snapshot: the content changes, and the queries are recorded in the agent's session log.

You can also run `myorch knowledge sync <name>` by hand. Snapshots are stored on the volume in `knowledge/<name>/<hash>/`, with read-only files. The latest 5 versions are kept per knowledge source. The design and implementation notes are in [notes/2026-11-03-knowledge-mounts.md](notes/2026-11-03-knowledge-mounts.md).

## Onboarding a new project

1. **Create the project pack**: write `myorch-knowledge/<name>/project.yaml`. For a known tech stack, write `extends: <template>`. Fill in at least `repo`, `base`, `identity`, `providers.github`, `image`. Commit to myorch-knowledge.
2. **Sync**: `uv run myorch project sync <name>`.
3. **GitHub provider**: see [4. Setup · GitHub credentials for the project](04-setup.md#github-credentials-for-a-project). `myorch project github-profile <name>` generates a profile that allows only this repository. Create other credentials (private dependency feeds) the same way and map them to `providers.artifacts`.
4. **Development image**: use the template's `image/`, or write your own, then build it.
5. **Network policy** (when the build needs network access): allow the needed addresses in `policy.yaml`. Write `policy: policy.yaml` in `project.yaml`.
6. **onboard**: `uv run myorch run flows/onboard.yaml --project <name>`. Read the report and pick proposals.
7. **Flows**: no copying needed. Flows are shared by all projects. Tune the review strength and whether to have a takeaway in `flows.development` in `project.yaml`, and put project-specific requirements in `prompts/`. See "Project settings for the shared flows" and "Prompt slots".
8. Try development once with a small requirement, and confirm that the branch, the checks and the push are right.

## Changing a project pack

| What to change | How |
|---|---|
| Knowledge in `guide/` | Prefer running learn (backed by evidence, traceable). You can also edit by hand |
| `checks.sh`, `checks.env` | learn and onboard (only `checks.sh`) both make proposals. development's review loop also proposes changes when it finds problems in them. You can also edit by hand |
| `setup.sh` | onboard, or development's review loop. You can also edit by hand |
| `risk-rules.yaml`, `security.md` | Only onboard makes proposals. You can also edit by hand. Development runs cannot change them: a run cannot change its own approval rules |
| `project.yaml`, `prompts/`, `image/`, `providers/`, `sources/` | Flows never change them. `sources/` can be registered in the console |

Which files a flow can change is decided by the `scope` of `project-apply` (`EDITABLE` in `src/myorch/project.py`):

| scope | Files it can change | Used in |
|---|---|---|
| `learn` | `guide/`, `checks.sh`, `checks.env` | learn, postmortem |
| `onboard` | `guide/`, `checks.sh`, `setup.sh`, `risk-rules.yaml`, `security.md` | onboard |
| `fix` | `guide/`, `checks.sh`, `checks.env`, `setup.sh` | development's review loop (`pack-apply`) |

Proposals are written back only after you pick their numbers in the run to adopt them. When writing back, myorch commits in myorch-knowledge (`<scope>(<project>): ...`, with `myorch-run: <run_id>`) and then re-syncs the snapshot.

After editing by hand, commit in myorch-knowledge, then run `uv run myorch project sync <name>`. Without a sync, new runs still mount the old snapshot. Note that `project.yaml` is read in two places:
- `{{project.x}}` reads `project.yaml` and `prompts/` in myorch-knowledge directly when a run starts, so changes take effect at once.
- `project.json` in the snapshot (read by scripts, e.g. development reads `approvers` from it) is updated only after a sync.

The `docs/` and `memory/` that agents write in a run do not go back to the project pack automatically. For projects with `flows.development.takeaway: true` (mdm), development commits each run's task information and takeaway to `myorch/<date>-<run_id>/` in the project repository, which is merged into the base branch with the PR. learn reads them from the base branch and records the ones it has read in `myorch-data/learn/<project>/takeaways.json`, so it skips them next time.

### Snapshots

<!-- diagram: pack-lifecycle.html -->
Diagram file: [diagrams/pack-lifecycle.html](diagrams/pack-lifecycle.html).

The sandbox sees a snapshot, mounted read-only at `/sandbox/workspace/project/`. The snapshot differs from the project pack in these ways:

- There is no `project.yaml`. The merged configuration (the values of `{{project.x}}`) is in `project.json`. Scripts that need the project configuration read `project.json` or the environment variables `MYORCH_PROJECT_*`.
- With a template, the template's files are placed first, then overridden by the project's files. `risk-rules.yaml` and `security.md` are the template's and project's concatenated. There is also a `risk-rules.json`.
- There is no `image/`, `providers/`, `sources/` or `.git`.

Each sync computes a version from the file contents (`sha256:` plus a 16-character hash) and writes it to `projects/<name>/<hash>/` on the volume, with read-only files. When the content has not changed, the version is the same and nothing is rewritten. `projects/<name>/.version` records the latest version.

- A new run uses the latest version, and the mandate records the version. Every step of the run mounts this version, so a sync does not change a run in progress.
- Exception: after a run writes back to the project pack with `project-apply`, its later steps mount the new version. Other runs are not affected.
- The latest 3 versions are kept, plus versions written within the last 30 days. The latest version is always kept.
