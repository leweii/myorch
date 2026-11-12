# 8. Writing flows: YAML syntax

This chapter covers how to write flow files. For the steps of each flow, see the [Flow manual](workflows/README.md). For the design reasons, see ARCHITECTURE.md §3.

After changing a flow, first look at the expanded result with `uv run myorch expand <flow> --project <p>`, then try it with a small requirement. The final authority for the fields is the pydantic models in `src/myorch/core/flow.py`.

## How a flow file becomes a run

<!-- diagram: flow-expansion.html -->
Diagram file: [diagrams/en/flow-expansion.html](../diagrams/en/flow-expansion.html).

When a run starts (the same for the CLI, the console, Slack and scheduled jobs), `flowfile.read_flow_file` processes the flow file in this order:

1. **presets**: picks a set of values by the input named in `presets.by`, replaces `{{preset.x}}` in `steps`, then removes `presets`.
2. **Per-project trimming** (`apply_project_if` in `core/fragment.py`): the flow's own steps and `use:` entries whose `if:` is false are removed, inputs whose `if:` is false become `hidden`, `required` expressions are evaluated, and `{{project.x}}` in `with:` is filled in. See "Per-project trimming: `if:`".
3. **Fragments**: each `use:` entry is replaced by the steps or loop in the fragment, `{{params.x}}` is replaced, and steps are removed according to the `if:` in the fragment (which only looks at `params`).
4. **Project**: `{{project.x}}` in the whole file is replaced with the configuration of `--project` (`project.yaml` merged with its template, plus `prompts/`). If the flow uses `{{project.x}}` or flow-level `if:` but no project is given, this is an error.
5. **Validation**: fields, IDs, dependencies and expressions are validated against the `Flow` model. If there is an error, nothing is submitted.

What is submitted to Temporal is the expanded YAML. Other `{{...}}` (`inputs`, `steps`, `run`, `loop`, etc.) are replaced at run time. See "Template variables".

## Top-level structure

```yaml
flow: development                 # flow name, also the prefix of run_id
role: development                 # development / review / postmortem / learn / sweep / eval / onboard
projects: [mdm]                   # optional: the projects that can use it. Leave out for all projects (none of the current flows set it). With the wrong project the run does not start
label: Development                # optional: the name on the console's task button. Defaults to the role's name
hint: From requirement to PR...   # optional: one line of description under the task in the console. Defaults to the role's description
inputs: {task: string, ticket: string}   # short form: name → string. The full form is also allowed, see "Inputs"
require_one_of: [[commit, bad]]   # optional: at least one input in each group must be filled
defaults: {ticket: ""}            # default values of inputs (strings)
image: "{{project.image}}"        # default image
model: claude-sonnet-5-5          # default model for agent steps
on_failed: ask                    # default failure handling for steps: fail (default) | ask. skip can only be set on a step
presets: {...}                    # optional: pick a set of parameters by an input, see "presets"
steps: [...]                      # steps and loops
```

