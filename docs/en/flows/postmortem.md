# postmortem

Analyses a problem that already got into the project's code: finds the commit that introduced it, works out at which layer it should have been stopped first and why it was not, proposes knowledge changes, and turns the change into an eval case. Use it after the problem is fixed (or reverted), when you want to know why it was not stopped at the time.

It only analyses and does not change project code; to fix the bug, use [development](development.md). The only things it changes outside are the local `myorch-knowledge` (the proposals you adopt) and the local eval set (`myorch-data/evals/cases/`). The difference from [learn](learn.md): learn looks for patterns across a batch of runs; postmortem works backwards from one concrete problem. learn later reads postmortem's conclusions too.

```sh
uv run myorch run flows/postmortem.yaml --project mdm -i commit=<fix or revert commit>
uv run myorch run flows/postmortem.yaml --project mdm -i bad=<commit that introduced the problem>
```

Console: choose the project first, then the task **Postmortem** (复盘), and fill in one of **Fix or revert commit** (修复或 revert 提交) or **Commit that introduced the problem** (引入问题的提交). The first message can describe the problem's symptoms and impact, or stay empty. If you write one, it is stored as `docs/external/request.md`, and analyze reads it.

<!-- flow-graph -->

## Inputs

- Give either `commit` or `bad`. When neither is filled in, the run does not start. When both are filled in, `bad` wins and `commit` is treated as the fix commit.
- The commit does not have to be on a branch (for example an unmerged PR): locate fetches it from GitHub by SHA.
- `runs` does not need to be filled in. At start the client fills in this project's newest development and review runs (at most 20; ended, failed and cancelled all count), and locate picks out the ones that touched this change.

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `locate` | Finds the commit that introduced the problem (see below), its PR, and which myorch runs touched it | `docs/postmortem/target.json`, `bad.diff`, `fix.diff` (when there is a fix commit) |
| `replay-before` | Host action: creates the eval case (draft) and starts a separate eval run that replays the change with the current knowledge. Runs at the same time as trace and analyze | Eval case, eval run `eval-<id>-before` |
| `trace` | From the runs that touched the change, lists what each layer saw at the time: which related files the requirements document mentioned, the findings of each review round near the change (10 lines before and after), CI results, the risk verdict | `docs/postmortem/trace.json`, `trace.md` |
| `analyze` | Works out what the problem is, at which layer it should have been stopped first and why it was not; proposes knowledge changes; writes the findings a reviewer is expected to report. Read-only; writes only `docs/postmortem/` | `postmortem.md`, `proposals.json`, `proposals.md`, `follow-ups.md`, `case.json` |
| `choose` | Lists the proposals and waits for you to reply with numbers. Skipped when there are no proposals | `docs/postmortem/accepted.json` |
| `apply` | Host action `project-apply`: writes back to the project pack, commits, re-syncs the snapshot. Skipped when nothing was adopted | A commit in `myorch-knowledge` |
| `replay-after` | Host action: writes the expected findings into the eval case. When proposals were adopted, replays once more with the new knowledge | Updated eval case, eval run `eval-<id>-after` |

`<id>` is the last part of this postmortem run's ID. For example, the replay of `postmortem-1a2b3c4d` is `eval-1a2b3c4d-before`.

### locate: how it finds the commit that introduced the problem

| What was given | How it finds it |
|---|---|
| `bad` | Used directly |
| `commit`, and its message contains `This reverts commit <sha>` | The reverted commit |
| `commit`, an ordinary fix | `git blame` on the lines the fix changed (for pure additions, the 2 lines before and after the insertion point); takes the commit that appears most often |

- When the commit that introduced the problem is a merge commit, the comparison base is its first parent, so the whole PR is looked at.
- The full-history clone lives outside the workspace (`/tmp`). The history that contains the fix does not enter the run's workspace.
- Runs that touched the change: runs whose branch has this commit while the base they started from did not; or runs whose linked PR is this commit's PR.
- The requirement is taken from `requirements.md` (or `pr.md`) of the first run that touched it. When no myorch run touched it, the commit message and the PR title and description are used.
- When it cannot find it, the step fails and says why: the commit is not in the repository; the lines the fix changed have no traceable origin (then use `-i bad=` to name it directly); the commit that introduced the problem is the repository's first commit.

### analyze: the layer that should have stopped it, and why

