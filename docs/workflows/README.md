# myorch Flow manual

This manual covers each flow: what it does, when to use it, what each step does, and when it needs you. The platform itself (services, architecture, commands, project packs, flow syntax) is in the [User manual](../README.md).

Each flow has one chapter. The input table, the step graph and the step details in a chapter are generated from `flows/*.yaml`. They match what `myorch expand` and the console **Flows** page show, so they cannot drift from the flow definition. The rest of the text is written by hand, next to the flow in `flows/<flow>.md`. After you change a flow or its `.md`, regenerate:

```sh
uv run --with markdown python docs/workflows/build.py
```

## One set of flows for every project

Each role has one flow (`flows/development.yaml`, `review.yaml` and so on), shared by all projects. The differences between projects live in the project pack `myorch-knowledge/<project>/`. They take effect when the flow is expanded; you do not pick them when you start a run:

- `flows.development` in `project.yaml`: how heavy review is (`reviewers`, `max_rounds`, `security`, `on_exhausted`), whether to commit a takeaway (`takeaway`), and whether there is an `incident` input (`incident`). When not set, the defaults are: 3, 3, true, `ask`, false, false.
- `prompts/<slot>.md`: the project's own prompt rules. The slots are `develop`, `review`, `verify`, `fix`, `risk`. At expansion the text is appended verbatim to the prompt of the matching step: the template's first, then the project's.
- `jira`: when the project has Jira, the development flow has a ticket input and `fetch-ticket`, and the ticket is the requirement. Without Jira, the first message is the requirement.
- `if:` in a flow drops steps and inputs per project. Dropped inputs do not show in the form.

