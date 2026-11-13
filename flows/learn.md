# learn

Turns a project's past runs into knowledge, so that the next run knows it from the start. All projects share this one flow. It turns various signals into proposals for changing the project pack. You pick which ones to adopt, and it writes them back to `myorch-knowledge/<project>/`. When the knowledge gets more accurate, every later development and review run benefits.

It only changes the local `myorch-knowledge`: `guide/`, `checks.sh`, `checks.env`. It does not push, does not touch the project repository and does not change flows (proposals about flows are only written to a file). The difference from [postmortem](postmortem.md): postmortem works backwards from one problem that slipped through; learn looks for recurring patterns across a batch of runs.

```sh
uv run myorch run flows/learn.yaml --project mdm                                                 # pick runs automatically
uv run myorch run flows/learn.yaml --project mdm -i runs=development-b947d694,review-5d5a21ec     # pick runs by hand
uv run myorch schedule add weekly-learn-mdm --flow learn --project mdm --cron '0 9 * * 1'         # every Monday at 9:00
```

Console: choose the project first, then the task **Learn**. **Runs to learn from** can stay empty. The first message is not needed.

<!-- flow-graph -->

## Which runs it learns from

When `runs` is not given, the client that starts the run picks them (the same for the command line, the console, Slack and schedules):

- development, review and postmortem runs of this project that started after the last **completed** learn;
- only runs that have ended (succeeded or failed); cancelled runs do not count;
- at most 20, the newest. `observe_runs` mounts at most 20 workspaces.

The command line lists the chosen runs before it starts. When there is no new run at all, the run does not start and reports "no new finished development / review runs". `uv run myorch list --project <project>` shows which runs exist. To pick by hand, use `-i runs=<id>,<id>`.

A failed or timed-out learn does not count as completed. The next learn picks the same runs again.

## Which signals it reads

| Signal | Source | How it gets into the run |
|---|---|---|
| review findings and what happened to them later | Each run's `docs/review/round-*/`, the PR's state | collect reads the read-only mounted `observed/<run_id>/` and queries GitHub for the PR |
| Questions people answered, requested changes, rejections, failed steps, re-runs, which run was continued from | The event log on the host | Written to `docs/learn/trail.json` by the client at start ([`trail.py`](../src/myorch/trail.py)). The sandbox cannot read the event log |
| CI failures | `docs/ci/round-*/result.json` | collect |
| Assumptions made during implementation, differences from the requirements and the design | The matching sections of `docs/implementation.md` | collect |
| Why a branch was continued | The request of the continuing run (`docs/external/request.md`) | collect |
| Postmortem conclusions and follow-up proposals | `docs/postmortem/` of postmortem runs | collect |
| Task info and takeaways written by development runs | `myorch/<date>-<run_id>/` on the project repository's base branch | collect reads them from the repository. Which ones were already learned is written to `docs/learn/takeaways-learned.json` at start |

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `collect` | Gathers the signals in the table above, works out what happened to each review finding later, and reads the takeaways in the repository that have not been learned yet. The script only reads from GitHub and does not run repository code | `docs/learn/findings.json`, `outcomes.md`, `signals.json`, `signals.md`, `takeaways.json`, `takeaways/` |
| `analyze` | Looks for patterns and makes proposals, each with evidence. Only writes proposal files and changes nothing | `docs/learn/proposals.json`, `proposals.md`, `follow-ups.md`, `flow-proposals.md` |
| `choose` | Lists the proposals for you and waits for you to reply with numbers. Skipped when there are no proposals | `docs/learn/accepted.json` |
| `apply` | Host action `project-apply`: validates, writes back to the project pack, commits in `myorch-knowledge`, re-syncs the snapshot. Skipped when nothing was adopted | A commit in `myorch-knowledge` |
| `takeaways-learned` | Host action: marks the takeaways read in this run as learned. Runs no matter how many proposals were adopted; skipped when no takeaways were read this time | `myorch-data/learn/<project>/takeaways.json` |

### collect: what happened to the findings

Findings already marked fixed do not count. Each of the others is checked in the order below, and the first that holds wins:

| Outcome | Meaning |
|---|---|
| `fixed` | In a later round, a finding in the same file is marked fixed |
| `rebutted` | The first 12 characters of the title appear in some round's `rebuttal.md` |
| `merged_unchanged` / `closed` / `open` | Review runs only: the reviewed PR was merged, was closed, is still open |
| `not_required` | Nit or Pre-existing; it never blocked |
| `unknown` | None of the above |

myorch no longer posts comments on PRs, so there is no "someone replied to the comment" outcome. Outcomes come only from the runs' own records and the PR state.

### collect: takeaways in the repository

- When the project sets `flows.development.takeaway` (currently only mdm), [development](development.md) commits `task.md` and `takeaway.md` with each change to `myorch/<date>-<run_id>/`. They reach the base branch only after the PR is merged, so only merged ones can be read. Other project repositories have no such directory, and this kind of signal is empty.
- When the workspace has no repository, collect makes a shallow clone without checking out files and reads them one by one with `git show`.
- Learned ones are skipped, and reading starts from the oldest. At most 30 entries and 300 KB per run (the first one is always read); the rest wait for the next run. The digest says how many are left.
- An entry whose `takeaway.md` cannot be read (network problem, empty file) is not read and not marked learned; the next run tries again. After 3 unreadable entries in a row it stops. Directories whose name is not `<date>-<run_id>` are skipped.
- When the repository cannot be read, it records only a fixed sentence and the git exit code, without the command line or git's output. learn carries on.
- Which ones were learned is recorded only on the local machine in `myorch-data/learn/<project>/takeaways.json` ([`takeaways.py`](../src/myorch/takeaways.py)). Nothing is written to the company repository or to `myorch-knowledge`.

