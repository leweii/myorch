# eval

Scores the reviewer. It replays the change that a past review looked at, unchanged, and lets the reviewer look at it again. It compares the result with the real issues a person confirmed and counts how many were caught, how many were missed and how many extra findings were reported. Use it after you change the reviewer's prompt, its model or the project pack's `guide/`, to tell whether things got better or worse. It writes nothing outside: it does not push, does not change code and does not change the project pack.

One eval run replays one case at one review depth. Collecting, confirming, batch-starting and scoring cases all happen on the local machine through `myorch eval` ([`src/myorch/evals.py`](../src/myorch/evals.py)). The flow itself only prepares the code and runs one review round.

```sh
uv run myorch eval collect                              # collect candidate cases from past runs
uv run myorch eval list                                 # cases and their state
# in the console under "Flows → Eval set", mark for each case which findings are real issues
uv run myorch eval run --depth quick --depth standard   # one eval per confirmed case and per depth
uv run myorch eval run --case <case ID>                 # run only one case (default depth standard)
uv run myorch eval report [--batch <batch>]             # comparison table
```

Do not start it from **+ New run** in the console or with `myorch run`. Only `myorch eval run` (and postmortem's replays) write the case into the workspace. When you start it directly, `prepare` finds no case and fails. The Slack start dialog does not list this flow.

<!-- flow-graph -->

## Where cases come from

A case is the change that round 1 of a past review looked at, plus the real issues a person confirmed. Cases are stored on the local machine in `myorch-data/evals/cases/<case ID>.json`.

| Stage | Who | What |
|---|---|---|
| Collect | `myorch eval collect` | Scans all runs. A development or review run becomes a candidate when its round-1 `verified.json` has an Important or Nit with a file location. The case ID is that run's ID. Pre-existing findings do not count as issues of this change. It records the comparison range (base and head commits), the commits on the branch, the requirements document of the time (`docs/requirements.md`, or `docs/pr.md` when there is none) and the findings. Cases that already exist are not collected again |
| Confirm | You, in the console under **Flows → Eval set** | Mark each finding that really is an issue and click **Add to eval set**. The state becomes confirmed. When none of them is, click **None are real issues** and the case is dropped. For old runs that did not record a head, the head is inferred (the first commit on the branch), and the page asks you to check it |
| Replay | `myorch eval run` | Starts one eval per confirmed case and per depth. All runs of one batch share a batch ID. Uses the latest version of the project pack and attaches no knowledge sources |
| Score | `myorch eval report` | Reads each replay's `docs/review/round-1/verified.json` and compares it with the marked findings. See "How scoring works" |

[postmortem](postmortem.md) also creates cases (`origin: git`). The change is the commit in the project repository that introduced the problem. It does not come from a myorch run, and the case ID is the postmortem run's ID. The postmortem starts these replays itself: once with the knowledge of the time, and, when you adopt the knowledge changes it proposes, once more with the new knowledge. `myorch eval report --batch <postmortem run ID>` shows them.

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `prepare` | Reads `docs/eval/case.json`. Clones the repository from the source run's own workspace (mounted read-only, so commits that were never pushed to GitHub can be replayed too). Postmortem cases are cloned from GitHub. Checks out the case's head, then deletes the `origin` remote, all other refs and the reflog, runs a gc, and points `origin/eval-base` at the case's base. Writes the requirements document of the time to `docs/eval/requirement.md` | `repo/`, `docs/eval/requirement.md` |
| `review-round` loop (1 round) | The same review round as in [review](review.md): `ci` → `review` (the count depends on the depth) and `security` → `verify`. The comparison base is `eval-base`, and the requirements document is `docs/eval/requirement.md`. There is no `fix`. When the loop runs out of rounds, the result is accepted automatically | `docs/ci/round-1.md`, `docs/review/round-1/` |

The depths are the same three as in review. See the preset table above.

## Not leaking the answers

- **The answer key stays out of the workspace**: `myorch eval run` writes only the case fields that may be seen into `docs/eval/case.json`: origin, source run, flow, repository, base and head commits, requirements document. The findings a person confirmed stay on the local machine in `myorch-data/evals/cases/`.
- **Later commits are not kept**: the source run's later fix commits would tell the reviewer where the problems are. So the repository keeps only the history reachable from head. Branches, remote refs and the reflog are deleted.
- **Nothing outside is writable**: after `origin` is deleted, no step in the replay can push.
- The reviewer also does not see the source run's technical design, implementation notes or the original ticket text. The workspace contains only the requirements document and the change. So the eval's review has fewer inputs than a review in the real development flow.

## How scoring works

Scoring happens on the local machine, not in the flow:

- Only Important and Nit are compared, on both sides.
- **Caught**: a replay finding is in the same file as a marked finding and the line numbers differ by at most 10 (a match on any one location counts).
- **Missed**: marked findings that were not caught.
- **Extra**: findings the replay reported that matched no marked finding. They may be false positives, or real issues nobody flagged at the time. The number of Important ones among them is listed separately.
- `report` first lists each run (depth, case, caught/expected, extra), then sums up per depth: cases, expected, caught, missed, recall (caught ÷ expected), extra, extra Important. Postmortem replays are split into "depth·before" and "depth·after". Runs that have not finished are not counted, and a note at the end says how many are still running.

## Where you step in

| When | Console | Command line |
|---|---|---|
| After collecting | Under **Flows → Eval set**, mark the real issues case by case | Confirming is console-only; `myorch eval list` shows the state |
| After the runs finish | Open each run and look at `docs/review/round-1/` | `myorch eval report --batch <batch>` |

The run itself has no questions and no approvals.
