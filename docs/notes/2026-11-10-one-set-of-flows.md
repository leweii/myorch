# One set of flows; the project pack decides the shape (2026-11-10)

Status: **code, flows, project packs and unit tests done.** This repository is on branch `converge-flows`, and myorch-knowledge is on a branch of the same name. The two must be merged together.

## What the user decided

- Return to "one set of generic flows + project pack parameters". The per-project copies `ck-*`, `blog-*` and `mo-*`, made since 2026-11-03, are merged into one set. The commit message back then said exactly that: "Diverge first, converge later".
- development uses a single develop step as its skeleton, which is ck's approach. mo's three steps analyze → design → implement are dropped.
- Project-specific prompt clauses are spliced **verbatim** into the step prompt when the flow is expanded. They are not left for the agent to read from the guide.
- Differences in flow shape are written under `flows:` in `project.yaml`. Nothing needs to be chosen when starting a run.

## Why

After copying per project, one improvement had to be made in three copies, and the three soon diverged. When changing a flow, you could not tell which differences a project really needed and which were just drift after copying. Principle 14, "flows are independent of projects", said one thing; the repository did another.

Copy-by-copy comparison:

| Fragment or flow | Differences between the three copies | Outcome |
|---|---|---|
| `choose-proposals`, `e2e`, `review` | Only the names differ | Merged as is |
| `sync-base` | ck has an extra bare-mirror speed-up | All projects use it |
| `review-loop` | ck has extra clauses on reuse, missed entry points, ticket checks and so on. All are generic engineering requirements; only the `*-interface` one is Core-NG specific. mo has an extra "suggest a write-back when the project pack is wrong" | ck's clauses are generic. `*-interface` goes into `prompts/review.md` of the core-ng template. Project pack fixes are on for all projects |
| `deliver` | ck is a superset of mo; blog has an extra "re-check after fixes" section | Use ck's. blog's ci-final is gone |
| `development` | Different shapes (see below) | One flow, trimmed by project settings |
| `learn`, `postmortem`, `eval`, `sweep` | Only a ck version, and its content was already generic | Drop `ck-` and `projects: [mdm]` |

## Mechanism (platform changes)

1. **Project settings** `flows.development`, in `Development` in `src/myorch/project.py`: `reviewers`, `max_rounds`, `security`, `on_exhausted`, `takeaway`, `incident`. The defaults are ck's previous behaviour; `takeaway` and `incident` are off by default. A template can set them, and the project overrides the template.
2. **Prompt slots** `prompts/<slot>.md`. The slots are develop, review, verify, fix and risk. The template's text comes first, then the project's. A file name that is not one of these slots is an error at once, so text under a misspelled name is not silently dropped. A flow references a slot with `{{project.prompts.<slot>}}`. Expansion splices the text verbatim into the prompt, and it is visible in the mandate snapshot.
3. **Flow-level `if:`**, on steps, `use:` entries and inputs. The expression is evaluated against `project` and trimmed at expansion (`apply_project_if` in `core/fragment.py`).
   - When a step is trimmed, the steps that depend on it **inherit its `needs`**. For example, after the four takeaway steps are trimmed, `risk-rules` waits for `review-fix` instead. If it were only removed from `needs`, `risk-rules` would have no dependencies and would run at the very start.
   - When an input is trimmed, it is not deleted. It becomes `hidden` and keeps its default. The form does not show it, but `{{inputs.ticket}}` in a prompt still renders at run time.
   - An input's `required` can also be an expression, for example `required: "not project.jira.url"`: with Jira, the ticket is the request and task can be left empty.
4. **`with:` fills in project values first**, so project values can be passed by type to a fragment's int and bool parameters. The old string form `acceptance: "true"` in e2e therefore goes back to a bool.
5. **Inputs per project**: `flowfile.flow_inputs(path, project)`. The CLI, relay, console (`inputs_for`), Slack form and run tags all resolve inputs per project. The ticket prefix comes from the project's Jira key. Old `ck-development` runs read `development.yaml`, so they can still be found by ticket number.
6. `flowfile` fills in defaults for project dictionaries that lack `flows` or `prompts`. Such dictionaries come from old mandates or are hand-written.

## What changes for each project

| | Before | Now |
|---|---|---|
| mdm | ck-development | Mostly unchanged. `flows.development.takeaway: true`. The `*-interface` clause and the Gradle notes go into the core-ng template's prompts. The review loop gains project pack fixes |
| blog | One reviewer looks once, one fix, then the checks run again after the fix; approval is fixed at 1 person; no risk classification | 1 reviewer, 1 round, no security review, `on_exhausted: accept`. Gains verify, risk classification (risk-rules, classify-risk) and project pack fixes. The checks no longer rerun automatically after fixes: fix runs the checks itself, and risk-rules lists commits that did not go through review. `risk-rules.yaml` has `default: 1`, so a person still has to approve every time |
| myorch | analyze → design → implement | One develop step. Incident analysis, the platform items design must consider and "what to do after the merge" go into `myorch/prompts/develop.md`. How to write the risk statement goes into `prompts/risk.md`. `flows.development.incident: true`. `outputs.cause` is no longer produced; the cause classification is written in the summary and the requirements document |

## Notes for the merge

- The two repositories must be merged together. Old code ignores the new `flows:` and `prompts/`, so merging myorch-knowledge first is fine. The other way round, merging only this repository leaves mdm without takeaway.
- Run ID prefixes are now `development-`, `review-` and so on, without the project. Sandbox names are `dev-deve-xxxxx`. See the project in the project column of the run list; a sandbox's project is in its `myorch.project` label.
- Runs that have not finished use the flow expanded at submission, so they are not affected. `from_run` picks runs by role, so old `ck-development` runs can still be continued.
- This branch has **not changed** the `flows/*.md` flow manuals or the places in docs/ that name flows: those files are being heavily rewritten in the main work tree and are not committed yet. Places to change: ARCHITECTURE "Code structure", 06-flows, 07-projects (`flows:`, `prompts/`), 08-flow-syntax (flow-level `if:`, input `if:` and `required` expressions, `with:` filling in project values first, the fragment table), 09-principles §14, the command examples in README, and renaming or merging `flows/ck-*.md`, `blog-*.md` and `mo-development.md`.