- One flow per role, shared by all projects. The differences between projects are in the project pack (`flows:` settings, `prompts/`). Flows read them with `{{project.x}}` and `if:`. See "Per-project trimming: `if:`" and [7. Project packs](07-projects.md#project-settings-for-the-shared-flows-flows).
- myorch identifies flows by `role`, not by name. Without `role`, a flow also counts as a role if its name is the role name (`development`), or is the role name after removing a prefix (runs like `ck-development` before 2026-11-10).
- When looking up the flow for a role (e.g. for eval replays), myorch first looks for a flow whose `projects` lists the project, and then for one without `projects`.
- The default names and descriptions of roles are in `ROLE_INFO` in `src/myorch/core/inputs.py`.

## Inputs

The full form of an input is below. The console draws the start form from it. The CLI uses `-i name=value`. The console does not recognize inputs by name. It only looks at the spec.

```yaml
inputs:
  ticket:
    if: "project.jira.url"        # optional: only for projects where this expression is true. In other projects it becomes hidden and keeps its default value
    type: ticket                  # string (default) | text | select | ticket | run | message | hidden
    label: Jira ticket            # the name on the form
    hint: e.g. 18700 or MD-18700  # placeholder in the input box
    prefix: "{{project.jira.key}}-"   # ticket: this prefix is removed, so MD-18700 and 18700 are both stored as 18700. The spec can use {{project.x}}
    required: true                # required. The run does not start without it. Can also be an expression on project, see below
    default: ""                   # written into defaults. If defaults has its own value with the same name, defaults wins
  from_run:
    type: run                     # pick from this project's finished runs
    role: development             # which role's runs to pick from
    empty: New branch from master # text shown when nothing is picked
    option: "Continue: {run_id} · {inputs.ticket} · PR #{pr} · {status}"   # text of an option. Split into segments with " · "; a segment whose fields are all empty is not shown
    carry: [ticket]               # when not filled, these inputs are copied from the picked run
    continue:                     # the console shows a "continue" button on finished runs (when the run's role equals role)
      label: Continue on this branch   # button text
      title: The new run starts from this branch's commit   # button tooltip
      ended: This run has ended...     # placeholder in the input box after the run has ended
      note: Continuing on the branch of {run_id}...   # note shown at the top when the form opens
      hint: Describe what to change on top of {run_id}...   # placeholder for the first message
  depth:
    type: select
    options: [{value: quick, label: "quick: 1 reviewer"}, standard]   # strings, or {value, label}
    more: true                    # the console folds it under More options (更多选项): a rarely used input. A required one is shown as usual
  task:
    type: message                 # not drawn as a field: the first message fills it, and the original text is also saved to docs/external/request.md
    from_message: first_line      # first_line: only the first line (at most 120 characters). Default text: the whole text
    label: Requirement
    required: "not project.jira.url"   # string: evaluated on project. Required for projects without Jira; with Jira the ticket is the requirement
  notes:
    type: text                    # multi-line text box
  runs:
    type: hidden                  # not drawn: filled in automatically by the CLI or the console
```

Rules (`src/myorch/core/inputs.py`, enforced by the console, the CLI and the relay):

- `required` and `require_one_of` are checked before the run starts.
- If an input has no default value and no value was passed, the run does not start (`missing flow inputs`).
- A flow only recognizes its first `type: message` input. If the caller has already given a value, the first message does not overwrite it.
- The project's `required_inputs` makes certain inputs required in addition. It applies only to flows that declare the input (see [7. Project packs](07-projects.md)).
- Inputs are resolved per project (`resolve_inputs` in `core/fragment.py`, `flowfile.flow_inputs(path, project)`): an input whose `if:` is false becomes `{type: hidden, default: <the original default>}`. It is not shown on the form, but it is still a declared input, so `{{inputs.x}}` in prompts still renders. A `required` given as a string is evaluated to true or false. `{{project.x}}` in the spec is filled in (e.g. the ticket prefix comes from the project's Jira key). The CLI, the relay, the console (the flows API returns `inputs_for` per project), the Slack form and run tags all use the spec resolved per project.
- When no project is given: inputs with `if:` count as hidden, a `required` given as a string counts as not required, and the spec stays as it is.

### Tags

Each run has a set of tags, and the run list can be searched by tag. Tags are computed at start and recorded in the mandate:

- Tags given with `myorch run --label MD-18700` (can be given several times).
- Inputs with `type: ticket`: the `prefix` is added (resolved for the run's project), e.g. `18700` becomes `MD-18700`. `ck-development` runs from before 2026-11-10 use the spec of `development.yaml`.
- Inputs of type `string` (and inputs the flow does not declare): Jira numbers written in them, e.g. `tickets: "MD-1,MD-2"`. Only the uppercase `ABC-123` form counts. Things like `UTF-8` and `SHA-256` do not.
- Inputs of type `message`, `text`, `hidden` and `select` are not scanned.

At most 20 tags, each at most 64 characters. When searching, a plain number finds `<any prefix>-<number>`. You can also search for part of a run ID.

## Steps

```yaml
- id: analyze                     # unique ID: lowercase letters, digits, -; starts with a letter or digit; at most 63 characters
  needs: [sync]                   # dependencies: IDs of top-level steps or loops
  harness: claude-code            # claude-code | script (alias shell)
  profile: dev-read               # permissions, see "Profile". Required when harness is set
  image: myorch-e2e-blog:latest   # optional: an image just for this step
  model: claude-opus-5-5          # optional: the model for this step
  interaction: allowed            # never (default) | allowed: the agent may ask questions
  escalate_when: "outputs.requires_design_decision"   # when this holds but the agent did not ask, it is turned into a question. Requires interaction: allowed
  when: "steps.x.outputs.n > 0"   # when the condition does not hold: skipped (downstream treats it as success)
  fanout: 3                       # 3 replicas in parallel: analyze.x1..x3. 1–8, default 1
  outputs:                        # keys the agent must give in manifest.outputs
    clear: "Is the requirement clear"
  shared: [sweep]                 # read-write project shared directories, mounted at /sandbox/workspace/shared/<name>/
  observe_runs: "{{inputs.runs}}" # mount other runs' workspaces read-only (used by learn)
  knowledge: false                # knowledge sources: when not set, agent steps mount them and script steps do not. true: both mount, false: neither
  on_failed: ask                  # fail | ask | skip. When not set, the flow's on_failed is used
  limits: {timeout: 30m, retries: 1, input_timeout: 24h}
  prompt: |
    Requirement: {{inputs.task}}
```

| Field | Default | Description |
|---|---|---|
| `harness` | — | `claude-code`: an agent. `script` (or `shell`): the prompt runs as a bash script. Only one of this and `action` can be set. If neither is set, there must be an `approval` (a pure approval step) |
| `profile` | — | Required when `harness` is set |
| `image` | — | Priority: the step's `image` → the profile's `image` → the flow's `image` → the harness's own `openshell-claude-code:latest`. An empty string is the same as not set |
| `model` | — | Only for agent steps. Priority: step → profile → flow → Claude Code's own default |
| `when` | always runs | An expression. When false, the step is recorded as `skipped` and downstream runs as usual |
| `fanout` | 1 | Replicas do not know about each other. Downstream sees the manifests of all replicas. `steps.<id>.outputs` in expressions takes only the first replica's |
| `outputs` | `{}` | Name → description. Written into the "output requirements" of the prompt. Downstream conditions, loops and approver counts read only `outputs` |
| `shared` | `[]` | Mounted together with the project's `shared`. Needs a project |
| `observe_runs` | — | Comma-separated run IDs (templates allowed), at most 20, not the run itself. Mounted at `/sandbox/workspace/observed/<run_id>/` |
| `knowledge` | see above | MCP knowledge sources are only given to agent steps |
| `limits.timeout` | `30m` | Time limit for one turn. Durations are written as `30s`, `45m`, `2h`, `7d` |
| `limits.retries` | 0 | How many more times to try after a failure, 0–5 |
| `limits.input_timeout` | `24h` | How long to wait for your reply: agent questions, questions from `on_failed: ask` |

- An agent step's prompt is the instruction to the model. The platform appends other parts after it. See "How the prompt is assembled".
- A script step's prompt is a bash script. `{{...}}` are replaced first. The script writes `/sandbox/out/manifest.json` itself.

### Failures and retries

- On retry, the step's `out/` is cleared and a new session is used. The shared `repo/` and `docs/` are kept.
- Exception: if the previous attempt was interrupted by the platform (the worker lost its heartbeat, the activity was cancelled, the sandbox failed to start), the retry continues the original session and reminds the agent to run `git status` first to see the changes left by the last attempt. A step exceeding its own `limits.timeout` does not count as an interruption and gets a new session (ARCHITECTURE.md section 9).
- If the agent still asks a question in an `interaction: never` step, the step fails at once, without retries.

When the step still fails after the retries are used up, `on_failed` decides:

| Value | What happens |
|---|---|
| `fail` (default) | The step fails. Downstream steps are recorded as `blocked`, and the run fails |
| `ask` | The run enters `waiting_input` and asks you. Reply `1` to start over (new attempt, new session), `2` to skip (recorded as skipped, the rest runs as usual), `3` to stop. Any other text is a hint: one more turn runs in the session of the failed turn, and the agent remembers what it has tried. If it fails again, you are asked again, with no more automatic retries. Waiting longer than `limits.input_timeout` counts as a failure |
| `skip` | The step is recorded as `skipped` with the failure reason in `error`. The following steps run as usual, the run does not fail because of it, and nobody is asked. For optional steps, e.g. development's takeaway |

- development sets `on_failed: ask` at the flow top level.
- At the flow top level only `fail` or `ask` is allowed.
- Host actions (`action:`) do not ask: a failure is a failure. With `on_failed: skip` they are skipped.
- `on_failed: skip` is not allowed inside loops (including `reflect`): `until` would treat a skipped step as done.
- A step skipped after a failure has no outputs. When later steps need its result, write `when` on one of its outputs (e.g. `steps.takeaway.outputs.items != null`), not on `steps.x.status`: skipped and succeeded are both `succeeded` in expressions.
- When a step is skipped after a failure, the events first carry a `step_finished` with `status: failed`, then one with `skipped`. Whoever reads the events, and learn, can see the failure.

## Approvals

Any step can have an `approval`: it first waits for approval, and runs only after it is approved. A step without `harness` and `action` only does the approval and is called a pure approval step.

```yaml
- id: human-review
  needs: [classify-risk]
  approval:
    when: "..."                   # optional: when false, no approval is needed
    count: "min(steps.risk-rules.outputs.available, max(steps.risk-rules.outputs.approvers, steps.classify-risk.outputs.approvers))"
    subject: "{{steps.risk-rules.outputs.head_sha}}"   # fixed when the approval opens. Check it later with {{approvals.human-review.subject}}
    require_initiator: true       # default true: the initiator must be one of the approvers
    changes: {loop: review-fix, rounds: 2}   # optional: approvers can request changes, which send the run back to this loop
    remind_after: 24h             # optional: if nobody acts for this long, an approval_reminder event is sent
    timeout: 7d                   # optional: if nobody acts for this long, this step fails ("approval timed out")
```

| Field | Default | Description |
|---|---|---|
| `when` | always required | An expression |
| `count` | 1 | An integer or an expression: how many different people must approve. 0 means approved automatically. If the expression's result cannot be read as a number, 1 person is used |
| `subject` | — | What is being approved, e.g. a commit SHA. Rendered and fixed when the approval opens |
| `require_initiator` | `true` | Only checked when `count` is greater than 0 |
| `changes.loop` | — | Must be a top-level loop that comes before this approval step (the approval step depends on it directly or indirectly) |
| `changes.rounds` | 2 | At most how many more rounds run after the run is sent back, 1–5 |
| `remind_after`, `timeout` | no reminder, no timeout | Durations |

- Short forms: `approval: required` equals `count: 1`. `approval: none` equals not setting it.
- Reject: the step is recorded as `rejected`, downstream is `blocked`, and the run fails.
- Request changes (only possible when `changes` is configured): the comments are written to `docs/review/round-<next round>/approver.json` (in the reviewer format), and the loop opens up to `rounds` more rounds. All top-level steps after the loop (including this approval step) go back to waiting and run again.
- The approval record (who approved, who rejected, the requested changes, the times) is in `{{approvals.<step>.…}}`, bound to `subject`.

## Host actions

```yaml
- id: apply
  action: project-apply           # only actions registered in runner/actions.py
  args: {scope: learn}            # values can use templates, replaced at run time
```

A host action does not start a sandbox. It runs deterministic code on the worker and does not run code written by agents. Each step instance runs it only once (at most 2 tries at the Temporal level; the action itself is idempotent), with `limits.timeout` as the time limit. The resulting manifest is also written to `out/`, so later steps can read its outputs.

| Action | Arguments | What it does | Used in |
|---|---|---|---|
| `project-apply` | `scope` (`learn` by default, `onboard`, `fix`), `proposals` (default `docs/learn/proposals.json`), `accepted` (default `docs/learn/accepted.json`) | Writes the adopted proposals back to the project pack, commits in `myorch-knowledge`, and re-syncs the snapshot. `scope` limits which files can be changed (`EDITABLE` in `project.py`). It writes only if everything applies cleanly; otherwise it writes nothing. The later steps of this run mount the new snapshot. Each step instance commits only once, so in a loop each round can write back once | learn, onboard, postmortem, development's review loop (`pack-apply`) |
| `postmortem-replay` | `phase` (`before` by default, `after`), `depth` (default `standard`) | Writes postmortem eval cases and starts a separate eval run that replays the change that introduced the problem. `before` replays with the current knowledge. `after` writes the expected findings and replays once more if proposals were adopted | postmortem |
| `run-trail` | `path` (default `docs/takeaway/trail.json`), `clear` (space-separated files) | Writes the run's history so far into the workspace: each step's status, summary, failure reason and retry count, the questions and answers, each review round's blocking issues and CI result, plus the myorch commit and the project pack version. Lines that look like secrets are removed first. The files in `clear` are emptied | development (takeaway) |
| `takeaway-redact` | `files` (space-separated) | Removes lines that look like secrets from these files (`knowledge.SECRET_RULES`). Fails when all files are empty | development (takeaway) |
| `takeaways-learned` | `picked` (default `docs/learn/takeaways.json`) | Records the run_ids of the takeaways learn has read in `myorch-data/learn/<project>/takeaways.json`, so they are skipped next time | learn |

The proposal file (`proposals.json`) is a list. Each entry is `{id, title, changes: [{file, content | edits: [{old, new}] | append}]}`. `file` is the path as seen in the sandbox, e.g. `project/guide/review-guide.md`. Each `old` in `edits` must appear exactly once in the file. `accepted.json` is `{"accepted": [id, ...]}`.

## Loops

```yaml
- loop: review-fix
  needs: [implement]
  when: "..."                     # optional: when false, the whole loop is skipped
  max_rounds: 3                   # 1–10, default 3
  until: "steps.verify.outputs.blocking == 0"   # required
  on_exhausted: ask               # ask (default) | fail | accept
  reflect: {id: reflect, ...}     # optional: reflect after the loop runs out of rounds, before asking you
  steps:
    - {id: review, fanout: 3, ...}
    - {id: verify, needs: [review], ...}
    - {id: fix, needs: [verify], when: "steps.verify.outputs.blocking > 0", ...}
```

<!-- diagram: loop-round.html -->
Diagram file: [diagrams/en/loop-round.html](../diagrams/en/loop-round.html).

How a round runs:

1. When all of the loop's `needs` are done and `when` is true, round 1 starts.
2. The steps in a round run according to their `needs`. Instance IDs: `verify.r2` is round 2, `review.r2.x1` is the first replica in round 2.
3. When all steps of the round have ended:
   - A step failed (or was rejected, or is blocked): the loop fails.
   - `until` is true: the loop succeeds.
   - `max_rounds` not reached yet: the next round starts.
   - `max_rounds` reached: handled by `on_exhausted`. `accept`: counts as success. `fail`: the loop fails. `ask`: if there is a `reflect`, it runs first (`reflect.r<round>`), then you are asked.
4. The `ask` question has three options: run one more round, accept the current result and continue, stop the flow. The `reflect` step's `outputs.recommendation` and `outputs.reason` (the summary if they are missing) are appended to the question. If there is no reply within 24 hours, the loop fails. A failed `reflect` does not affect the run's result.

Dependency rules:

- Steps in the loop body can `need` steps in the same round, or top-level steps outside the loop. They cannot `need` their own loop.
- Steps outside the loop can only `need` the loop ID, and see the results of the last round.
- From round 2 on, each instance can see the manifests of all instances of the previous round.
- `until` and expressions in the loop body look at the steps of the current round.

## Fragments (`use:`)

```yaml
- use: lib/review-loop.yaml
  id: review-fix
  needs: [develop]
  when: "..."
  if: "..."                       # optional: evaluated on project; when false, the whole use entry is dropped, see "Per-project trimming"
  with: {base: "{{project.base}}", reviewers: "{{project.flows.development.reviewers}}"}
```

- A `use` entry can only have `use`, `id`, `needs`, `with`, `when`, and the flow-level `if:`. `id` is required and becomes the ID of the expanded step or loop.
- The path is relative to the directory of the flow file and cannot leave that directory.
- `{{params.x}}` in the fragment is replaced on expansion. When the whole value is `{{params.x}}`, it is replaced with its type (e.g. `fanout: "{{params.reviewers}}"` becomes an integer). When `{{params.x}}` is part of a value, booleans are written as `true`/`false`.
- When a step in a fragment has `if: "params.fix"` and the expression is false, the step is removed, and it is also removed from the `needs` of other steps. When a whole `use` is removed, the places in the flow that `need` it drop it too. `if:` in fragments can only use `params`, not `project`: when a fragment needs to look at the project, the flow passes the project value in through `with:`.
- Parameters are validated strictly: missing required parameters, wrong types, out-of-range values, values not in `enum`, extra parameters, and references to undeclared parameters are all errors before submission.
- `{{project.x}}` in `with:` is filled in before the fragment binds its parameters. When the whole value is `{{project.x}}`, the type is kept, so project values can be passed to `int` and `bool` parameters (`reviewers: "{{project.flows.development.reviewers}}"`, `acceptance: "{{project.e2e.acceptance}}"`), and are validated against the parameter's type, range and `enum`. `{{preset.x}}` is replaced before fragment expansion and can also be passed to `int` and `bool` parameters.
- Limits:
  - Nested fragments are not supported.
  - Step IDs inside a loop fragment are fixed, so the same loop fragment can be used only once in a flow. The ID of a single-step fragment is given by the `use` entry, so it can be used several times.
  - The referring flow's IDs cannot clash with step IDs inside the fragment. That is why the review flow's loop is called `review-round`, not `review`.

Format of a fragment file:

```yaml
fragment: review-loop
description: CI → parallel reviewers + security → verify vote → (optional) fix
params:
  base:      {type: string, required: true}
  reviewers: {type: int, default: 3, min: 1, max: 8}
  on_exhausted: {type: string, default: ask, enum: [ask, fail, accept]}
item:                             # one step (has id) or one loop (has loop). The ID is replaced by the use entry's id
  loop: _
  ...
```

A parameter's `type` is `string`, `int` or `bool`. It can have `required`, `default`, `min`, `max`, `enum`. A required parameter cannot have a default value.

The current fragments (`flows/lib/`), shared by all projects:

| Fragment | What it is | Parameters (defaults) | Used in |
|---|---|---|---|
| `sync-base` | A single step (script, dev-git): clones or fetches the project repository, checks out the latest base, initializes submodules. Uses a bare mirror in the shared directory `git-mirror` to speed up the clone | none | development, sweep, onboard |
| `review-loop` | A loop: `ci` → parallel `review` (fanout) + `security` → `verify` vote → (optional) `fix` → `pack-choose`, `pack-apply`, until there are no blocking issues. Has `reflect`. When a blocking issue is in the project pack, fix proposes project pack changes, `pack-choose` lets you pick, and `pack-apply` writes them back with `project-apply` (scope `fix`). With `fix: false` none of these three steps exist. The prompts of review, verify and fix include the project's `prompts/review.md`, `verify.md`, `fix.md` | `base` (required), `reviewers` (3, 1–8), `max_rounds` (3, 1–10), `security` (true), `fix` (true), `on_exhausted` (ask), `since` (empty: round 1 only looks at changes after this commit), `requirement_doc` (`docs/requirements.md`), `review_model`, `verify_model`, `fix_model` (empty: use the flow's model) | development (parameters from `flows.development`), review, eval |
| `e2e` | A loop: `e2e-smoke` (the project's fixed cases) + `e2e-accept` (the agent writes cases from the acceptance criteria) → (optional) `e2e-fix`. The project decides the tools, see [7. Project packs · e2e/ convention](07-projects.md#e2e-convention-optional) | `image` (required), `profile` (dev-read), `acceptance` (bool, true), `base` (required), `max_rounds` (2, 1–5), `fix` (true), `on_exhausted` (ask), `requirement_doc` (`docs/requirements.md`), `check_model`, `fix_model` | review (`if: project.e2e.image`, `fix: false`, 1 round) |
| `choose-proposals` | A single step (script, can ask questions): lists the proposals in `<dir>/proposals.json` for you to pick by number, writes `<dir>/accepted.json`, outputs `accepted` | `dir` (required), `what` (`修改建议`, "proposed changes") | learn, postmortem, onboard |
| `deliver` | A single step (script, dev-git): after approval, pushes the approved commit, writes the delivery note `docs/delivery.md`, outputs `delivery`. Refuses to push when the branch changed after approval or the current branch is the base branch. Does not call platform APIs and does not open a PR | `approved` (required: the approved commit) | development |

The **Fragments** group (片段) in the console's **Flow definitions** side bar (流程定义) lists all fragments, their parameters and the flows that use them.

## Per-project trimming: `if:`

One set of flows serves all projects. When a flow's shape varies by project, write `if:` in the flow. The expression is evaluated on `project` (the project configuration merged with its template, the same values as `{{project.x}}`). It is evaluated at submission, before fragments are expanded (`apply_project_if` in `core/fragment.py`), and is not visible at run time.

Where `if:` can be written:

| Location | When false |
|---|---|
| The flow's own top-level steps and top-level loops | The item is removed. Steps that depend on it **inherit its `needs`** |
| `use:` entries | The whole entry is removed, as above |
| Inputs in `inputs` | Not removed. It becomes `{type: hidden, default: <the original default>}`, see "Inputs" |

Steps in fragments and steps in loop bodies cannot have an `if:` on `project` (`if:` in fragments only looks at `params`).

```yaml
- id: takeaway-commit
  if: "project.flows.development.takeaway"
  needs: [takeaway-redact]
  ...
- id: risk-rules
  needs: [takeaway-commit]        # projects without takeaway: all four takeaway steps are removed, and risk-rules waits for review-fix instead
```

`needs` is inherited to keep the chain in order: if the step were only removed from `needs`, `risk-rules` would have no dependencies and would run at the very start. When several steps in a row are removed, the search goes up the chain until it finds a step that stays.

- Using flow-level `if:` without `--project` is an error.
- For the expression syntax, see "Expressions". The context only has `project` (all other references are `null`), e.g. `project.jira.url`, `project.e2e.image`, `project.flows.development.incident`. Empty strings, `false` and `null` all count as false.
- `myorch expand <flow> --project <p>` shows the result trimmed for a project.

### Project prompt clauses: `{{project.prompts.<slot>}}`

Project-specific prompt clauses are not written in the flow. They go in `prompts/<slot>.md` in the project pack. See [7. Project packs · Prompt slots](07-projects.md#prompt-slots-prompts). The flow writes `{{project.prompts.<slot>}}` in the prompt. The text is inserted verbatim at submission and is visible in the expanded flow (`.myorch/flow.yaml` in the workspace). The only slots are `develop`, `review`, `verify`, `fix`, `risk` (`PROMPT_SLOTS` in `project.py`). If the project has no such file, the value is an empty string.

Where they are used now: development's `develop` step (slot `develop`) and `classify-risk` (`risk`); the `review`, `verify` and `fix` steps of the review-loop fragment (slots with the same names), so development, review and eval all include them.

## Rules in prompts (`[[rule:]]`)

Some clauses in prompts were added to make up for mistakes models have made, e.g. "list all entry points", "find out why before removing an existing check". As models get stronger, these clauses may no longer be needed and only take up attention. So each such clause is marked, and can be removed on its own in an eval:

```yaml
prompt: |
  ...
  [[rule:entry-points/develop]]
  - When adding validation to a field: list all entry points...
  [[/rule]]
```

- Each marker is on its own line. The id is `<rule>/<place>`. The same rule has one place each in develop, review and verify. Markers cannot be nested.
- On expansion the marker lines are removed and the body is kept as usual. When the rule is in the project's `flows.ablate`, the whole block is removed: `entry-points` removes every place of it, `entry-points/review` removes only one place (`strip_rules` in `core/fragment.py`).
- Why each rule exists is written in `flows/rules/registry.yaml`: which mistake it makes up for, which case and gap number it comes from, which change, the date it was added, and the result of the latest eval with it removed (`checked`). `tests/test_rules.py` checks that the registry and the markers in the flows match one to one.
- Eval: replay the same batch of cases twice, once as usual and once with `myorch run ... -i at=... --set flows.ablate=<rule>` (see [5. CLI reference · run](05-cli.md#run)). Rules whose results do not get worse can be deleted. For those that get worse, record the date, the model and the result in `checked`.
- When changing a prompt, new clauses that make up for weaknesses must also be marked and registered.

## presets

```yaml
inputs: {depth: string}
defaults: {depth: standard}
presets:
  by: depth
  quick:    {reviewers: 1, security: false, review_model: claude-haiku-5-5}
  standard: {reviewers: 2, security: true,  review_model: claude-sonnet-5-5}
steps:
  - use: lib/review-loop.yaml
    id: review-round
    with: {reviewers: "{{preset.reviewers}}"}
```

- `by` names the input used to pick. The value is the input given at start, or `defaults` if none was given. If neither exists, or the value is not the name of a preset, it is an error.
- `{{preset.x}}` is replaced only in `steps`, at submission, before fragment expansion. When the whole value is `{{preset.x}}`, the type is kept.
- Referencing a value the preset does not have is an error.

## Expressions

Used in `when`, `until`, `approval.when`, `approval.count`, `escalate_when`, the `if` in fragments, the flow-level `if`, and an input's `required` given as a string. The syntax is checked at submission.

- References: `steps.<id>.outputs.<key>`, `steps.<id>.status`, `inputs.<name>`, `loop.round`, `run.id`, `run.branch`, `approvals.<step>.<field>`, `project.<field>`, `replica`. `escalate_when` can only use `outputs.<key>`, which refers to the step's own outputs. The `if` in fragments can only use `params.<name>`. The flow-level `if` and an input's `required` can only use `project.<field>`.
- Literals: numbers, `'string'` or `"string"`, `true`, `false`, `null`.
- Operators: `== != < <= > >=`, `and or not`, parentheses, `max() min() len()`. There is no arithmetic, so `-` can appear in step IDs (`steps.risk-rules.outputs.approvers`).
- A reference that does not exist has the value `null`. A size comparison with `null` is always false, so a missing output never satisfies `> 0`. `max` and `min` ignore `null`. `len(null)` is 0.
- `steps.<id>.status`: `succeeded` when the step (all replicas) succeeded or was skipped, `failed` when one failed, otherwise the current status.

## Template variables

| Variable | When replaced | Where it can be used |
|---|---|---|
| `{{preset.x}}` | At submission | Any value in `steps` |
| `{{params.x}}` | At submission (fragment expansion) | Any value in a fragment |
| `{{project.x}}`, `{{project.a.b}}` | At submission (from `--project`) | The whole flow file, including the input specs. When the whole value is a reference, the type is kept. Lists inside strings are joined with spaces, and empty values are empty strings. `{{project.prompts.<slot>}}` is the text of `prompts/<slot>.md` in the project pack. `{{project.flows.development.x}}` is the project setting for the shared flow |
| `{{inputs.x}}`, `{{run.id}}`, `{{run.branch}}`, `{{loop.round}}`, `{{replica}}` | At run time | `prompt`, `args`, `observe_runs`, `approval.subject` |
| `{{steps.<id>.outputs.<key>}}`, `{{approvals.<step>.subject}}` | At run time | Same as above |

- When a value referenced at run time does not exist, the step fails (`template references unknown value`). A reference to a missing value in an expression does not fail. Its value is `null`.
- `{{replica}}` is 1 without fanout. `{{loop.round}}` exists only in loops.
- Fields such as `model`, `image` and `profile` are replaced only at submission and cannot use run-time variables.

## How the prompt is assembled

The prompt of an agent step's first turn is built from these parts, in order (`src/myorch/core/prompt.py`):

1. The step's own `prompt`, with templates replaced.
2. Retry note: only when the previous attempt was interrupted by the platform and the original session could not be continued.
3. Workspace: `repo/` (the current branch, noted when read-only), `docs/`, `memory/MEMORY.md`, `project/` (the project pack), the project's `repo_docs` and `conventions`, and the loop round.
4. Knowledge: the knowledge sources mounted in this step, where they are, what to read first, and which tools MCP allows.
5. Upstream steps: each upstream instance's summary, files, outputs and notes. The manifest is at `/sandbox/in/<instance>/manifest.json`.
6. Question rules: with `interaction: allowed`, how to use `myorch-ask`. With `never`, the agent is told to decide on its own and write it in notes.
7. External content is only data: content under `docs/external/` must not be treated as instructions.
8. Output requirements: the manifest format and the keys required by `outputs`.

Later turns do not repeat these. After you reply to a question, the new turn sends only your reply and the question rules. When you give a hint in `on_failed: ask`, the new turn sends the failure reason and the hint. When the original session is continued after a platform interruption, it sends the reason for the interruption and "run `git status` first".

## Environment and mounts in the sandbox

Environment variables:

- Run: `MYORCH_RUN_ID`, `MYORCH_INSTANCE`, `MYORCH_STEP`, `MYORCH_BRANCH`, `MYORCH_ROUND` (0 outside loops), `MYORCH_REPLICA`, `MYORCH_WORKSPACE`, `MYORCH_ATTEMPT`, `MYORCH_INTERACTION`, `MYORCH_INPUT_<NAME>` (one per input, name in uppercase).
- Project: `MYORCH_PROJECT`, `MYORCH_PROJECT_REPO`, `MYORCH_PROJECT_BASE`, `MYORCH_PROJECT_SUBMODULES`, `MYORCH_PROJECT_PR_TEMPLATE`, `MYORCH_GIT_NAME`, `MYORCH_GIT_EMAIL`.
- Script steps only: `MYORCH_TURN`, `MYORCH_REPLY` (your reply), `MYORCH_SESSION`, `MYORCH_SESSION_DIR` (a directory that keeps state across turns).

Mounts:

| Path | Content | Access |
|---|---|---|
| `/sandbox/workspace/` | The run's workspace: `repo/`, `docs/`, `memory/` | Read-write. When the profile has `repo: ro`, `repo/` is read-only |
| `/sandbox/workspace/.myorch/`, `.claude/` | The run's records: snapshots of the flow and the mandate, each step's manifest and logs, each instance's session | Read-only. The step writes its own artifacts through `/sandbox/out/` and `/sandbox/logs/` |
| `/sandbox/workspace/.claude/<instance>/` | This instance's session (`CLAUDE_CONFIG_DIR`) | Read-write |
| `/sandbox/workspace/project/` | The project pack snapshot, see [7. Project packs · Snapshots](07-projects.md#snapshots) | Read-only |
| `/sandbox/workspace/shared/<name>/` | The project's shared directories (the step's and the project's `shared`) | Read-write |
| `/sandbox/workspace/knowledge/<name>/` | Knowledge source snapshots | Read-only |
| `/sandbox/workspace/observed/<run_id>/` | Workspaces of the runs in `observe_runs` | Read-only |
| `/sandbox/in/<instance>/` | The `out/` of upstream instances | Read-only |
| `/sandbox/out/` | This step's artifacts and manifest | Read-write |
| `/sandbox/bin/` | `myorch-ask`, `myorch-checks`, `myorch-mcp`, `myorch-stream`, `myorch-steps` (reads the manifests and logs of this run's steps in parts) | Read-only |

## Profile

Profiles are in `profiles/<name>.yaml`, with the fields `image`, `policy`, `providers`, `repo` (`rw` or `ro`), `model` (`runner/profiles.py`). `providers` lists roles. The project's `providers` maps the roles to providers in the gateway. `policy: project` uses the project pack's `policy`.

| profile | Code | Credentials (roles) | policy | Used for |
|---|---|---|---|---|
| `dev-git` | read-write | `github`, `artifacts` | the project's | clone, fetch, push the branch, read PRs |
| `dev-write` | read-write | `artifacts` | the project's | implementation, fixes, CI |
| `dev-read` | read-only (`docs/` writable) | none | gateway default | analysis, review, risk verdict, picking proposals |
| `ticket-read` | read-only | `jira` | the project's | reading Jira tickets (development's `fetch-ticket`) |

Agent steps get the model credentials (`claude`) added automatically. The principle: **steps that can reach GitHub do not run code from the repository. Steps that run code have no GitHub credentials.**

## Manifest

At the end of each step, `/sandbox/out/manifest.json` is written:

```json
{"status": "ok", "summary": "One sentence", "files": [{"path": "plan.md", "description": "..."}],
 "outputs": {"blocking": 0}, "notes": "Things downstream should watch out for"}
```

- `status`: the agent writes `ok` or `failed`. `failed` is handled as a failure and is retried.
- `files` lists only files in `/sandbox/out`. Documents written to `docs/` do not need to be listed.
- `step_id` is filled in by the platform. What the agent writes does not count.
- When the agent asks a question with `myorch-ask`, the status is `needs_input`. This takes precedence over the status in the manifest.
- When the agent wrote no manifest, a broken one, or one with `needs_input` but no question, the platform generates a fallback `manifest_missing` that lists the files in `out/`. It is currently treated as success (a non-zero exit code still counts as failure).

## Agent questions

In a step with `interaction: allowed`, the agent calls:

```sh
/sandbox/bin/myorch-ask "Keep compatibility with the old API?" --option "Compatible" --option "Not compatible"
```

and then ends its turn. After you reply, a new sandbox continues the same session with `--resume`. The wait is `limits.input_timeout`. On timeout the step fails.

`escalate_when`: when the agent ends without asking and this expression is true, the platform asks for it. The question is taken from `outputs.question` or `outputs.design_question`, or the summary if neither exists. The options are taken from `outputs.options` or `outputs.design_options`.

With `interaction: never` (default), `myorch-ask` refuses to run, and the prompt tells the agent to make its own assumptions and write them in notes. If the agent still asks, the step fails without retries.
