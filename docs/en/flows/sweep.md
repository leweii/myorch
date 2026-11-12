# sweep

Finds bugs in a project's existing code. All projects share this one flow. Each run looks at a batch of files in a fixed order, with several auditors in parallel, verifies the findings and writes a daily report. It suits nightly schedules that work through the whole repository batch by batch.

It is observe-only: it writes only the daily report `docs/sweep/report.md`. It does not change code, does not push, does not start development and does not change the knowledge. To fix a problem it found, a person starts a separate [development](development.md) run. The difference from [review](review.md): review looks at a PR's change; sweep looks at code already on the main branch.

```sh
uv run myorch run flows/sweep.yaml --project mdm                     # run once, audit depth defaults to standard
uv run myorch run flows/sweep.yaml --project mdm -i depth=deep
uv run myorch schedule add nightly-sweep-mdm --flow sweep --project mdm --cron '0 2 * * *'   # every night at 2:00
```

Console: choose the project first, then the task **Find bugs** (找 bug), and choose the audit depth. The first message is not needed.

<!-- flow-graph -->

## Which files it looks at

Decided by `sweep:` in the project pack's `project.yaml` (see [7. Project packs](../07-projects.md)):

| Field | Meaning | When not set |
|---|---|---|
| `paths` | Look only at these directories or files | The whole repository |
| `include` | Look only at files matching these globs (`**` allowed) | No filter |
| `exclude` | Skip files matching these globs | Nothing excluded |
| `chunk` | How many files per run (1 to 200) | 20 |

- The file list comes from `git ls-files`, sorted by path.
- mdm and blog currently have no `sweep:`, so they look at the whole repository, 20 files per run. myorch looks only at `src/myorch`, `flows`, `tools` and `deploy` (not `src/myorch/ui/static/vendor/**`), 15 files per run.
- sweep reads the configuration from the project pack snapshot. After changing `project.yaml`, run `uv run myorch project sync <project>`.
- When the configuration selects no files at all, `pick` fails.

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `sync` | Clones or updates the repository and checks out the latest base branch ([`lib/sync-base.yaml`](../../../flows/lib/sync-base.yaml), sped up by the project's shared bare mirror) | `repo/` |
| `pick` | Takes the next batch of files at the cursor and copies the known issues into the workspace. The cursor does not move here | `docs/sweep/chunk.txt`, `docs/sweep/known.json` |
| `audit` | Each auditor reads the batch on its own and looks for defects that exist now. Read-only; does not change code | `docs/sweep/audit-<n>.json` |
| `verify` | Checks each finding by opening the code, removes false positives and known issues, merges duplicates, writes the daily report | `docs/sweep/verified.json`, `docs/sweep/report.md` |
| `record` | Records the new issues in `known.json` and moves the cursor forward | `cursor.json`, `known.json` in the shared directory |

### audit: what it looks for

- Correctness (logic errors, missed edge cases, unhandled errors, mismatches with docs or comments), security (injection, leaked secrets, unsafe defaults, missing permission checks), errors users see (broken links, wrong data or display).
- Each auditor has its own focus: auditor 1 looks more at correctness and edge cases, auditor 2 at security and error handling, auditor 3 at whether docs and data agree.
- Not reported: code style, naming, things that could be written better but are not wrong, missing tests; known issues; code outside this batch of files (used only to understand context).
- There are only two severities: `Important` (wrong behaviour, wrong data, security problems; worth having someone fix) and `Nit` (really a problem, but with small impact). Findings it is not sure about are not reported.
- When there is a `guide/review-guide.md`, it follows its standards.

### verify: verification

- Each finding is checked by opening the code; findings that do not hold are removed.
- The same issue reported by several auditors is merged into one, and `votes` is the number of auditors who reported it. Findings reported by only one auditor are kept if they hold.
- Findings that are the same issue as one in `known.json` are removed.
- The daily report: with no new issues, it has only the sentence "No new issues found in this batch of files." (这批文件没有发现新问题。), followed by the directories that were looked at. With new issues, the first sentence gives the number of Important and Nit findings, then lists each one by severity with its location, the problem, the consequence and a suggested fix. No process notes, no pleasantries.

## State across runs

State across runs lives in the project's shared directory `shared/<project>/sweep/` (on the myorch storage volume). Only `pick` and `record` mount it.

| File | Content | Written by |
|---|---|---|
| `cursor.json` | `{"next": index of the file the next batch starts at, "total": total number of files, "run": the run that wrote it}` | `record` |
| `known.json` | List of reported issues: `fingerprint`, `file`, `line`, `title`, `severity`, `run`, `at` (date) | `record` appends |

- **The cursor moves forward only after the whole run succeeds.** `record` is the last step. If any earlier step fails, it does not run, and the next run scans the same batch again.
- After the last file it starts over from the beginning, so it can keep running on a schedule.
- The cursor is a position in the sorted list, not a file name. When files are added or deleted, the start of the next batch shifts by a few files: a few may be skipped or looked at twice, and the next pass makes up for it.
- Known issues are deduplicated twice: verify decides by meaning whether two are the same issue, and `record` then removes exact duplicates by the `file|title` fingerprint.
- `known.json` only grows. To scan from the beginning, delete `cursor.json`. To have an issue reported again, delete its entry from `known.json`. Each run's workspace also has a copy from that time in `docs/sweep/known.json`.

## Where you step in

You are not needed while it runs. To read the daily report: open the run in the console and find `docs/sweep/report.md` under its **Docs** (文档) tab; or `uv run myorch ws <run> docs/sweep/report.md`. For issues worth fixing, start a separate development run.

## Scheduled runs

- `--flow` takes the flow name `sweep`. For a different audit depth, add `-i depth=quick` or `-i depth=deep`.
- When the run started by the previous schedule is still running, this one is skipped, so two runs never move the cursor at the same time. Do not add `--allow-overlap`: two runs would scan the same batch, and the one that ends later could overwrite `known.json`.
- The run's starter is the person who created the schedule. `uv run myorch schedule list` shows when it runs next; `pause`, `unpause` and `delete` manage it; `trigger` runs it once right away.

## Artifacts

```
docs/sweep/
  chunk.txt          the files looked at this time, one per line
  known.json         copy of the known issues at start
  audit-<n>.json     each auditor's findings
  verified.json      new issues after verification
  report.md          daily report
```
