# 6. Flows

Each flow is described in the separate **Flow manual**: [workflows/README.md](workflows/README.md). It has one chapter per flow: what it does, its inputs, the step graph, what each step does, and when it needs you. The step graphs and input tables are generated from `flows/*.yaml`. The text is in `flows/<flow>.md`. The manual's home page covers roles, which flow to use when, how to start a run and step in, and how to continue on the same branch.

This chapter only lists the flows.

## The flows

| Role | Task in the console | Purpose | Flow file |
|---|---|---|---|
| `development` | Development (开发) | From requirement to pushed branch: requirements, implementation, review, approval, push the branch. Does not open a PR | `development.yaml` |
| `review` | Review PR (评审 PR) | Reviews an existing PR. The verdict is shown in the console | `review.yaml` |
| `postmortem` | Postmortem (复盘) | Finds which change introduced a missed problem and which layer should have caught it, improves the knowledge, generates eval cases | `postmortem.yaml` |
| `sweep` | Find bugs (找 bug) | Finds bugs in existing code, one batch of files at a time. Produces only a daily report | `sweep.yaml` |
| `learn` | Learn (学习) | Distills knowledge from past runs and writes it back to the project pack | `learn.yaml` |
| `eval` | Eval (评测) | Replays eval cases and scores the reviewers | `eval.yaml` |
| `onboard` | Onboard project (接入项目) | Analyzes the repository and proposes changes to the project pack | `onboard.yaml` |

- One flow per role, shared by all projects (mdm, blog and myorch alike). The fragments are in `flows/lib/`: `sync-base`, `review-loop`, `e2e`, `deliver`, `choose-proposals`.
- The differences between projects live in the project pack. You do not choose them when you start a run. `flows.development` in `project.yaml` sets the review strength, what happens when the loop runs out of rounds, and whether there is a takeaway and an `incident` input. `prompts/<slot>.md` holds project clauses that are inserted into prompts. Whether the project has Jira or an e2e image decides the ticket input, `fetch-ticket` and the end-to-end tests. The flows use `if:` to trim themselves per project. See [7. Project packs · Project settings for the shared flows](07-projects.md#project-settings-for-the-shared-flows-flows).
- From 2026-11-03 to 2026-11-10 the software development flows were copied per project (`ck-`, `blog-`, `mo-`). After that they were merged back into one set. Old run IDs still carry the old names (e.g. `ck-development-1f29e33a`). They are still found by role, and you can still continue them.
- `role:` at the top of a flow file sets its role. myorch identifies flows by role. `projects:` can restrict a flow to certain projects. None of the current flows set it.
- The **Flow definitions** side bar (流程定义) in the console lists all flow definitions and fragments, grouped by `projects:` (all are in the **General** group (通用) now). A flow opens in a tab, where you can pick a project and inputs to preview the expanded steps.
- For the syntax of flow files, see [8. Flow syntax](08-flow-syntax.md). After changing a flow, regenerate the Flow manual: `uv run --with markdown python docs/workflows/build.py`.
