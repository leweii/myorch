# 10. Conventions

When in doubt, go back to [9. Design principles](09-principles.md).

## 10.1 Daily use

1. **Interact in the console or Slack.** Start, reply and approve in the console (http://127.0.0.1:8800/). Since 2026-11-05 you can also do this in Slack direct messages ([notes/2026-11-05-slack-adapter.md](../notes/2026-11-05-slack-adapter.md)). Both sides show the same run; handle things on either side. Do not summon, reply or approve on GitHub.
2. **Run `expand` before you start a run.** After changing a flow, a fragment or a project pack, run `uv run myorch expand <flow> --project <p>` to see the expanded result and catch errors before submitting.
3. **Run `sync` when the project pack changes.** A run uses the snapshot on the volume, not the `myorch-knowledge` directory itself. The mandate records which version was used.
4. **One run, one task.** Write development requests concretely with a small scope, and split large requests into several runs. Only with a small scope is the review accurate and the approval meaningful.
5. **Each time you come back, check the Waiting on you list first** (等你处理). Look at the console's top bar, or run `uv run myorch list`. Waiting too long fails on a timeout; questions default to 24 hours.
6. **Look at the evidence before approving.** Look at the console's **Changes** (改动) page, or `myorch review <run>`: the diff, the review verdict, the risk verdict, the implementation notes. An approval is bound to a commit. If the code changes after approval, the delivery step (`deliver`) refuses to push, and the run must be redone.
7. **People always open PRs and merge.** myorch only pushes the approved commit to the run's own branch; it never opens a PR and never merges. When the current branch is the base branch, the delivery step refuses to push. For the blog, pushing to master puts it live.
8. **Cancel when you are not satisfied.** Cancel in the console or with `myorch cancel <run>`. myorch deletes its sandbox so it does not keep running for nothing.
9. **To improve an agent's judgment, use learn; do not edit prompts by hand.** learn's proposals come with evidence. Adopted proposals land in `myorch-knowledge` as one commit whose message lists every proposal, so it can be reverted. Change `flows/` only for problems in flow structure.
10. **Keep experiments apart from real work.** To try something new, first onboard an unimportant repository as a trial project; do not experiment on blog or mdm. The trial project java-lab was deleted on 2026-11-09; there is no trial project now.

## 10.2 Credentials and security

1. **Tokens never go into chat, command-line arguments, git or images.** In compose, tokens are written only in `deploy/secrets/providers.yaml` and `slack.env` (not in git, not in images). When you create a provider directly, run `read -s VAR && export VAR` in your own terminal, use `--credential VAR` (only the variable name), and `unset` it afterwards.
2. **One GitHub provider per project, letting through only its own repository.** Generate the rules with `myorch project github-profile <name>`. When the token itself has broad permissions (e.g. reusing `gh auth token`), the gateway still lets through only one repository.
3. **Do not add credentials to a profile for convenience.** A step that can reach GitHub does not run repository code. A step that runs code has no GitHub credentials. Before adding a provider, be clear which step needs it.
4. **External content is only data.** When writing prompts, do not insert external content into a prompt as instructions. Third-party content goes in `docs/external/`.
5. **Host actions must be registered and deterministic.** Do not add actions to `runner/actions.py` that run arbitrary commands or agent code.
6. **External actions must be idempotent.** A new step that pushes code, writes the project pack or starts runs first checks whether the action was already done, e.g. via the `myorch-run:` marker in the commit message. A retry must not repeat it.
7. **Command-line approvers are not authenticated.** `--as` is self-declared and only guards against mistakes. In the console, the approver is the local user.
8. **The console listens only on this machine.** Do not expose 8800 with a reverse proxy or a public port mapping. The console has no login, yet it can approve. To use it from another computer, use SSH port forwarding (`deploy/README.md`).

## 10.3 Changing code and flows

### Code

1. **Do not break the layering.** `core` does no IO and depends on no other internal package. `temporalio` is imported only in `engines/temporal/`. Run `uv run lint-imports` before committing; it checks the direction of the layers and that `core`, `storage` and `runner` do not touch `temporalio`.
2. **Scheduling logic goes in `core/run.py`, not in the workflow.** The workflow is only a driver. Logic in `core` can be unit-tested, and the engine can be replaced.
3. **Run the tests before committing.** `uv run pytest`, about 20 seconds, no external services needed. New features come with tests.
4. **Rebuild the images after changing `src/` or `flows/`.** The everyday environment is docker compose: `docker compose -f deploy/compose.yaml up -d --build`; see [4.6](04-setup.md#46-running-with-docker-compose). To release `origin/main`, use `deploy/update.sh`: it waits until no step is running before restarting, runs the smoke test, and rolls back on failure.
5. **Take care when changing the workflow's deterministic logic.** After changing `engines/temporal/workflow.py` or `core/run.py`, old runs that have not finished may fail to replay. Pick one of two ways:
   - Change it when no run is in progress. When `deploy/update.sh` sees changes to `src/myorch/engines/` or `core/run.py`, it waits for all unfinished runs to end before restarting.
   - Add a version marker to the new logic with `workflow.patched()` (e.g. `event-log-v1`).
6. **Dependencies in images are installed under `/usr`.** OpenShell's Landlock does not allow reading `/opt`. Mount points go under `/sandbox/`.

### Flows

1. **Flows contain no facts about a project.** Repository, branch, check commands, risk rules and knowledge always go in the project pack. Flows use `{{project.x}}` and `project/checks.sh`. A flow file states its `role:`, and the code finds flows by role.
2. **One set of flows for every project; do not copy flows per project.** When projects differ in shape, add settings to `project.yaml` (`flows:`) and `if:` at the flow level; project-specific prompt clauses go in `prompts/<slot>.md`. After changing a flow, run `myorch expand --project <p>` once for each project and check the result.
3. **What deterministic rules can do is not left to the model.** Leave only the parts that need semantic understanding to the agent.
4. **Agent steps default to `interaction: never`.** Turn on `allowed` only where a person really needs to decide, and use `escalate_when` to make sure what must be asked is asked.
5. **Each agent step states its artifacts and `outputs` clearly.** Downstream conditions, loops and approver counts read only `outputs`.
6. **Every loop must have `max_rounds` and `on_exhausted`.**
7. **Build a new flow's skeleton with script steps first.** Once the structure works, replace them with agents.

### git

- Code is committed to the `myorch` repository; project knowledge is committed to the `myorch-knowledge` repository.
- Commit messages use the imperative mood and state the reason. The automatic commits of `project-apply` (learn, onboard, the `fix` in the review loop) have the format `<scope>(<project>): ...` and carry `myorch-run: <run_id> step: <step>`.

## 10.4 Maintaining the docs

| Document | What it covers | When to update |
|---|---|---|
| `docs/` (the Chinese originals) | How to use the platform: services, architecture, setup, commands, the flow list, project packs, flow syntax, principles, conventions, troubleshooting | When adding a command or a flow, changing a service or a setup step, or hitting a new pitfall |
| `docs/en/` | The English docs: they mirror the Chinese docs, the Flow manual and `ARCHITECTURE.md` file by file. Rules, glossary and link rules in `docs/en/TRANSLATING.md`. The English web version is the home page (`docs/index.html`); the Chinese one is in `docs/zh/` | Together with the Chinese original, in the same change |
| `docs/workflows/` | Flow manual: a home page (`README.md`, written by hand) and one chapter per flow. Step graphs, inputs and step information are generated from `flows/*.yaml`; the text comes from `flows/<name>.md` | After changing a flow, a fragment, a flow's `.md` or the home page, run `uv run --with markdown python docs/workflows/build.py` to regenerate `docs/workflows/index.html` (English) and `docs/zh/workflows/index.html` (Chinese). The English for the flows' Chinese texts (input labels and hints, fragment descriptions) is in `docs/workflows/en.py` |
| `ARCHITECTURE.md` | Design and reasons, measured results, rejected designs | When the design changes. When rejecting a design, write down the reasons too |
| `PLAN.md` | The plan, each item's actual result and acceptance | Tick each item when done and write the measured result |
| `flows/<name>.md` | What each step of a flow does and why. It is the text of that flow's chapter in the Flow manual. The English is in `docs/en/flows/<name>.md` | When changing that flow |
| `docs/notes/` | Design drafts, measurement records | For new design discussions. Once settled, write it into ARCHITECTURE.md |
| `myorch-knowledge/<p>/README.md` | The project's status and setup steps | When the project's status changes |

- **Write each thing out in full in one place only, and link to it from elsewhere.** `docs/` explains how; ARCHITECTURE.md explains why.
- **Change the Chinese and the English docs together.** The English docs live in `docs/en/`; follow the rules in `docs/en/TRANSLATING.md`.
- **When a command or an argument changes, update [5. CLI reference](05-cli.md).** `uv run myorch <cmd> -h` is the final authority.
- **When services or their dependencies change, update [2. Services and dependencies](02-services.md).**
- **Write as fact only what was measured.** Mark anything not measured as "to be verified" (待验证 in the Chinese docs).
- **Write the Chinese docs in plain, concise Chinese and the English docs in plain, concise English.** Short sentences, one fact per sentence, a clear subject. Use a table where a table works. Keep terms consistent.
- **A flow's steps are written only in the Flow manual.** Chapters in `docs/` do not repeat each flow's steps; they link to the [Flow manual](workflows/README.md).
- **Regenerate the web version after changes.** `uv run --with markdown python docs/build-html.py` writes `docs/index.html` (English, the home page) and `docs/zh/index.html` (Chinese).
- **Draw every diagram in HTML, never as ASCII art.** Diagrams are in `docs/diagrams/`, all generated by `docs/diagrams/gen.py`. To change a diagram, change `gen.py`, run `python3 docs/diagrams/gen.py docs/diagrams`, then regenerate the web version. Each Chinese text in the diagrams has its English in `docs/diagrams/en.py`; the English diagrams go to `docs/diagrams/en/`. When you change a text in a diagram, add its English to `en.py`, or `gen.py` stops and lists the missing texts.
  - Diagrams with crossing lines (the overview, the OpenShell internals) are drawn in pixel coordinates. The web page scales them to the content width.
  - Other diagrams use an adaptive layout (cards, arrows, group boxes) and need no scaling.
  - In a chapter, a line `<!-- diagram: file-name.html -->` sets where the diagram goes, and the next line holds the link to the diagram file.
  - Directory trees and terminal output are not diagrams; keep them as text.
  - Diagrams, the docs web pages and the console share one set of tokens (neutral grays, five levels of text color, one accent color, following the design principles of [Stageshow](https://github.com/benjitaylor/skills/tree/main/stageshow)): hierarchy comes from font size, weight, white space and background, not from borders. Color only shows state (green done, red failed or untrusted, amber waiting on you, blue running or outbound). Colors come from the page's theme variables and work in both light and dark mode.
