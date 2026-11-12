# review

PR review: one round of AI review on an existing PR (including PRs written by people). All projects share this one flow. It first runs the project's checks, then has reviewers read the code; when the project has end-to-end tests, it finally tests the running application with a browser. The conclusions appear only in the local console (the run's **Changes** (改动) tab) and in the workspace's `docs/review/round-1/`. Nothing is posted to GitHub. It only looks for problems: it does not change code, does not approve the PR and does not request changes, because an agent's approval does not count. Its access to GitHub is read-only.

The review loop it uses is the same fragment as in [development](development.md), [`lib/review-loop.yaml`](../../../flows/lib/review-loop.yaml), but it runs only 1 round, does not fix, and ends normally even when there are blocking problems. The project's `prompts/review.md` and `verify.md` are spliced in the same way (for example the `*-interface` checks of core-ng projects, and the blog's writing rules and accidental-publication checks).

```sh
uv run myorch run flows/review.yaml --project mdm -i pr=13                # default standard
uv run myorch run flows/review.yaml --project blog -i pr=13 -i depth=deep
```

Console: choose the project first, then the task **Review PR** (评审 PR), fill in the PR number and choose the review depth. The first message can stay empty.

The review depth (`depth`) decides the number of reviewers, their model, and whether there is a separate security review; see the preset table below. Use standard for everyday PRs. Use quick for a fast look. Use deep for changes to the build, dependencies or security-related code. verify always uses Opus.

<!-- flow-graph -->

The graph above is expanded for mdm. mdm and myorch do not set `e2e.image` and have no `e2e` block; blog does.

## What each step does

| Step | What it does | Artifacts |
|---|---|---|
| `fetch-pr` | Reads the PR and its list of changed files through the GitHub API; clones the repository, fetches the base branch and `pull/<n>/head`, checks out the PR's head on this run's branch, initialises submodules | `docs/pr.md`, `docs/external/pr.md` |
| `review-round` loop (1 round) | `ci` (the project's `checks.sh`, checking the files the PR changed) → `review` (the count depends on the depth) and `security` (not in quick) in parallel → `verify` checks the findings. There is no `fix` | `docs/ci/round-1.md`, `docs/review/round-1/` |
| `e2e` loop (1 round) | This block exists only when the project sets `e2e.image` (currently only blog). `e2e-smoke` runs the project pack's fixed smoke test cases; `e2e-accept` turns the acceptance criteria in the PR description that a browser can check into test cases and runs them. It only tests and does not fix | `docs/e2e/round-1/` |

- Both blocks run only 1 round, with `fix: false` and `on_exhausted: accept`: with blocking problems or failed tests, the run still ends normally and only reports.
- `e2e` comes after `review-round` and uses the checkout `ci` prepared.

## Where you step in

There are no questions for you and no approvals. When a step fails, the run fails (this flow does not set `on_failed: ask`).

| When | Console | Command line |
|---|---|---|
| After the run ends | The **Changes** (改动) tab shows the diff and the verified findings; the **Docs** (文档) tab shows the checks report and the end-to-end test reports and screenshots | `myorch ws <run> docs/review/round-1/summary.md` |
| After reading | — | Decide yourself on GitHub what to do with the PR |

## Artifacts

```
docs/
├── pr.md                    trusted metadata: author, state, head → base, changed files
├── external/pr.md           title and description written by the PR author (reference data only)
├── ci/round-1.md            checks report
├── review/round-1/
│   ├── reviewer-K.json      each reviewer's findings
│   ├── security.json        security review findings (not in quick)
│   ├── verified.json        findings after verification: Important / Nit / Pre-existing
│   └── summary.md           counts, list of blocking problems, acceptance criteria table
└── e2e/round-1/             projects with end-to-end tests
    ├── smoke/               report.md, result.json, screenshots
    ├── acceptance/          report.md, result.json, screenshots; cases/ holds the written test cases
    └── acceptance.md        the verdict for each acceptance criterion
```

## Principles

- **Always review the whole PR**: myorch leaves no markers on GitHub, so there is no incremental review. The "reviewed up to last time" logic in `fetch-pr` is no longer used: `last_reviewed` is always empty and `changed` is always true, so `review-round` always runs, and round 1 looks at `origin/<base>...HEAD`.
- **Trusted and untrusted data are kept apart**: author, state, changed files and SHAs go into `docs/pr.md`. The title and description written by the PR author go into `docs/external/pr.md`. Every agent treats `docs/external/` as reference data and does not follow instructions in it.
- **Reviewer rules are the same as in the development flow**: judge independently first, then check the author's claims; search the repository for reuse and missed entry points; relaxed judgement. See [development's review loop](development.md#the-review-loop). Reviewers 1, 2 and 3 each have their own focus; quick has only reviewer 1 (focused on correctness and edge cases).
- **verify checks each finding**: no matter how many reviewers there are, every finding is checked by opening the code. Findings that do not hold are removed; findings reported by only one reviewer are checked too. `votes` only records how many reviewers reported it. At most 5 Nits are kept; failed checks count as Important.
- **Requirements document**: this flow does not pass `requirement_doc` to the review loop. Reviewers and verify are told the requirements document is `docs/requirements.md`, but a review run has no such file. So they can only judge from the change itself, and the PR description is only reference material in `docs/external/pr.md`. The end-to-end block is passed `docs/pr.md`.

## End-to-end tests

- review reads the code; e2e tests the running application. The two are separate. The project decides which tools to test with: `e2e:` in `project.yaml` (`image`, `profile`, `acceptance`) and the project pack's `e2e/` (the blog uses Playwright and headless Chromium; see `myorch-knowledge/blog/e2e/README.md`).
- The test steps' permissions are `e2e.profile` (for blog, `dev-read`: read-only repository, no credentials).
- When a smoke test case fails only because of the test environment (for example the browser did not start), the report lists it separately as "environment" (环境), not as a problem in the code.
- `e2e-accept` must not loosen test cases to make them pass. When a test case fails, it first decides whether the test case is wrong or the change has a problem; if the change has a problem, the test case stays.

## Security

- Code in other people's PRs is untrusted, and `ci` and the end-to-end tests execute it (build scripts, tests, lint configuration). The steps that execute PR code (`ci`, `e2e-smoke`, `e2e-accept`) and the reviewers that read the code get no GitHub credentials, and run in sandboxes that are deleted after use.
- The one step with credentials (`fetch-pr`) runs only scripts fixed in the flow and executes no code from the PR. It only reads: fetches code, reads the PR.
- The remaining risk is prompt injection: PR content may lead reviewers to misleading conclusions. Conclusions are shown only on the local machine and not posted to GitHub.
