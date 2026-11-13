# development

The development flow: from one requirement to a pushed branch. All projects share this one flow. In one session it writes the requirements, the technical design and the code changes. The change then goes through review, a risk verdict and human approval. Finally the approved commit is pushed to this run's branch. With the project setting `open_pr` (mdm), `deliver` then opens the pull request and the console shows its link; otherwise a person opens it. myorch never merges: a person decides on merging. Before approval the change does not leave the sandbox and nothing is pushed.

The differences between projects are all in the project pack; you do not pick them when you start a run (see [Differences by project](#differences-by-project)):

- How heavy review is: how many reviewers, at most how many rounds, whether there is a separate security review, what happens when the loop runs out of rounds (`flows.development` in `project.yaml`).
- Whether there is Jira: if so, `fetch-ticket` reads the ticket first and the ticket is the requirement. If not, the first message is the requirement.
- Whether to commit the task info and lessons (takeaway) to the repository's `myorch/` directory (`flows.development.takeaway`).
- Whether there is an `incident` input: when the platform had a problem, the failing run is attached for `develop` to look at (`flows.development.incident`).
- The project's own prompt rules (`prompts/develop.md`, `review.md`, `verify.md`, `fix.md`, `risk.md`). At expansion their text is appended verbatim to the prompt of the matching step.

```sh
uv run myorch run flows/development.yaml --project mdm -i ticket=18700
uv run myorch run flows/development.yaml --project mdm -i ticket=18700 -i task="Backend API only; the frontend gets its own ticket"
uv run myorch run flows/development.yaml --project blog -i task="Add a word count to article pages"
uv run myorch run flows/development.yaml --project myorch -i task="..." -i incident=development-1a2b3c4d
uv run myorch run flows/development.yaml --project mdm -i from_run=<previous run ID> -i task="Found in testing: bulk import does not validate"
uv run myorch run flows/development.yaml --project mdm -i ticket=18700 -i at=<the PR's merge-base> -i ticket_as_of=2026-10-01T08:00:00Z
```

Console: pick the project first, then the task **Development**. The form shows only the fields this project uses.

- Projects with Jira (mdm): fill in the ticket number (`MD-18700` or `18700`). The first message is an optional note: write the key points and constraints that are not in the ticket.
- Projects without Jira (blog, myorch): the first message is the requirement. It is required and can span several lines.
- myorch: when the platform had a problem in some run, put that run's ID in **Run with the problem**. When a local Claude Code session finds a platform problem, it also starts the run with the channel tool `start`, and the first message describes what was seen and a first analysis.

The first line of the message becomes `task`. The full text is written to `docs/external/request.md`.

<!-- flow-graph -->

The graph above is expanded for mdm, which has the most steps. blog and myorch have no `fetch-ticket` and none of the four takeaway steps. blog's review loop has only 1 reviewer and no `security`.

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `sync-master` | Clones or updates the repository, force-aligns with `origin/<base>`, initializes submodules. The first clone goes through the mirror repository in the project's shared directory; after that only increments are fetched ([`lib/sync-base.yaml`](lib/sync-base.yaml)) | `outputs.base_sha` |
| `checkout-branch` | Creates this run's branch. The branch name comes from the project pack's `branch` (mdm: `MD-<ticket number>-myorch-<run ID>`; blog and myorch: `myorch/<run ID>`). Runs the project's `setup.sh`. When continuing, it switches to the previous run's branch; when replaying, to `at`. See the two sections below | `outputs.branch`, `outputs.since` |
| `fetch-ticket` | Only when the project has Jira. Reads the ticket with the Jira REST API (read-only), writes it as `docs/external/ticket.md`, downloads screenshots. See "Reading the ticket" | `docs/external/ticket.md`, `docs/external/attachments/` |
| `develop` | Does it all in one session: writes the requirements document, writes the technical design, asks all questions that need a human decision at once, changes the code, runs the checks, writes the implementation record, commits once. See "develop" | `docs/requirements.md`, `docs/design.md`, `docs/implementation.md`, one commit |
| `review-fix` loop | `ci` (the project's `checks.sh`) → `review` (count per project) and `security` in parallel → `verify` checks the findings and computes `blocking` → `fix` (only when `blocking > 0`) → `pack-choose`, `pack-apply` (only when `fix` proposed project pack changes). The number of rounds and what happens when they run out depend on the project. See "The review loop" | `docs/ci/round-N.md`, `docs/review/round-N/` |
| `takeaway-trail` | Only with `flows.development.takeaway` (mdm). Host action: writes the run's history so far to `docs/takeaway/trail.json` | `trail.json` |
| `takeaway` | Same condition. Read-only agent: writes the task info `task.md` and the lessons `takeaway.md` | `docs/takeaway/task.md`, `takeaway.md` |
| `takeaway-redact` | Same condition. Host action: deletes lines that look like secrets from the two files | |
| `takeaway-commit` | Same condition. Commits the two files to the repository at `myorch/<date>-<run ID>/` | one commit |
| `risk-rules` | Computes the number of approvers by rules: changed paths and content, unresolved review findings, failed checks, commits that were not reviewed | `outputs.approvers`, `head_sha` and others |
| `classify-risk` | Reads the actual diff, judges independently how many approvers are needed, writes a risk note for the approvers | `docs/risk.md` |
| `human-review` | Human approval. For the number of approvers see "Risk and approval". Passes automatically when the number is 0 | |
| `deliver` | Checks that HEAD is the approved commit, pushes the branch, writes the delivery note; with `open_pr`, opens the pull request | `docs/delivery.md`, `outputs.delivery`, `outputs.pr_url` |

- When `develop` decides no code change is needed (`proceed` is false), all later steps are skipped and the run ends normally.
- For projects without takeaway, the four steps are removed at expansion, and `risk-rules` waits directly on `review-fix`.
- The flow sets `on_failed: ask` at the top level: when a step still fails after retries, it asks you.

## Where you step in

| When | Console | Command line |
|---|---|---|
| `develop` asks before changing code | Reply in the conversation, or click an option | `myorch reply <run> develop "answer"` |
| Adopting project pack changes | Reply with numbers, for example `1,3`, `all`, `none` | `myorch reply <run> pack-choose.r<N> "1,3"` (N is the round) |
| Review runs out of rounds with blocking findings left (projects with `on_exhausted: ask`) | Read the reflect proposal, reply 1, 2 or 3 | `myorch reply <run> review-fix 1` (1 runs one more round, 2 accepts the current result, 3 stops) |
| A step still fails after retries | Reply 1, 2, 3, or write a hint | `myorch reply <run> <step> 1` (1 starts over in a new session, 2 skips, 3 stops. Any other text is a hint, and the step continues in the same session) |
| Approval is needed | Read the diff, review verdict and risk verdict on the run's **Changes** tab, click **Approve**, **Request changes** or **Reject** | `myorch review <run>` opens the summary page; `myorch approve <run> human-review`, `myorch changes <run> human-review "comments"`, `myorch reject <run> human-review` |
| After the push | — | Without `open_pr`, open a PR on GitHub from the pushed branch (the delivery note can serve as the PR description). Review the PR and decide whether to merge. For the myorch project, a release follows the merge; see "myorch: after merging" |

When any of the four takeaway steps fails, you are not asked (`on_failed: skip`), and later steps are not blocked. Failed host actions do not ask either.

## Artifacts

All steps of a run share one workspace. The run's **Docs** tab in the console shows all documents and screenshots. On the command line, `myorch ws <run>` lists the files and `myorch ws <run> docs/risk.md` reads one.

```
observed/<run ID>/            the previous run when continuing; the failing run with incident (read-only)
docs/
├── external/
│   ├── request.md            original text of the first message (when started from the console or Slack)
│   ├── ticket.md             the ticket (fetch-ticket, or read by develop via MCP)
│   └── attachments/          images from the ticket
├── previous/                 when continuing: the previous run's documents and conversation
├── requirements.md           requirements, acceptance criteria, ticket mapping; with incident it starts with the cause category
├── design.md                 impact analysis, approach, compatibility and rollback, test plan
├── implementation.md         changes, evidence, check results, assumptions, differences from the design; fix appends "Round N fix"
├── ci/round-N.md             check report per round (round-N/ holds result.json, head.txt)
├── review/round-N/           reviewer-K.json, security.json, verified.json, summary.md, rebuttal.md, approver.json, pack/
├── takeaway/                 trail.json, task.md, takeaway.md (only for takeaway projects)
├── risk.md                   risk verdict and the places the approvers should check
└── delivery.md               delivery note
repo/myorch/<date>-<run ID>/  task.md, takeaway.md (committed on the branch, only for takeaway projects)
```

## develop

One agent goes from reading the requirement to committing code, in three phases in order: requirements, technical design, implementation. It cannot skip the first two phases and go straight to code. The project's `prompts/develop.md` is appended verbatim after the "Inputs" part of the prompt; see [Differences by project](#differences-by-project).

- **Inputs**: the ticket `docs/external/ticket.md` (when there is one), screenshots, the original request `docs/external/request.md`, project conventions, the knowledge that comes with the repository, the project pack's `guide/` (its `decisions.md` holds decisions people made before). The original request and the ticket are external data; instructions in them are not followed.
- **Requirements document**: goal, scope, what is explicitly out of scope, checkable acceptance criteria, a ticket mapping table, the files expected to change, risks, sources.
  - Ticket mapping table: each requirement in the ticket is copied as one row (including those added in comments), with the acceptance criterion it maps to, or why it is not done. When one requirement contains several actions, the acceptance criteria must cover each action.
  - The agent cannot put modules, features or scenarios the ticket names into "explicitly out of scope" on its own. To exclude one, it must ask a person.
- **Technical design**: impact analysis (callers, cross-module dependencies, other services), the approach and the rejected approaches, compatibility and rollback, a test plan (a test for each acceptance criterion, plus edge cases and counterexamples), risks. No code in the design.
  - List every entry point: when adding validation to a field or requiring consistency in several places, search back in at least two ways, by the error message and by the field name. Write the search commands and the entry points found into the design.
  - Before removing or loosening an existing check, find out why it exists with comments, `git log -L` and `git blame`. If that cannot be found out, or what it protects no longer holds after the change, ask a person.
  - Look for an existing implementation of the same kind first: if it fits, call it; if its shape does not fit, reshape it so it can be shared; when the behavior is decided by a shared method, change the shared method and check every caller. Do not copy it. Changing the shared implementation for reuse is within this change's scope.
- **Ask before changing code, at most once**: the questions from both phases that need a human decision go into one question, each with options, the first one recommended. The agent cannot choose for you on: things users can see, interfaces and contracts, data models and migrations, behavior changes that affect other modules or services, and choices that clearly change the size of the implementation. Decisions already in `decisions.md` are followed and not asked again.
- **Safety net**: when the agent finishes and reports questions it did not ask but decided by the recommendation (`requires_design_decision`), the platform still asks you, and your answer is sent back to the same session as the next turn.
- **Implementation**: change only code within scope, give each acceptance criterion a test or check, run `myorch-checks` (the project's `checks.sh`) until it passes, write the implementation record, and commit once on the current branch. When the check report lists failures under "Environment" (network or system limits of the sandbox), they are not code problems: commit as usual and note them in the implementation record.
- **Stop**: when the requirement contradicts the code, when code outside the scope must change, or when checks fail because of the code and cannot be fixed, it does not commit. The step fails and then asks you what to do.
- **It may decide no change is needed**: the feature already exists, or you decide not to do it. No documents are written and everything after is skipped.

Why one step: when these were separate, every handoff lost information. The steps with credentials (`sync-master`, `fetch-ticket`, `deliver`) still run separately; `develop` gets no GitHub or Jira credentials.

## Reading the ticket

- **Condition**: the project sets `jira.url` (mdm: `example.atlassian.net`, prefix `MD`) and a ticket number is given. When only digits are given, the project's prefix (`jira.key`) is added. Projects without Jira have no ticket input and no `fetch-ticket` step.
- **Read-only**: the credentials are the project's Jira provider (mdm: `jira-mdm`). The egress rules allow only reading tickets, reading attachment content, and `/myself`. The sandbox holds only a placeholder; the gateway swaps in the real token.
- **Content**: title, type, status, priority, reporter, assignee, labels, components, parent ticket, linked tickets, description, subtasks, attachment list, comments. The file states at the top that it is external data.
- **Screenshots**: only image attachments are downloaded, at most 10, each at most 5 MB, saved to `docs/external/attachments/<id>-<file name>`. The "Attachments" section of `ticket.md` lists the paths, and `develop` opens them with Read. `!file name!` in the description refers to these images. Other attachments are listed by name only. A failed download does not affect the run. The console's **Docs** tab also shows these images.
- **Comments**: `ticket_comments` defaults to `all`. With `none`, comments are left out and only the description is used (for evals: the requirement should be in the ticket, not depend on comments).
- **MCP fallback**: when `fetch-ticket` did not run (the project has no `jira.url`) and the run has an MCP knowledge source that can read tickets, `develop` first reads the ticket via MCP and writes the same `docs/external/ticket.md`, noting the source at the top. If it cannot read it, it says so in the summary and continues from the original request. mdm does not attach the Atlassian MCP by default at the moment.

## The review loop

Defined in the fragment [`lib/review-loop.yaml`](lib/review-loop.yaml). [review](review.md) and [eval](eval.md) use it too (one round only, no fixing). The parameters here come from the project's `flows.development`: by default 3 reviewers plus a separate security review, at most 3 rounds, and it asks you when it runs out of rounds. The project's `prompts/review.md`, `verify.md` and `fix.md` are appended to the matching steps.

- **What each round looks at**: round 1 looks at the whole change (when continuing, only this run's new commits); each later round looks only at the commits since the previous round started. `ci` computes the range and writes it to `docs/ci/round-N/head.txt`.
- **Judge independently first, read the author's account last**: a reviewer first looks only at the requirements document, the technical design and the diff, and finds problems on its own. Only then does it read the implementation record and check that the tests and checks listed there really exist and the assumptions hold. A reason given in the implementation record is no excuse to let a problem pass.
- **Check the requirements document against the original ticket**: the requirements document is also written by the author. Leaving out, narrowing or rewriting a ticket requirement, a ticket mapping table that covers only some of the actions, or moving something the ticket names out of scope on its own, all count as Important.
- **Look in the repository**: three kinds of problems that the diff alone does not show always count as Important:
  - Not reusing an existing implementation of the same kind, or copying a branch of a shared method instead of changing it.
  - Missed entry points: other places that read or write the same field or the same error message were not changed, and the design does not say why.
  - An existing check was removed or loosened, breaking what it protected, and the requirements document does not say a person decided this.
- **Division of work**: reviewer 1 focuses on correctness and edge cases, reviewer 2 on acceptance criteria and tests, reviewer 3 on design, scope and reuse. Everyone checks for missed entry points and loosened checks. Projects with only 1 reviewer (blog) have only reviewer 1.
- **Reading earlier steps' history**: `fix` and `reflect` can use `/sandbox/bin/myorch-steps` to read, in parts, the manifests and logs of this run's earlier steps (for example why develop did something). The reviewer and verify prompts do not mention it: they must judge independently first. Earlier steps' records (`.myorch/`, other steps' `.claude/`) are read-only for every step.
- **Rule markers**: rules like "look in the repository", "judge independently first" and "at most 5 Nits" above were added because the model made those mistakes before. They are marked in the flow file with `[[rule:<rule>/<location>]]`, and the reason for each is recorded in [`rules/registry.yaml`](rules/registry.yaml). At expansion the marker is removed and the text stays. Only a replay with `--set flows.ablate=<rule>` removes the whole rule, to evaluate whether the rule is still needed.
- **verify**: merges duplicates, opens the code for each finding to confirm it, also confirms findings only one reviewer reported, and deletes those that do not hold. Deletes pure style comments and keeps at most 5 Nits. Each failed check counts as one Important; environment failures do not. Findings from the previous round are marked fixed, open or rebutted. `blocking` is the number of unresolved Important findings; the loop ends when it is 0.
- **fix**: fixes only Important findings, changes all places of one problem together, and leaves Nits alone. When it thinks a finding does not hold, or the fix needs a human decision, it does not change the code and writes it in `rebuttal.md` instead (those needing a human decision start with "Needs a human decision:"); the next round's verify judges it. When the cause is in the project pack, it proposes a project pack change; see the next section. After fixing it runs the checks, appends "Round N fix" to the end of the implementation record, and commits once.
- **Running out of rounds**: with `on_exhausted: ask` (the default), `reflect` reads all rounds and judges whether it is an isolated problem, a wrong direction, something needing a human decision, or reviewers being wrong. It gives a proposal and then asks you: run one more round, accept the current result, or stop. With `accept` (blog) the flow goes straight on. The last round's fixes were never reviewed, and `risk-rules` lists those commits.
- **The approver requests changes**: the comments are written into the next round's `approver.json`. Each one counts as Important, and verify will not delete them.

## Project pack changes (pack-choose, pack-apply)

Sometimes a blocking problem lies in the project pack, for example a mistake in `checks.sh`. The project pack is read-only in the sandbox (`project/`), so `fix` cannot change it. In that case:

1. `fix` does not change `project/` and does not write a rebuttal for it. It copies `project/` to `/tmp`, changes the copy, runs the relevant checks once with the copy, and writes the proposals to `docs/review/round-N/pack/proposals.json` (for machines) and `proposals.md` (for people, including the verification result).
2. `pack-choose` lists the proposals and asks which ones you adopt.
3. `pack-apply` writes the adopted changes back to `myorch-knowledge` on the host, commits only those files, and syncs the project snapshot again. The original text of each change must appear exactly once in the file; otherwise nothing is written and the step fails.
4. The next round of this run mounts the new snapshot and checks with the new project pack. Other runs are not affected. When the write-back happened in the last round, the reflect step proposes one more round.

Only files under `guide/`, `checks.sh`, `checks.env` and `setup.sh` can be changed (`EDITABLE["fix"]` in `project.py`). `risk-rules.yaml` and `security.md` cannot: a run must not change how its own changes are approved. To change other files, `fix` writes "Needs a human decision:".

## Task info and takeaway

Only when the project sets `flows.development.takeaway: true` (currently only mdm): putting a `myorch/` directory in the repository needs the project's consent. After the review loop and before the risk verdict, four steps commit this run's task info and lessons to the repository.

- **Committed on the branch**: `takeaway-commit` copies `task.md` and `takeaway.md` to `repo/myorch/<date>-<run ID>/` (the date is the day the run started), commits only this directory, with the fixed commit message `Add myorch task and takeaway for <run ID>`. When the content has not changed, it does not commit again. It is visible at approval, is in the PR after the push, and reaches the base branch after merging; [learn](learn.md) reads it from there.
- **Not blocking**: all four steps have `on_failed: skip`. A failure records a failure event, counts as skipped, and the flow goes on. Each step runs only when the previous one produced output, so stale or half-written files are never committed (`takeaway-trail` empties both files first).
- **trail**: each step's status, summary, failure reason and retry count; questions and answers; each review round's blocking count and check results; branch, inputs, myorch commit, project pack version. Lines that look like secrets are deleted before writing.
- **What takeaway writes**: `task.md` holds only facts (ticket, run, inputs, requirements summary, questions and answers, each review round and its checks, the result). `takeaway.md` holds the problems encountered, root causes, how they were solved, the missing knowledge, and suggestions for the flow or platform. Each item is tagged with a category (project knowledge, flow, platform, environment) and gives evidence. "Missing knowledge" holds only project knowledge that would help with a different requirement too, not the answer to this ticket. The two files together are at most 200 lines. The agent has no knowledge sources attached and is read-only.
- **Reading the history**: besides `trail.json`, `takeaway` can use `/sandbox/bin/myorch-steps` to read each step's logs in parts, to find where things first went wrong.
- **No secrets**: the two files go into the company repository. `takeaway-redact` deletes lines that look like secrets by `knowledge.SECRET_RULES` (the same rules as the knowledge source scan). When both files end up empty it fails, so the commit does not run.
- **How risk-rules sees this commit**: it comes after the last review round, but it does not count as a "commit that was not reviewed": a commit with a single parent that changes only `.md` files under `myorch/` is not counted. Merge commits are always counted. `risk-rules` takes `head_sha` after this commit, so what you approve and what is pushed is the commit with these two files.
- **When it is not written**: replays (`at`) do not write it; otherwise the eval case's answer would get into knowledge. It is also not written when `develop` decides no change is needed. A run that fails before approval cannot be pushed, so its lessons are not in the repository.

## Risk and approval

Risk is decided by the change, not by the author. Two classifiers back each other up: rules may miss semantic risk, the model may miss path-based risk, and the stricter of the two wins.

**risk-rules** (deterministic script):

- The rules come from the project pack's `risk-rules.yaml` (the template's rules plus the project's own). Of the rules that match, the strictest applies. Rules can match by path, by text in new files, or by a regex on changed lines. When no rule matches, `default` applies.
- At least 1 approver in any of these cases: the last review round still has unresolved Important findings; the last round's checks failed; there are new commits after the last review round (for example after accepting when the loop ran out of rounds, so nobody looked at the last round's fixes). The takeaway commit does not count; see the previous section.
- Outputs `approvers` (the number the rules require) and `available` (the number of approvers registered for the project).

**classify-risk** (agent, read-only): reads the actual diff, the requirements document, the assumptions in the implementation record, the last review round and checks, and the commits that were not reviewed, and decides 0, 1 or 2 approvers. Any one of these means 2: changes to interfaces or data, changes to build or deployment, anything security-related, hard to undo, affects downstream. It writes `docs/risk.md`: the verdict, the impact, unresolved problems, and 3 to 5 places the approvers should check. The project's `prompts/risk.md` is appended to the end of the prompt (myorch uses it to require that the impact be written in terms of the platform's boundaries, unfinished runs, and deployment).

Number of approvers for **human-review**:

```
min(approvers registered for the project, max(risk-rules count, min(classify-risk count, 2)))
```

- All three projects currently register only one approver, so the number is at most 1.
  - mdm: the core-ng template's default is 1. Database migrations, interface modules, build and dependencies, CI and deployment, environment config, entity field annotations and destructive SQL need 2. mdm's protos submodule needs 2 and spec documents need 1. So every run needs exactly 1.
  - blog: `default: 1`; every run needs 1.
  - myorch: `default: 0`; the platform's boundaries (code for sandbox, credentials, profiles, deployment, scheduling and approval) need 2. When both judgments give 0 (for example only docs changed), approval passes automatically and `deliver` pushes the branch right away. Only the branch is pushed; merging and deployment are done by a person.
- The person who started the run must be one of the approvers (`require_initiator`). An agent's approval does not count.
- Approval is bound to a commit: `head_sha` is recorded when approval opens, and `deliver` checks it before pushing.
- Request changes: back to `review-fix`, at most 2 more rounds. Then takeaway, the risk verdict and approval run again from the start.
- After 24 hours with no action, one reminder is sent. After 7 days with no action, the step fails ("approval timed out") and nothing is pushed.
- If any approver rejects, the run ends.

## Delivery

Defined in [`lib/deliver.yaml`](lib/deliver.yaml).

- It refuses to push when the current branch is the base branch. It refuses to push when HEAD is not the approved commit; the run must be redone and approved again.
- Pushes the branch (normal push, no force push). Does not call the GitHub API and does not open a PR.
- Writes `docs/delivery.md`: title and ticket, what changed (taken from "Changes" in the implementation record), how it was verified (check results, acceptance criteria table), number of review rounds and unresolved problems, commits that were not reviewed, the risk verdict and the places to check, how to undo.
- `outputs.delivery` carries the branch, base, commit, title, ticket, the delivery note and the repository's PR template. The `run_finished` event carries it, and the console and Slack use it to tell you which branch was pushed.
- With `flows.development.open_pr` (mdm): after the push, opens the pull request with `gh api` (title: the delivery title; body: the delivery note, then the repository's PR template, then a hidden `<!-- myorch-pr run=… -->` marker; head: the run's branch; base: the project's base). If the branch already has an open PR (a run continued with `from_run`), that PR is reused and the push updates it. The link is output as `pr_url`: the console shows "PR opened" in the run's conversation, the `pr_opened` and `run_finished` events carry it, and the run list has it. If opening the PR fails, the step fails after the push; "Re-run failed steps" pushes again (nothing changes) and retries the PR. Without `open_pr`, you open the PR.

## Continue (from_run)

The general procedure is in [Continue on the same branch](../docs/workflows/README.md#continue-on-the-same-branch). In this flow:

- `checkout-branch` mounts the previous run's workspace read-only and starts from its last commit. If someone added commits to the remote branch of the same name, it starts from the latest remote commit. If the two have diverged, it stops and a person must sort it out first.
- Files copied to `docs/previous/`: `requirements.md`, `design.md`, `implementation.md`, `risk.md`, `delivery.md`, `external-request.md` (the previous original request), the last review round's `review-summary.md`, and `RUN` with the previous run ID. Earlier runs are in `earlier/`. `conversation.md` is written by the host: what people said to the previous run.
- `develop`'s requirements document states the full requirement, with a separate paragraph "This change" under "Goal". The previous acceptance criteria still hold unless this request changes them. The design is changed on top of the previous one. The code is changed on top of the existing commits, not redone. Human decisions in `conversation.md` take precedence over assumptions in the previous documents.
- `checkout-branch` outputs `since` (the commit at the start of this run). Round 1 of the review loop looks only at commits after `since`; checks still run on the files the whole branch changed against base. The risk verdict looks at the whole branch.
- It pushes to the same branch, so a PR that is already open updates automatically. If no ticket number or `incident` is given, the previous ones are reused.
- Cannot be combined with `at`.

## Replay (for evals)

Used to redo an already merged ticket on the code as it was then, and compare with the merged PR. The form shows `at`, `ticket_as_of` and `ticket_comments` only for projects with Jira (on the command line, `-i at=` can be passed for any project). Leave them empty normally.

- `at` is the PR's merge-base. `checkout-branch` creates the branch from this commit, then deletes the `origin` remote, all other refs, `FETCH_HEAD`, the reflog and borrowed objects, runs gc, and does the same for submodules. Later commits would leak the answer, so only history reachable from `at` stays in the repository. Finally it points `origin/<base>` at `at`, so every later diff uses it as the base.
- `ticket_as_of` is the moment work on the ticket started (ISO time). `fetch-ticket` uses the ticket's changelog to roll title, description, status, priority and assignee back to that moment. It keeps only earlier comments, attachments and linked tickets, and leaves out subtasks (subtasks may contain the solution).
- A replay never asks for approval and never pushes: the conditions of `human-review` and `deliver` include `not inputs.at`. Deleting `origin` is a second safeguard. No takeaway is written either. The run ends after `classify-risk`.
- Cannot be combined with `from_run`; `checkout-branch` fails right away.
- To find out whether the ticket description alone is enough, add `-i ticket_comments=none`.
- **Comparisons**: a replay can change this run's flow settings with `--set`, for example `--set flows.development.reviewers=1` or `--set flows.ablate=nit-cap` (removes one marked rule; rule names are in [`rules/registry.yaml`](rules/registry.yaml)). Only existing settings under `flows.` can be changed, and only in replays. What was changed is recorded in the mandate, and the run gets a `set:…` tag so variants can be grouped. `myorch expand --set …` shows the changed prompt first.

## Differences by project

There is only this one flow. Before 2026-11-10 each project had its own copy (`ck-development`, `blog-development`, `mo-development`). The same improvement had to be made three times, and the three copies soon diverged, so they were merged back into one ([docs/notes/2026-11-10-one-set-of-flows.md](../docs/notes/2026-11-10-one-set-of-flows.md)). The differences between projects live in the project pack `myorch-knowledge/<project>/` and take effect when the flow is expanded:

- **`flows.development` in `project.yaml`**: `reviewers` (default 3), `max_rounds` (3), `security` (true), `on_exhausted` (`ask`), `takeaway` (false), `incident` (false). A template can set them; the project overrides the template.
- **`prompts/<slot>.md`**: the slots are `develop`, `review`, `verify`, `fix`, `risk`. The template's comes first, then the project's, appended verbatim to the prompt of the matching step. The mandate's snapshot shows the assembled prompt. A file name that is not one of these slots is an error.
- **`if:` in the flow**: drops steps and inputs per project. `fetch-ticket`, the ticket input and the replay inputs depend on `project.jira.url`; the four takeaway steps on `flows.development.takeaway`; the `incident` input on `flows.development.incident`. Dropped inputs do not show in the form and keep their defaults.

| | mdm | blog | myorch |
|---|---|---|---|
| Jira | Yes: ticket required, `fetch-ticket`, replay inputs | No: the first message is the requirement | No: the first message is the requirement |
| review | Default: 3 reviewers, security, 3 rounds, asks you when out of rounds | 1 reviewer, no security, 1 round, accepts when out of rounds | Default |
| takeaway | Yes | No | No |
| `incident` | No | No | Yes |
| prompts | From the core-ng template: `develop.md` (Gradle dependencies, `*-interface` contract comments), `review.md` (checks for `*-interface`) | `develop.md` (Jekyll: keep documents short, writing rules, what to ask a person), `review.md` (build, links, writing rules, accidental publishing) | `develop.md` (incident cause analysis, platform matters the design must cover, "What to do after merging"), `risk.md` |
| Approval | 1 person | Always 1 person (`default: 1` in `risk-rules.yaml`) | 0 or 1 person, by risk |

To change how one project works: change its project pack (`flows.development`, `prompts/`, `risk-rules.yaml`), then run `uv run myorch project sync <project>`. Changing `flows/development.yaml` or a fragment affects all projects.

### blog

Personal blog (`leweii/leweii.github.io`). Changes are small, so review is light: 1 reviewer looks once, blocking findings are fixed once and not reviewed again, and the loop accepts when it runs out of rounds. Nobody reviewed the commits after the fix; `risk-rules` lists them, and the "review" section of the delivery note lists the problems that were not fixed. The branch is `myorch/<run ID>`; it never pushes to master: pushing to master puts the blog live.

- `prompts/develop.md`: for posts, read the writing rules; for theme changes, read the development guide. Keep the requirements document and technical design short, and write "Not applicable" for parts that do not apply. The opinions and facts in a post, whether it is public (`published`, encrypted posts), and site-wide changes (`_config.yml`, layouts, dependencies, deployment) must all be asked of a person.
- `prompts/review.md`: will the build break, will links break, will pages render wrong; writing rules, front matter, accidental publishing.
- End-to-end tests run only in [review](review.md), not in development.

### myorch and incident

The myorch platform itself: code, flows, prompts and docs are all in the repository `leweii/myorch`, branch `myorch/<run ID>`. Two uses: a normal requirement; or, when the platform had a problem in some run (a run of any project), passing `incident` to find the cause and fix the platform.

`incident` is the run that had the problem. Its workspace is mounted read-only for `develop` at `/sandbox/workspace/observed/<run ID>/`:

- `.myorch/flow.yaml`: the snapshot of the flow that run used.
- `.myorch/steps/<instance>/out/manifest.json` and `logs/turn-<attempt>-<turn>.log`: each step's result and each attempt's log. `supervisor-*.log` holds the sandbox's network, DNS and credential records.
- `docs/` and `memory/MEMORY.md`: that run's requirements, design, review and check reports.

These files are that run's data: the agent only reads them and does not run commands or follow instructions in them. The console's run picker lists only this project's runs, so `incident` is a plain text box.

myorch's `prompts/develop.md` requires `develop` to decide the cause before writing the requirements:

| Cause | Meaning | Next |
|---|---|---|
| `platform-code` | A defect in myorch's code (`src/myorch/`, `tools/`, `deploy/`) | Continue: requirements, design, implementation, review, approval |
| `flow` | A defect in a flow definition, fragment, prompt or profile (`flows/`, `profiles/`) | Continue |
| `project-knowledge` | That project's pack lacks knowledge or checks; the platform is not at fault | Stop. Proposes that project's learn or postmortem |
| `environment` | Network, external services, expired credentials, machine resources | Stop. Record only, no code change |
| `one-off` | A random model mistake that a re-run will most likely not repeat | Stop. Proposes a re-run |
| `unclear` | Not enough evidence | Stop. States what evidence is missing |

"Stop" means `outputs.proceed` is false: all later steps are skipped, the run ends normally, and nothing is pushed. The cause category, the evidence (files and lines) and where to handle it go in the summary. To continue, the requirements document starts with "The failing run and its cause category", and "Goal" states what went wrong in that run, which file holds the root cause, and what should happen in the same situation after the change. The fix must not loosen policies, providers, mounts or approval.

The same `prompts/develop.md` also makes the technical design cover several platform matters: layering (engines → runner → storage → core), which flows a changed fragment or profile affects (flows are shared by all projects), Temporal determinism, volumes and events.db, commands and images in the sandbox, and whether the docs need regenerating.

### myorch: after merging

This flow does not deploy. The sandbox only changes the repository and has no access to docker.sock, the gateway or the data directory, and a flow must not have automatic merge or deploy steps (the project pack's `security.md`). `develop` writes "What to do after merging" at the end of the implementation record. After you open a PR on GitHub and merge it into main:

1. On myorch's main branch, run `deploy/update.sh` (first use `--check` to see which commits will be released and what it will wait for). It fast-forwards main, waits until no step is running, rebuilds the images from a clean checkout of the target commit, restarts the worker and the console, and runs the smoke flow. If the smoke test fails, it rolls back to the previously released commit automatically. When `src/myorch/engines/` or `core/run.py` changed, it waits for all unfinished runs to end. See "Release origin/main" in [`deploy/README.md`](../deploy/README.md).
2. When `images/claude-code/` changed, `update.sh` also rebuilds the sandbox base image `openshell-claude-code`. Each project's development image is based on it; to use the new base image, the project's image must also be rebuilt (the project pack's `image/build.sh`). When `pyproject.toml` or `uv.lock` changed, rebuild the myorch project's development image (in the myorch-knowledge directory, run `myorch/image/build.sh ../myorch`); only then do later development runs of the myorch project use the new dependencies.
3. When `tools/` changed (the helper commands in the sandbox, such as `myorch-checks` and `myorch-ask`), they are installed into the volume by `myorch init` (the `init` service in compose). Make sure `myorch init` has been run again; otherwise the sandbox still has the old versions.
