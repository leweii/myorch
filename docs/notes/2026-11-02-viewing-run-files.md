# How to view a run's files on the Mac: test record (2026-11-02)

Status: **Abandoned (2026-11-03).** The user decided to do neither A nor B; there is another plan. The text below is kept only as a test record. Previous status: both A and B were tested. **Addendum (same day): myorch will later be used by a team. A and B only solve "viewing on my own Mac", so the direction changed to C (server-side event store + web page); see the end of this note.**

## Problem

Each step's outputs (code, `docs/`, manifest, logs, agent conversation) are written under `runs/<run_id>/` in the Docker volume `myorch-artifacts`. The data survives sandbox deletion, but the volume lives inside the Docker Desktop VM, where Finder and VS Code cannot see it. Today the only options are `myorch ws` (reads one file at a time, about 1 second each) or `myorch export` (copies a whole run by hand and gives a snapshot).

## Option A: back the volume with a Mac directory (bind-type volume)

```sh
docker volume create --driver local --opt type=none --opt o=bind --opt device=<Mac directory> \
  --label openshell.ai/sandbox-attachable=true --label openshell.ai/sandbox-attachable-workspace=default <name>
```

Test results:

- Works at the Docker level. After `chown 1000:1000` in a helper container, the owner shows as 1000 inside the container and as the current user (uid 502) on the Mac. Both sides can read and write.
- **OpenShell refuses to create a sandbox with it**:
  `docker volume '…' is backed by a host bind mount and requires enable_bind_mounts = true in [openshell.drivers.docker]`
- Using A requires setting `enable_bind_mounts = true` under `[openshell.drivers.docker]` in `/opt/homebrew/var/openshell/gateway.toml`. Judging by the error message in the gateway binary (`docker bind mounts require enable_bind_mounts = true`), this switch also allows ordinary bind mounts. That means any caller that can pass `--driver-config-json` to the gateway could mount any Mac directory (such as `~/.ssh`) into a sandbox. Today only myorch calls the gateway, but this removes one layer of defence.
- Because the switch was not turned on, **A's read/write performance was not tested** (a shared directory is expected to be slower than the VM's own disk; the Gradle cache would suffer most).

A's benefit: what agents write is visible on the Mac in real time, with no sync needed.

## Option B: sync the volume to a Mac directory

A helper container (alpine + rsync) mounts the volume read-only and the Mac directory read-write. Test results:

| Operation | Time |
|---|---|
| Full sync of `runs/` (290,000 files, 5.7 GB on the Mac) | 19 minutes |
| Full sync again (no changes, scan only) | 116 seconds |
| Sync one run only (incremental) | 1 second |

The synced files are owned by the current user on the Mac. Finder and VS Code can open them directly.

Almost all of the size is build output in `repo/`. The largest run is mdm's `onboard-17e1299e`:

| Directory | Files | Size |
|---|---|---|
| `repo/` | 133,737 | 3.4 GB |
| `.claude/` (agent conversation) | 13 | 1.9 MB |
| `docs/` | 7 | 100 KB |
| `.myorch/` (manifest, questions, logs) | 14 | 96 KB |
| `memory/` | 1 | 4 KB |

## Recommendation (leaning towards B)

1. After each step, the worker syncs only this run's `docs/`, `.myorch/`, `.claude/` and `memory/` to `myorch-data/runs/<run_id>/`. This takes about 1 second. The gateway is not changed and sandbox permissions stay the same.
2. For code, do not copy `repo/`. Export only the changes: `changes.diff` (against base) and the list of commits. The git command that produces the diff must use `--no-ext-diff`, `-c core.fsmonitor=false` and `-c core.hooksPath=/dev/null`, so that no program from the repository's config runs (the console's diff already does this). When the full repository is needed, still use `myorch export`.
3. Add `myorch sync <run>` to back-fill historical runs.
4. The local web page (ARCHITECTURE.md §12, second step) reads this directory and the Temporal state.

## Found along the way

- Run data is never cleaned up. The single run `onboard-17e1299e` takes 4.1 GB in the volume (mdm's build output). A cleanup command or a retention policy is needed.
- The test volume `myorch-bindtest` and the directories `myorch-data/bindtest` and `myorch-data/mirrortest` have all been deleted. The real `myorch-artifacts` was not changed.

## Addendum: once a team uses it (2026-11-02)

A and B assume "one person viewing files on their own Mac". For team use, the data cannot live only on one person's computer, and viewing cannot depend on opening a folder. So both are suitable only as a stopgap. Instead, follow the approach of pi-cloud-agent (github.com/zen8labs/pi-cloud-agent):

- **Option C: event store.** The worker turns each step's results (manifest, docs, tool calls and usage from the session transcript, a truncated diff produced inside the sandbox) into events and writes them to a server-side database. Large files go to file storage. The web page reads only from there, not from the volume, so deleting a sandbox has no effect. The sandbox needs no network callback and still writes only its own `/sandbox/out` and `/sandbox/logs`.
- **Presentation**: a web service with login. The home page is "Waiting for you" (questions, approvals). The run page is a timeline of steps. Opening a step shows its activity (tool calls), outputs and diff. Review rounds get their own cards. Approvals and answers happen on the web page, with identity from the login instead of being self-declared on the command line.

Team use brings these further requirements (not only for viewing):

| Area | Now (single user) | Team needs |
|---|---|---|
| Deployment | One Mac, Docker Desktop, Temporal dev server | A Linux server (or cluster), a production Temporal and database, resource and concurrency limits |
| Identity and permissions | No login. The approver is self-declared with `--as` | Login (GitHub or company SSO). Per-project control over who can view, start and approve |
| Code visibility | Only me | Session transcripts and diffs contain company code: per-project authorization, redaction before writing out, a retention period |
| Model credentials | Personal subscription token | Team or organization API key. Cost tracking per project and per person (the data from ⑪) |
| GitHub identity | mdm and java-lab use the personal account `leweii` | Must not act as an individual: a bot account or a GitHub App |
| Security | Issues such as repository config running programs locally and untrusted content reaching the web page | Other people's PRs and more initiators: ⑬-1 to ⑬-4 must be fixed first |