### analyze: which kinds of knowledge it looks for

At most 8 proposals. With too little evidence it proposes fewer or none. It does not repeat what the knowledge and the repository's own docs already say. The wording is short and follows the style of the existing files.

| Type | What it looks for | Where it writes |
|---|---|---|
| `record-decision` | Decisions a person made while answering during requirements analysis or design that apply to all similar questions later | `guide/decisions.md` (created when missing): the situation, what to do, why, the source. Later similar questions follow it without asking a person |
| `fill-gap` | Causes of rework: continuations, postmortem conclusions, implementations that deviated from the requirements or design, assumptions that had to be made | Guides for development and design. The question is "what could the knowledge have said in advance so that the first attempt was right" |
| `add-rule` | The same kind of problem recurring (in at least 2 runs, or in 1 with high impact) | Rules to avoid during implementation, or `review-guide.md` |
| `relax-rule` | Comments that were rebutted repeatedly, or merged without changes | Relax `review-guide.md` |
| `add-check` | Recurring problems a script can detect, or CI checks that fail repeatedly | `checks.sh` or `checks.env`. Only deterministic checks; checks that may give false positives only warn. When it is not sure it can write the check correctly, it goes into `follow-ups.md` |

Two more files are never applied: `follow-ups.md` holds proposals for changing the project repository itself (lint, CI, tests), which need a separate development run; `flow-proposals.md` holds proposals for myorch flows (step order, when to ask questions, steps that always fail or are not needed).

Takeaways are treated as signals and checked like other evidence: only project knowledge that generalises to later work is adopted, not what holds only for one ticket. Evidence is written as `<run_id>: takeaway`. Proposals about flows, the platform or the environment go into `flow-proposals.md`.

Each proposal's change is written as an exact replacement (`edits`: `old` copied verbatim from the current file and occurring exactly once in it) or an append (`append`). No diffs, because diffs written by agents often have wrong line numbers.

## Where you step in

| When | Console / Slack | Command line |
|---|---|---|
| `choose`: pick which to adopt | Reply with numbers in the conversation, or click `all` or `none` | `uv run myorch reply <run> choose "1,3"` |

- The question has one line per proposal, with its type and the number of pieces of evidence. The details are in `docs/learn/proposals.md`: the run's **Docs** tab in the console, or `uv run myorch ws <run> docs/learn/proposals.md`.
- Reply with the numbers only: `1,3` and `1 3` both work. `all` adopts all of them; `none` or `0` adopts none. When a number does not exist or the reply cannot be understood, it asks again.
- Once the reply is accepted, it starts running and cannot be changed. To adopt more, run learn again or edit the project pack by hand.
- Without a reply for 24 hours, this step times out and fails. A scheduled learn also stops here and waits for you.

## Writing back to the project pack

`apply` is the host action `project-apply` with `scope: learn` ([`actions.py`](../src/myorch/runner/actions.py), [`project.py`](../src/myorch/project.py)). The sandbox cannot reach `myorch-knowledge`, so this happens on the host.

| Can change | Cannot change |
|---|---|
| Files under `guide/`, including new ones | `risk-rules.yaml`, `security.md`, `setup.sh` |
| `checks.sh`, `checks.env` | `project.yaml`, `image/`, `policy.yaml`, `providers/`, `sources/` and other files |

- It writes only the project's own directory `myorch-knowledge/<project>/`. Templates (for example mdm's `_templates/core-ng/`) stay unchanged. A file the project only inherits from the template is first copied from the template on its first change, then changed.
- All adopted changes are validated in memory first: the path is within the allowed scope, does not escape the project pack, and each `old` occurs exactly once. **If anything fails, nothing is written.** The step fails and lists the reasons.
- After writing, it commits in `myorch-knowledge`, committing only the files written this time (changes someone else staged are not included). The commit message is `learn(<project>): #1 title; #3 title`, and the body has `myorch-run: <run_id> step: apply` and the adopted numbers.
- Then it re-syncs the project pack snapshot. Runs that start after that use the new knowledge, and the project pack version recorded in the Mandate changes with it. Runs in progress keep using their own snapshots.
- Idempotent: when it finds this step has already committed, it counts as succeeded and does not write a second time.
- If a proposal works out badly: `git revert <commit>` in `myorch-knowledge`, then `uv run myorch project sync <project>`.

## Artifacts

All under `docs/learn/` in the workspace. The run's **Docs** tab in the console shows them, or use `uv run myorch ws <run> docs/learn`.

```
docs/learn/
  trail.json                 written at start: questions and answers, comments, rejections, failures, re-runs from the event log
  takeaways-learned.json     written at start: run_ids of takeaways already learned
  findings.json, outcomes.md findings and their outcomes, statistics table
  signals.json, signals.md   signals other than review
  takeaways.json, takeaways/<date>-<run_id>/   takeaways read this time
  proposals.json, proposals.md                 proposals
  follow-ups.md, flow-proposals.md             proposals that are not applied automatically
  accepted.json              the numbers you adopted
```

## Scheduled runs

The `--flow` of `myorch schedule add` takes the flow name `learn`. Schedules start runs through the same path as the console, so automatic run selection and writing `trail.json` and `takeaways-learned.json` work the same.

- When the run started by the previous schedule is still running (for example still waiting at `choose`), this one is skipped. To run both at once, add `--allow-overlap`.
- When there are no new runs, this one does not start a run. This holds even when the repository has new takeaways, because takeaways are only read inside a learn run.
- The run's starter is the person who created the schedule. `uv run myorch schedule list` shows when it runs next; `pause` and `delete` stop it.