| Project | `flows.development` | `prompts/` | Jira |
|---|---|---|---|
| mdm (Cookbook, master-data-management) | Defaults, plus `takeaway: true` | `develop.md` and `review.md` from the core-ng template (Gradle dependencies, `*-interface` contracts) | Yes (`MD`) |
| blog (personal blog) | `reviewers: 1`, `max_rounds: 1`, `security: false`, `on_exhausted: accept` | `develop.md`, `review.md` (Jekyll, writing rules, accidental publishing) | No |
| myorch (the platform itself) | Defaults, plus `incident: true` | `develop.md` (incident cause analysis, the platform's design checks, what to do after merging), `risk.md` | No |

- How each project goes through the development flow: see [Differences by project in development](../../flows/development.md#differences-by-project).
- To change how one project works, change its project pack, then run `uv run myorch project sync <project>`. Changing a flow or fragment under `flows/` affects all projects.
- A flow file starts with `role:`. myorch recognizes flows by role, not by name. For example, **Continue on this branch** works for any run of the development role, and eval replay uses the flow of the eval role.
- Between 2026-11-03 and 2026-11-10, flows were copied per project (prefixes `ck-`, `blog-`, `mo-`). They are now merged into one set; the reasons are in [docs/notes/2026-11-10-one-set-of-flows.md](../notes/2026-11-10-one-set-of-flows.md). Old runs (for example `ck-development-…`) can still be viewed and continued.

## Roles

| Role | Task in the console | Purpose | What it writes outside | Flow |
|---|---|---|---|---|
| `development` | Development | From requirement to pushed branch: requirement, implementation, review, approval, push the branch | Pushes the branch after approval. Opens no PR, does not push the base branch | [development](../../flows/development.md) |
| `review` | Review PR | Reviews an existing PR; the verdict shows in the console | Nothing. Only reads the PR | [review](../../flows/review.md) |
| `postmortem` | Postmortem | A problem already got into the code: find the change that introduced it and the layer that should have caught it, improve knowledge, create eval cases | Only the local `myorch-knowledge` | [postmortem](../../flows/postmortem.md) |
| `sweep` | Find bugs | Finds bugs in existing code, one batch of files at a time; produces only a daily report | Nothing | [sweep](../../flows/sweep.md) |
| `learn` | Learn | Builds knowledge from past runs: improves `guide/`, records decisions people made, adds deterministic checks | Only the local `myorch-knowledge` | [learn](../../flows/learn.md) |
| `eval` | Eval | Replays an eval case and scores the reviewers | Nothing | [eval](../../flows/eval.md) |
| `onboard` | Onboard project | Analyzes the repository, trial-runs the checks, proposes changes to the project pack | Only the local `myorch-knowledge` | [onboard](../../flows/onboard.md) |

## Which flow to use when

| Situation | Use |
|---|---|
| There is a requirement to implement | development |
| The change is pushed and your own testing found a problem, or you want to change more on top of it | development, [continue on the same branch](#continue-on-the-same-branch) |
| A PR needs a look | review |
| After a few runs, review keeps reporting the same kind of problem or keeps missing the same kind | learn |
| A new repository is added, or a repository's structure changed | onboard |
| You want to look for bugs in old code regularly | sweep, run nightly with `myorch schedule` |
| You changed a reviewer's prompt or model and want to know if it got better or worse | eval |
| A problem is already fixed and you want to know why it was not caught | postmortem (fix the bug itself with development) |
| The platform itself had a problem (in a run of any project) | development on the myorch project, with `incident` |

## Starting a run

| Where | How |
|---|---|
| Console http://127.0.0.1:8800/ | **+ New run** in the top bar: pick the project, then the task (one button per flow of this project), fill in the fields, and write the request in the first message |
| Slack | `/myorch` opens the start dialog: pick the project, then it lists the project's flows; the fields are the same as in the console. Once the run starts, the bot opens a thread for it in your DMs |
| Local Claude Code session | Start with the channel tool `start`, for example to start development on the myorch project after finding a platform problem. Runs it starts push their events to that session |
| Command line | `uv run myorch run flows/<flow>.yaml --project <project> -i name=value`. For the commands see [User manual · 5. CLI reference](../05-cli.md) |
| Schedule | `uv run myorch schedule add <name> --flow <flow name, for example sweep> --project <project> --cron '…'`, for example a nightly sweep or a weekly learn |

- The fields are defined by the flow file's `inputs`: label, hint, whether required, type. The types are text, message, dropdown, ticket number (`MD-18700` and `18700` both work) and run (picked from this project's finished runs). Each chapter's Inputs table lists them.
- What goes in the message box is also defined by the flow and can differ by project. For development on a project with Jira (mdm), the requirement is in the ticket and the message is an optional note. blog and myorch have no tickets, so the message is the requirement and is required.
- A ticket input, and ticket numbers in inputs, become tags on the run automatically. The run list can be searched by tag.
- Before starting, the client reads the flow, picks parameters by presets, expands fragments, fills in project values, and checks. Mistakes are reported right away and the run does not start. To see the expanded result first: `uv run myorch expand flows/<flow>.yaml --project <project>`.

## Where you step in

A flow stops and waits for you in the places below. The **Waiting on you** list in the console top bar and `uv run myorch list` both list them. The Slack thread has the same buttons. In the step graph, amber cards and the **May ask you** marker show these places.

| When | Console / Slack | Command line |
|---|---|---|
| An agent asks a question | Reply in the conversation. In Slack, click an option button or reply in the thread | `myorch reply <run> <step> "answer"` |
| A loop runs out of rounds and the problem is not solved | Read the reflect step's proposal first, then reply 1 to run one more round, 2 to accept, 3 to stop | `myorch reply <run> <loop> 1` |
| Choosing proposals (learn, onboard, postmortem and others) | Reply with numbers, for example `1,3`, `all`, `none` | `myorch reply <run> choose "1,3"` |
| Approval | First read the **Changes** tab in the right pane (diff, review verdict, risk verdict), then click **Approve**, **Request changes** (write your comments in the input box first) or **Reject**. Slack has three buttons: Approve, Request changes, Reject | `myorch review <run>` to see the material, then `myorch approve`, `myorch changes` or `myorch reject` |
| You do not want it to continue | **Cancel** | `myorch cancel <run>` |
| It failed and the cause is fixed | **Re-run failed steps**, or **Re-run from this step** on a step. The same run continues; succeeded steps keep their results | `myorch resume <run>` |

- A question that waits too long times out and fails. The default is 24 hours.
- An approval is bound to a commit. If the code changes after approval, delivery refuses to push and the run must be redone.
- Request changes sends the run back to the loop before approval (for example `review-fix`) for one more round. Your comments go to the fixing agent.
- A local Claude Code session can answer questions and resume from a failed step. It cannot approve.

## Continue on the same branch

Use this after a development run has finished: your own testing found a problem, or you want to change more on top of this change. Open that run in the console, click **Continue on this branch**, and write what to change this time in the first message. On the command line use `-i from_run=<run ID>`.

- The chosen run must be finished, belong to the same project, and have the development role. If no ticket number is given, the previous one is reused.
- The new run uses the same branch name. The push updates the same branch, so a PR a person opened updates too.
- It starts from the last commit of that branch. If someone added commits to the remote branch, it starts from the latest remote commit. If the two have diverged, it stops and a person must sort it out first.
- The previous run's requirements, design, implementation record, risk verdict, delivery note and last-round review verdict are put in `docs/previous/`. The agent writes on top of them and states separately what changed this time. Earlier runs are in `docs/previous/earlier/`, nested one inside another.
- What people said to the previous run is in `docs/previous/conversation.md`: the agent's questions and the answers, change requests, comments given on approve or reject, and failures and re-runs. The event log is on the host and the sandbox cannot read it, so the host writes this file into the new run when it starts (the same for command line, console and Slack). The agent treats it as human decisions; where it conflicts with assumptions in the previous documents, it wins.
- Implementation, review and approval then proceed as usual. Review round 1 looks only at the commits added this time; checks and the risk verdict look at the whole branch's changes against base. Details are in [Continue in development](../../flows/development.md#continue-from_run).

## Reading the step graph

- Top to bottom is execution order. Steps on the same row run in parallel. When a card does not wait for every step on the row above, the card says "Waits for …" underneath.
- The second line of a card is the executor: agent (model · profile), script (profile), host action (a registered deterministic action that does not run in a sandbox), or approval. The profile sets the sandbox's permissions; see [User manual · 7. Project packs](../07-projects.md).
- Cards with a blue bar on the left are agents. Hatched cards are host actions. Amber marks where you are needed: approval cards and steps marked **May ask you**.
- A dashed outer border marks a conditional step or loop. Hover over **conditional** to see the expression.
- A box is a loop: its title says the maximum number of rounds, when it stops, and what happens when it runs out of rounds. The reflect step at the bottom runs only when the loop is exhausted and it is to ask you; it gives a proposal.
- "×3 parallel" means three instances of the same step (for example 3 reviewers).
- The graph is expanded for mdm with default inputs (mdm's development flow has the most steps). For another project, the project pack may drop some steps or change parameters (for example blog's review has only 1 reviewer), and project values in conditions may differ. Use `myorch expand flows/<flow>.yaml --project <project>` or the console **Flows** page to see the expansion for a given project. Open **Executor, condition and outputs of each step** below the graph to see the full expressions.

## Changing a flow

1. Change `flows/<flow>.yaml` or a fragment. The syntax is in [User manual · 8. Writing flows](../08-flow-syntax.md).
2. Run `uv run myorch expand flows/<flow>.yaml --project <project>` to see the expanded result.
3. Update `flows/<flow>.md` to match, and regenerate this manual.
4. Run `uv run pytest` once. When the worker and console run in compose, `docker compose -f deploy/compose.yaml up -d --build` makes them load the new flow.
