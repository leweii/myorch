# The delivery interface: what the workflow hands to a channel (2026-11-02)

Status: **The workflow side is implemented**: the approval gate (request changes, reminder, timeout) and `deliver` (`flows/lib/deliver.yaml`). The workflow side is implemented by the workflow review session. The channel side (opening a PR on GitHub) is implemented by the session working on channels.

## Division of work

- **workflow** (the last step of `development.yaml`, `deliver`, fragment `flows/lib/deliver.yaml`):
  1. Check that the current branch is not the base branch.
  2. Check that HEAD is the commit that was approved. If not, stop and do not push.
  3. Push this run's branch to the project's git remote (`git push`; credentials come from the project's `github` provider role).
  4. Write `docs/delivery.md`: the description of this change, in the format below.
  5. Call no platform API: do not open a PR, post comments or add labels.
- **channel**: after receiving the `run_finished` event, deliver according to the target system. On GitHub that means opening a PR:
  - PR title: `delivery.title`.
  - PR body: `delivery.description`, followed by `delivery.pr_template` (the repository's own PR template, if there is one).
    When the request came from an issue in the same repository, add `Closes #<issue>` to the body. The channel itself knows where the request came from (the Mandate's `source`).
  - head = `delivery.branch`, base = `delivery.base`.
  - Idempotent: if this branch already has a PR, do not open a new one. Only post the existing PR's link back to the conversation.
  - The channel adds its own hidden marker (for example `<!-- myorch-pr run=… -->`) to recognize its own PRs.
  - Delivery failures (push succeeded but opening the PR failed) are retried by the channel or explained in the conversation. The run itself has already finished and is not rerun.

## The `data` of the `run_finished` event

In addition to the existing fields (`status`, `failure` and so on), when the run succeeds and `deliver` ran, there is a `delivery`:

```json
{
  "status": "succeeded",
  "delivery": {
    "branch": "LAB-5-myorch-development-javalab8-issue8",
    "base": "main",
    "commit": "<the approved and pushed commit, 40 characters>",
    "title": "Reject null or blank id in OrderService.pay and cancel",
    "ticket": "5",
    "repo": "leweii/myorch-java-lab",
    "description": "<full text of docs/delivery.md>",
    "pr_template": "<full text of the repository's PR template, empty string if none>"
  }
}
```

- When requirements analysis decides no change is needed (`proceed=false`), there is no `delivery`, and `stopped` holds the reason (as today).
- When the run fails, is cancelled, or the approval is rejected or times out, there is no `delivery`.
- The workflow no longer produces the old field `pr_url`. After the channel opens a PR, it reports the link in the conversation itself.

## Format of `docs/delivery.md`

```
# {title}
Ticket: {ticket} (omit this line when there is no ticket number)

## What changed
{the "Changes" section of implementation.md}

## How it was verified
- Checks: {conclusion of the last CI round}
- Acceptance criteria: {the acceptance criteria table from the last round's summary.md}

## review
{rounds} rounds, {n} blocking issues, {m} unresolved (listed), {k} nits

## Risk
{the "Conclusion" and "For the approver to check" sections of risk.md}

## How to roll back
Before merging: abandon branch {branch}. After merging: revert the commits this merge brought in ({base}..{commit}).
```

The full requirements document, implementation notes, review notes and risk assessment stay in the run (`myorch review <run>`, the console).

## New approval gate events (same batch of changes, for channels and the console)

- The `approval` event gains a field `actions: ["approve", "reject", "changes"]`. `changes` means "request changes":
  the channel sends the approver's text back to the run as change requests (CLI: `myorch changes <run> <step> "<comments>"`). The run goes back to the review loop to fix them, then the risk is assessed again and approval is requested again.
- `approval_reminder`: sent once when an approval has waited longer than `remind_after` (development sets 24 hours). The channel forwards it to the approvers.
- When an approval is still not handled after `timeout` (development sets 7 days), the step ends as `failed`, and `error` is "Approval timed out: nobody handled it within 7d". The run ends, `run_finished.data.status` is `failed`, and `failure` contains that sentence. There is no new status value.
- After changes are requested, the same approval gate opens again. The second `approval` event has key `approve:<step>:1`, the third `approve:<step>:2`, and so on. The `approval_reminder` key carries the same sequence number (`remind:<step>:1`). The first one has no sequence number, as before.
- The `data` of `approval_reminder`: `needed` (how many approvals are needed), `approved_by` (people who already approved), `waited` (the value of `remind_after`).

## Update 2026-11-13: `deliver` can open the PR itself

With the project setting `flows.development.open_pr` (turned on for mdm), `deliver` opens the pull request in the same step, right after the push, with the project's GitHub provider (`gh api`, so the token stays in OpenShell). It reuses the branch's open PR if there is one. The body is `delivery.description`, then `delivery.pr_template`, then `<!-- myorch-pr run=<run_id> -->`. The link is the step's `pr_url` output, which the existing paths already show: the console's "PR opened" item, the `pr_opened` event, and `run_finished.data.pr_url`. No channel side comes back. Projects without the setting behave as described above.

## The channel side (implemented 2026-11-02, deleted 2026-11-06)

**Since 2026-11-06 there is no channel side**: opening PRs is not a problem myorch needs to solve. `channels/delivery.py`, `deliveries.json`, the console's delivery records and "Retry opening PR" have all been deleted. The workflow side is unchanged. `run_finished.delivery` only tells people which branch was pushed. Below is how it worked before deletion.

Delivery happened in the channel layer. The code was in `src/myorch/channels/delivery.py`, run by `myorch serve` (or `myorch ui`, which is the same). Each process had only one delivery loop, so only one `myorch serve` could run at a time. The state file stayed at `myorch-data/ui/deliveries.json`. The console only showed delivery records. "Retry opening PR" also called the same delivery loop.

- The delivery loop read `myorch-data/events.db`. When it found a `run_finished` with `delivery`, it opened a PR. Runs without `delivery` were ignored, including runs that still opened their own PR with `push-and-pr`. So PRs were never opened twice, before or after `deliver` went live.
- The PR was opened in a one-off sandbox job (`runner/jobs.py`) using the project's `github` provider. The token stayed in OpenShell. Neither the channel nor the console could get it.
- Idempotent: if the branch already had a PR (in any state), only the link was recorded. If the existing PR's head was not `delivery.commit`, it did nothing and reported an error. So `delivery.commit` had to be the full 40-character hash.
- PR body: `delivery.description`, followed by `delivery.pr_template`, and finally the hidden marker `<!-- myorch-pr run=<run_id> -->`. `Closes #<issue>` was added by channels other than the console, based on `source`. Runs started from the console have no issue.
- State was kept in `myorch-data/ui/deliveries.json`: the `seq` read so far and each run's delivery result. Nothing was lost while the console was stopped: on the next start it continued from the last `seq`. On failure it retried automatically 3 times, 60 seconds apart, and then showed a "Retry opening PR" button in the run's conversation. A delivery interrupted by a console restart was redone on the next start.
- The console needed `branch`, `base`, `commit`, `title` and `description` in `delivery`. `repo` and `project` were optional: when missing, it found the project from the run's mandate, then took the repository from the project pack.
- "Request changes": the console already had the button in place. It calls `engines.temporal.client.request_changes(client, run_id, step_id, approver, text)`. Until that function existed, the button was hidden. Implement it with this signature, or tell the console to change.
- `approval_reminder` and `expired`: the console did not read these two events. The approval stayed in "Waiting for you". After `expired`, the run status was failed and the conversation showed the failure reason.