| Output | Values |
|---|---|
| `layer`: the first layer that should have stopped it | `requirement` requirements analysis should have asked; `implementation` the implementation guide should have avoided it; `review`; `security`; `risk` should have been judged high risk for a person to look at; `ci` deterministic checks; `approval` the approver should have seen it; `none` |
| `category`: why it was not stopped | `no-rule` neither the knowledge nor the checks covered it; `rule-not-applied` a rule existed but was not used; `flagged-not-handled` a reviewer or CI pointed it out, but it was rebutted, downgraded or ignored; `human-let-through` a person saw the risk and let it through anyway; `not-detectable` it really could not be found in advance |

- When no myorch run touched the change, the question is: had it gone through myorch at the time, which layer should have stopped it.
- At most 5 proposals, in the same format as learn (exact replacement or append), changing only `guide/`. Rules must be written so that they apply to the same kind of problem, not only to this one bug.
- Proposals better done as lint, CI or tests go into `follow-ups.md` and need a separate development run.
- `case.json` contains only the problems a reviewer should find from the change itself. When `category` is `not-detectable`, or only CI could find it, it is `[]`.

## Replays and the eval case

A replay is **a separate eval run** (the [eval](eval.md) flow, [`evals.py`](../../../src/myorch/evals.py)). The reviewer there sees only the change itself and the requirement, not this run's workspace: not the incident description, the fix, the analysis or the expected findings. The eval run clones the repository from GitHub, checks out the commit that introduced the problem, and deletes later commits (including the fix), branches and the reflog.

The case ID is this postmortem's run ID, stored on the local machine in `myorch-data/evals/cases/<run ID>.json`. This is how it reaches the eval set:

| When | Case state | What happens |
|---|---|---|
| `replay-before` | `draft` | Only the change, no expected findings yet; not visible in the eval set. Replays once with the current knowledge |
| `replay-after`, `case.json` has content | `pending` | The expected findings are written; it waits for you to confirm under **Flows → Eval set** (流程 → 评测集) in the console. When proposals were adopted, replays once more with the new knowledge |
| `replay-after`, `case.json` is empty | `rejected` | No problem that could have been found in advance; the case is not scored and not replayed again |
| You mark the valid findings under **Flows → Eval set** | `confirmed` | The case joins the eval set, and later `myorch eval run` uses it too. If you mark none, it is `rejected` |

- The case page shows how many findings each of the two replays caught. Before confirmation, all findings in `case.json` count; after confirmation, the ones you marked.
- To compare this run's two replays on the command line: `uv run myorch eval report --batch <postmortem run ID>`. It scores only against the findings you marked, so confirm the case first.
- Both replay steps are idempotent: a replay that was already started is not started a second time.
- A replay is another run; if it fails, the postmortem itself is not affected. Look at the eval run for the cause.

## Where you step in

| When | Console / Slack | Command line |
|---|---|---|
| `choose`: pick which to adopt | Reply with numbers in the conversation, or click `all` or `none` | `uv run myorch reply <run> choose "1,3"` |
| After the run ends: confirm the eval case | Open this case under **Flows → Eval set** and mark the valid findings | No command |

- Replies are written the same way as for learn: `1,3`, `1、3`, `all`, `none`; if the reply cannot be understood, it asks again. The details are in `docs/postmortem/proposals.md`.
- When nothing is adopted, the knowledge is not changed and there is no second replay.

## Writing back to the project pack

Uses the same host action `project-apply` as learn, also with `scope: learn`. For the rules see [learn's "Writing back to the project pack"](learn.md#writing-back-to-the-project-pack): write only when everything validates, commit only the files it wrote, the commit message is `learn(<project>): …` with `myorch-run: <run_id>`, re-sync the snapshot after writing, `git revert` is possible.

The only difference is which files are read: the proposals are in `docs/postmortem/proposals.json`, the adopted numbers in `docs/postmortem/accepted.json`. The prompt lets analyze change only `guide/`; the action itself validates against learn's scope and also accepts `checks.sh` and `checks.env`.

## Artifacts

All under `docs/postmortem/` in the workspace. The **Docs** (文档) tab in the console's right panel shows them, or use `uv run myorch ws <run> docs/postmortem`.

```
docs/postmortem/
  target.json            the commit that introduced the problem, base, fix, PR, runs that touched it, requirement
  bad.diff, fix.diff     the change that introduced the problem; the fix (sometimes)
  trace.json, trace.md   what each layer saw at the time
  postmortem.md          conclusion: the problem, the layer that should have stopped it and why, evidence, proposals
  proposals.json, proposals.md, accepted.json   knowledge proposals, the numbers you adopted
  follow-ups.md          proposals for deterministic checks that need a separate development run
  case.json              the expected findings for the eval case
```
