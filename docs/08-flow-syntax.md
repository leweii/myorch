# 8. 写流程：YAML 语法

这一章讲流程文件的写法。每个流程有哪些步骤，见 [流程手册](workflows/README.md)。设计理由见 ARCHITECTURE.md §3。

改完流程后，先用 `uv run myorch expand <flow> --project <p>` 看展开结果，再用小需求试跑。字段的最终依据是 `src/myorch/core/flow.py` 里的 pydantic 模型。

## 流程文件怎么变成一次运行

<!-- diagram: flow-expansion.html -->
图的文件：[diagrams/flow-expansion.html](diagrams/flow-expansion.html)。

发起运行时（命令行、控制台、Slack、定时任务都一样），`flowfile.read_flow_file` 按顺序处理流程文件：

1. **presets**：按 `presets.by` 指定的输入选一组值，替换 `steps` 里的 `{{preset.x}}`，然后删掉 `presets`。
2. **按项目裁剪**（`core/fragment.py` 的 `apply_project_if`）：flow 自己的步骤和 `use:` 条目上的 `if:` 为假时去掉，输入上的 `if:` 为假时改成 `hidden`，`required` 表达式求值，`with:` 里的 `{{project.x}}` 填好。见「按项目裁剪：`if:`」。
3. **片段**：把每个 `use:` 条目换成片段里的步骤或循环，替换 `{{params.x}}`，按片段里的 `if:`（只看 `params`）去掉步骤。
4. **项目**：用 `--project` 的配置（`project.yaml` 合并模板后的结果，加上 `prompts/`）替换整个文件里的 `{{project.x}}`。流程用了 `{{project.x}}` 或流程层的 `if:` 却没给项目时，报错。
5. **校验**：按 `Flow` 模型校验字段、ID、依赖和表达式。有错就不提交。

提交给 Temporal 的是展开后的 YAML。其他 `{{...}}`（`inputs`、`steps`、`run`、`loop` 等）留到运行时替换，见「模板变量」。

## 顶层结构

```yaml
flow: development                 # 流程名，也是 run_id 的前缀
role: development                 # development / review / postmortem / learn / sweep / eval / onboard
projects: [mdm]                   # 可选：能用它的项目。不写表示所有项目（现有的流程都不写）。用错项目时运行不会启动
label: 开发                        # 可选：控制台任务按钮上的名字。不写时用角色的名字
hint: 从需求到 PR……               # 可选：控制台任务下面的一行说明。不写时用角色的说明
inputs: {task: string, ticket: string}   # 简写：名字 → string。也可以写完整形式，见「输入」
require_one_of: [[commit, bad]]   # 可选：每组输入至少填一项
defaults: {ticket: ""}            # 输入的默认值（字符串）
image: "{{project.image}}"        # 默认镜像
model: claude-sonnet-5-5          # agent 步骤的默认模型
on_failed: ask                    # 步骤的默认失败处理：fail（默认）| ask。skip 只能写在步骤上
presets: {...}                    # 可选：按某个输入选一组参数，见「presets」
steps: [...]                      # 步骤和循环
```

- 一个角色一个流程，所有项目共用。项目之间的差异写在项目包里（`flows:` 设置、`prompts/`），流程用 `{{project.x}}` 和 `if:` 读它们，见「按项目裁剪：`if:`」和 [7. 项目包](07-projects.md#共用流程的项目设置flows)。
- myorch 按 `role` 认流程，不按名字。没写 `role` 时，名字本身是角色名（`development`），或者去掉前缀后是角色名（2026-11-10 之前的 `ck-development` 这类运行），也算这个角色。
- 找某个角色的流程时（例如 eval 回放），先找 `projects` 里列了这个项目的，没有再找没写 `projects` 的。
- 角色的默认名字和说明在 `src/myorch/core/inputs.py` 的 `ROLE_INFO`。

## 输入

输入的完整形式如下。控制台照它画发起表单，命令行用 `-i 名字=值`。控制台不按名字认输入，只看 spec。

```yaml
inputs:
  ticket:
    if: "project.jira.url"        # 可选：只给这个表达式为真的项目。其他项目里变成 hidden，保留默认值
    type: ticket                  # string（默认）| text | select | ticket | run | message | hidden
    label: Jira 工单               # 表单上的名字
    hint: 例如 18700 或 MD-18700    # 输入框里的提示
    prefix: "{{project.jira.key}}-"   # ticket：去掉这个前缀，MD-18700 和 18700 都存成 18700。spec 里可以写 {{project.x}}
    required: true                # 必填。不填时运行不启动。也可以是对 project 的表达式，见下
    default: ""                   # 写进 defaults。defaults 里另写了同名值时，以 defaults 为准
  from_run:
    type: run                     # 从这个项目已结束的运行里选
    role: development             # 选哪个角色的运行
    empty: 从 master 新建分支       # 不选时显示的文字
    option: "接着改：{run_id} · {inputs.ticket} · PR #{pr} · {status}"   # 选项的文字。用 " · " 分段，字段都为空的段不显示
    carry: [ticket]               # 没填时，从选中的运行复制这些输入
    continue:                     # 控制台在已结束的运行上显示「接着改」按钮（运行的角色等于 role 时）
      label: 在这个分支上接着改     # 按钮文字
      title: 新运行从这个分支的提交开始   # 按钮的提示
      ended: 这个运行已经结束……     # 运行结束后输入框里的提示
      note: 在 {run_id} 的分支上接着改……  # 打开表单时显示在上面的说明
      hint: 写这次要在 {run_id} 的基础上改什么……   # 第一条消息的提示
  depth:
    type: select
    options: [{value: quick, label: "quick：1 个 reviewer"}, standard]   # 字符串，或 {value, label}
    more: true                    # 控制台把它收进「更多选项」：不常用的输入。必填的照常显示
  task:
    type: message                 # 不画成字段：第一条消息填进来，原文另存 docs/external/request.md
    from_message: first_line      # first_line：只取第一行（最多 120 个字）。默认 text：取全文
    label: 需求
    required: "not project.jira.url"   # 字符串：对 project 求值。没有 Jira 的项目必填，有 Jira 时工单就是需求
  notes:
    type: text                    # 多行文本框
  runs:
    type: hidden                  # 不画：由 CLI 或控制台自动填
```

规则（`src/myorch/core/inputs.py`，控制台、CLI 和 relay 都执行）：

- `required` 和 `require_one_of` 在运行启动前检查。
- 没有默认值、也没传值的输入，运行不启动（`missing flow inputs`）。
- 一个流程只认第一个 `type: message` 的输入。调用方已经给了值时，第一条消息不覆盖它。
- 项目的 `required_inputs` 另外要求某些输入必填，只对声明了这个输入的流程生效（见 [7. 项目包](07-projects.md)）。
- 输入按项目解析（`core/fragment.py` 的 `resolve_inputs`，`flowfile.flow_inputs(path, project)`）：`if:` 为假的输入变成 `{type: hidden, default: <原来的默认值>}`，表单里不显示，但仍是声明过的输入，prompt 里的 `{{inputs.x}}` 照样能渲染；字符串形式的 `required` 求值成真假；spec 里的 `{{project.x}}` 填好（例如工单前缀来自项目的 Jira key）。CLI、relay、控制台（流程接口按项目返回 `inputs_for`）、Slack 表单和运行标签都用按项目解析后的 spec。
- 没给项目时：带 `if:` 的输入算 hidden，字符串形式的 `required` 算不必填，spec 照原样。

### 标签

每个运行带一组标签，运行列表可以按标签搜索。标签在启动时算好，记在 mandate 里：

- `myorch run --label MD-18700` 给的标签（可以写多次）。
- `type: ticket` 的输入：加上 `prefix`（按运行的项目解析），例如 `18700` 变成 `MD-18700`。2026-11-10 之前的 `ck-development` 运行按 `development.yaml` 的 spec 算。
- `string` 类型的输入（以及流程没有声明的输入）：里面写的 Jira 号，例如 `tickets: "MD-1,MD-2"`。只认大写的 `ABC-123` 形式，`UTF-8`、`SHA-256` 这类不算。
- `message`、`text`、`hidden`、`select` 类型的输入不扫描。

最多 20 个标签，每个最多 64 个字符。搜索时，纯数字能找到 `<任意前缀>-<数字>`，也可以搜运行 ID 的一部分。

## 步骤

```yaml
- id: analyze                     # 唯一 ID：小写字母、数字、-，以字母或数字开头，最多 63 个字符
  needs: [sync]                   # 依赖：顶层步骤或循环的 ID
  harness: claude-code            # claude-code | script（别名 shell）
  profile: dev-read               # 权限，见「Profile」。写了 harness 就必须写
  image: myorch-e2e-blog:latest   # 可选：这一步专用的镜像
  model: claude-opus-5-5          # 可选：这一步的模型
  interaction: allowed            # never（默认）| allowed：允许 agent 提问
  escalate_when: "outputs.requires_design_decision"   # 条件成立但 agent 没提问时，强制转为提问。要求 interaction: allowed
  when: "steps.x.outputs.n > 0"   # 条件不满足时 skipped（下游视同成功）
  fanout: 3                       # 并行 3 个副本：analyze.x1..x3。1–8，默认 1
  outputs:                        # 要求 agent 在 manifest.outputs 里给出的键
    clear: "需求是否清楚"
  shared: [sweep]                 # 可读写的项目共享目录，挂在 /sandbox/workspace/shared/<名字>/
  observe_runs: "{{inputs.runs}}" # 只读挂载其他运行的工作区（learn 用）
  knowledge: false                # 知识源：不写时 agent 步骤挂、脚本步骤不挂。true 都挂，false 都不挂
  on_failed: ask                  # fail | ask | skip。不写时用 flow 的 on_failed
  limits: {timeout: 30m, retries: 1, input_timeout: 24h}
  prompt: |
    需求：{{inputs.task}}
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `harness` | — | `claude-code`：agent。`script`（或 `shell`）：prompt 当 bash 脚本运行。和 `action` 只能写一个。两个都不写时必须有 `approval`（纯审批步骤） |
| `profile` | — | 写了 `harness` 就必须写 |
| `image` | — | 优先级：步骤的 `image` → profile 的 `image` → flow 的 `image` → harness 自带的 `openshell-claude-code:latest`。空字符串等于不写 |
| `model` | — | 只对 agent 步骤有效。优先级：步骤 → profile → flow → Claude Code 自己的默认 |
| `when` | 总是执行 | 表达式。为假时步骤记为 `skipped`，下游照常执行 |
| `fanout` | 1 | 副本之间互不知道。下游看到所有副本的 manifest。表达式里的 `steps.<id>.outputs` 只取第一个副本的 |
| `outputs` | `{}` | 名字 → 说明。写进 prompt 的「输出要求」。下游的条件、循环、审批人数只读 `outputs` |
| `shared` | `[]` | 和项目的 `shared` 合在一起挂。需要项目 |
| `observe_runs` | — | 逗号分隔的运行 ID（可以用模板），最多 20 个，不能是自己。挂在 `/sandbox/workspace/observed/<run_id>/` |
| `knowledge` | 见上 | MCP 知识源只给 agent 步骤 |
| `limits.timeout` | `30m` | 一轮的时间上限。时长写成 `30s`、`45m`、`2h`、`7d` |
| `limits.retries` | 0 | 失败后再试几次，0–5 |
| `limits.input_timeout` | `24h` | 等你回复的时间：agent 提问、`on_failed: ask` 的问题 |

- agent 步骤的 prompt 是给模型的指令。平台在后面追加其他部分，见「prompt 怎么组成」。
- script 步骤的 prompt 是 bash 脚本，`{{...}}` 先替换。脚本自己写 `/sandbox/out/manifest.json`。

### 失败和重试

- 重试时清空本步骤的 `out/`，换新会话。共享的 `repo/`、`docs/` 保留。
- 例外：上一次是被平台中断的（worker 丢了心跳、activity 被取消、sandbox 起不来），重试接着原来的会话，并提醒 agent 先 `git status` 看清楚上次留下的改动。步骤自己超过 `limits.timeout` 不算中断，换新会话（ARCHITECTURE.md 第 9 节）。
- `interaction: never` 的步骤里 agent 仍然提问时，步骤直接失败，不重试。

重试用完后仍失败时，看 `on_failed`：

| 值 | 做什么 |
|---|---|
| `fail`（默认） | 步骤失败。下游步骤记为 `blocked`，运行失败 |
| `ask` | 运行进入 `waiting_input` 问你。回复 `1` 重新开始（新 attempt、新会话），`2` 跳过（记为 skipped，后面照常），`3` 终止。其他文字当作提示：接着失败那一轮的会话再跑一轮，agent 记得自己试过什么。之后再失败会再问你，不再自动重试。等待超过 `limits.input_timeout` 按失败处理 |
| `skip` | 步骤记为 `skipped`，`error` 里写明失败原因，后面的步骤照常执行，运行不会因此失败，也不问人。用于可有可无的步骤，例如 development 的 takeaway |

- development 在 flow 顶层写了 `on_failed: ask`。
- flow 顶层只能写 `fail` 或 `ask`。
- 宿主机动作（`action:`）不问人：失败就失败，写了 `on_failed: skip` 时跳过。
- `on_failed: skip` 不能写在循环里（包括 `reflect`）：跳过的步骤会被 `until` 当成完成。
- 失败后跳过的步骤没有 outputs。后面的步骤要用它的结果时，`when` 写成依赖它的某个输出（例如 `steps.takeaway.outputs.items != null`），不要用 `steps.x.status`：跳过和成功在表达式里都是 `succeeded`。
- 失败后跳过时，事件里先发一条 `status: failed` 的 `step_finished`，再发 `skipped` 的。看事件的人和 learn 都能看到失败。

## 审批

任何步骤都可以带 `approval`：先等审批，通过后再执行这一步。没有 `harness` 和 `action` 的步骤只审批，叫纯审批步骤。

```yaml
- id: human-review
  needs: [classify-risk]
  approval:
    when: "..."                   # 可选：为假时不需要审批
    count: "min(steps.risk-rules.outputs.available, max(steps.risk-rules.outputs.approvers, steps.classify-risk.outputs.approvers))"
    subject: "{{steps.risk-rules.outputs.head_sha}}"   # 审批打开时固定。后面用 {{approvals.human-review.subject}} 核对
    require_initiator: true       # 默认 true：发起人必须是审批人之一
    changes: {loop: review-fix, rounds: 2}   # 可选：审批人可以提修改意见，把运行送回这个循环
    remind_after: 24h             # 可选：这么久没人处理，发一条 approval_reminder 事件
    timeout: 7d                   # 可选：这么久没人处理，这一步失败（「审批超时」）
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `when` | 总是要 | 表达式 |
| `count` | 1 | 整数或表达式，表示要几个不同的人批准。0 表示自动通过。表达式的结果读不出数字时按 1 人 |
| `subject` | — | 审批的对象，例如提交 SHA。审批打开时渲染并固定 |
| `require_initiator` | `true` | 只在 `count` 大于 0 时检查 |
| `changes.loop` | — | 必须是顶层循环，并且在这个审批步骤之前（审批步骤直接或间接依赖它） |
| `changes.rounds` | 2 | 送回后最多再跑几轮，1–5 |
| `remind_after`、`timeout` | 不提醒、不超时 | 时长 |

- 简写：`approval: required` 等于 `count: 1`。`approval: none` 等于不写。
- 驳回：步骤记为 `rejected`，下游 `blocked`，运行失败。
- 提修改意见（只在配置了 `changes` 时可以）：意见写进 `docs/review/round-<下一轮>/approver.json`（reviewer 的格式），循环再开最多 `rounds` 轮。循环之后的所有顶层步骤（包括这个审批步骤）回到等待状态，重新执行。
- 审批记录（批准人、驳回人、修改意见、时间）在 `{{approvals.<step>.…}}` 里，绑定 `subject`。

## 宿主机动作

```yaml
- id: apply
  action: project-apply           # 只能用 runner/actions.py 里注册的动作
  args: {scope: learn}            # 值可以用模板，运行时替换
```

宿主机动作不起 sandbox，在 worker 上执行确定性的代码，不执行 agent 写的代码。每个步骤实例只执行一次（Temporal 层面最多试 2 次，动作本身是幂等的），时间上限是 `limits.timeout`。结果的 manifest 也写进 `out/`，后面的步骤能读到它的 outputs。

| 动作 | 参数 | 做什么 | 用在 |
|---|---|---|---|
| `project-apply` | `scope`（`learn` 默认、`onboard`、`fix`）、`proposals`（默认 `docs/learn/proposals.json`）、`accepted`（默认 `docs/learn/accepted.json`） | 把采纳的建议写回项目包，在 `myorch-knowledge` 里提交，重新同步快照。`scope` 限制能改的文件（`project.py` 的 `EDITABLE`）。全部能干净地应用才写，否则一个都不写。这次运行后面的步骤挂新快照。每个步骤实例只提交一次，所以循环里每一轮都可以写回一次 | learn、onboard、postmortem、development 的 review 循环（`pack-apply`） |
| `postmortem-replay` | `phase`（`before` 默认、`after`）、`depth`（默认 `standard`） | 写 postmortem 的评测案例，另起一个 eval 运行回放引入问题的改动。`before` 用现在的知识库回放。`after` 写入期望的发现，采纳了建议时再回放一次 | postmortem |
| `run-trail` | `path`（默认 `docs/takeaway/trail.json`）、`clear`（空格分隔的文件） | 把运行到现在的过程写进工作区：每步的状态、摘要、失败原因、重试次数，问答，review 每轮的 blocking 和 CI 结果，加上 myorch 提交和项目包版本。像密钥的行先删掉。`clear` 里的文件清空 | development（takeaway） |
| `takeaway-redact` | `files`（空格分隔） | 删掉这些文件里像密钥的行（`knowledge.SECRET_RULES`）。文件都空时失败 | development（takeaway） |
| `takeaways-learned` | `picked`（默认 `docs/learn/takeaways.json`） | 把 learn 读过的 takeaway 的 run_id 记进 `myorch-data/learn/<项目>/takeaways.json`，下次跳过 | learn |

建议文件（`proposals.json`）是一个列表，每条是 `{id, title, changes: [{file, content | edits: [{old, new}] | append}]}`。`file` 写成 sandbox 里看到的路径，例如 `project/guide/review-guide.md`。`edits` 的每个 `old` 必须在文件里恰好出现一次。`accepted.json` 是 `{"accepted": [id, ...]}`。

## 循环

```yaml
- loop: review-fix
  needs: [implement]
  when: "..."                     # 可选：为假时整个循环 skipped
  max_rounds: 3                   # 1–10，默认 3
  until: "steps.verify.outputs.blocking == 0"   # 必填
  on_exhausted: ask               # ask（默认）| fail | accept
  reflect: {id: reflect, ...}     # 可选：跑满后、问你之前先反思
  steps:
    - {id: review, fanout: 3, ...}
    - {id: verify, needs: [review], ...}
    - {id: fix, needs: [verify], when: "steps.verify.outputs.blocking > 0", ...}
```

<!-- diagram: loop-round.html -->
图的文件：[diagrams/loop-round.html](diagrams/loop-round.html)。

一轮怎么跑：

1. 循环的 `needs` 都完成后，`when` 为真就开始第 1 轮。
2. 一轮里的步骤按 `needs` 执行。实例 ID：`verify.r2` 是第 2 轮，`review.r2.x1` 是第 2 轮的第 1 个副本。
3. 这一轮所有步骤都结束后：
   - 有步骤失败（或被驳回、blocked）：循环失败。
   - `until` 为真：循环成功。
   - 还没到 `max_rounds`：开始下一轮。
   - 到了 `max_rounds`：按 `on_exhausted` 处理。`accept`：当作成功。`fail`：循环失败。`ask`：有 `reflect` 时先跑它（`reflect.r<轮>`），再问你。
4. `ask` 的问题有三个选项：再跑一轮、接受当前结果并继续、终止流程。`reflect` 的 `outputs.recommendation` 和 `outputs.reason`（没有时用摘要）附在问题后面。24 小时没回复，循环失败。`reflect` 失败不影响运行的结果。

依赖规则：

- 循环体里的步骤可以 `needs` 同一轮的步骤，或者循环外的顶层步骤。不能 `needs` 自己所在的循环。
- 循环外的步骤只能 `needs` 循环 ID，看到的是最后一轮的结果。
- 从第 2 轮起，每个实例能看到上一轮所有实例的 manifest。
- `until` 和循环体里的表达式看的是当前这一轮的步骤。

## 片段（`use:`）

```yaml
- use: lib/review-loop.yaml
  id: review-fix
  needs: [develop]
  when: "..."
  if: "..."                       # 可选：对 project 求值，为假时整条 use 不要，见「按项目裁剪」
  with: {base: "{{project.base}}", reviewers: "{{project.flows.development.reviewers}}"}
```

- `use` 条目只能有 `use`、`id`、`needs`、`with`、`when`，以及流程层的 `if:`。`id` 必填，成为展开后的步骤或循环的 ID。
- 路径相对流程文件所在的目录，不能跑到目录外面。
- 片段里的 `{{params.x}}` 在展开时替换。整个值都是 `{{params.x}}` 时按类型替换（例如 `fanout: "{{params.reviewers}}"` 变成整数）。值的一部分是 `{{params.x}}` 时，布尔值写成 `true`/`false`。
- 片段里的步骤写 `if: "params.fix"` 时，表达式为假就去掉这个步骤，其他步骤 `needs` 里的它也一起去掉。整个 `use` 被去掉时，flow 里 `needs` 它的地方也去掉。片段里的 `if:` 只能用 `params`，不能用 `project`：片段要看项目时，由 flow 把项目值经 `with:` 传进来。
- 参数严格校验：缺必填、类型不对、越界、不在 `enum` 里、多传、引用没声明的参数，都在提交前报错。
- `with:` 里的 `{{project.x}}` 在片段绑定参数之前填好。整个值是 `{{project.x}}` 时保留类型，所以项目值可以传给 `int`、`bool` 参数（`reviewers: "{{project.flows.development.reviewers}}"`、`acceptance: "{{project.e2e.acceptance}}"`），按参数的类型、范围、`enum` 校验。`{{preset.x}}` 早于片段展开，同样可以传给 `int`、`bool` 参数。
- 限制：
  - 不支持片段嵌套。
  - 循环片段里的步骤 ID 是固定的，所以同一个循环片段在一个 flow 里只能用一次。单步片段的 ID 由 `use` 条目给，可以用多次。
  - 引用方的 ID 不能和片段内的步骤重名。所以 review 流程的循环叫 `review-round`，不叫 `review`。

片段文件的格式：

```yaml
fragment: review-loop
description: CI → 并行 reviewer + security → verify 投票 →（可选）fix
params:
  base:      {type: string, required: true}
  reviewers: {type: int, default: 3, min: 1, max: 8}
  on_exhausted: {type: string, default: ask, enum: [ask, fail, accept]}
item:                             # 一个步骤（有 id）或一个循环（有 loop）。ID 会被 use 条目的 id 替换
  loop: _
  ...
```

参数的 `type` 是 `string`、`int` 或 `bool`。可以写 `required`、`default`、`min`、`max`、`enum`。必填参数不能有默认值。

现有片段（`flows/lib/`），所有项目共用：

| 片段 | 是什么 | 参数（默认值） | 用在 |
|---|---|---|---|
| `sync-base` | 单步（script，dev-git）：clone 或 fetch 项目仓库，切到最新的 base，初始化子模块。用共享目录 `git-mirror` 里的裸镜像加速 clone | 无 | development、sweep、onboard |
| `review-loop` | 循环：`ci` → 并行 `review`（fanout）+ `security` → `verify` 投票 →（可选）`fix` → `pack-choose`、`pack-apply`，直到没有阻塞问题。带 `reflect`。阻塞问题出在项目包时，fix 提出项目包修改，`pack-choose` 让你挑，`pack-apply` 用 `project-apply`（scope `fix`）写回。`fix: false` 时这三步都没有。review、verify、fix 的 prompt 里拼进项目的 `prompts/review.md`、`verify.md`、`fix.md` | `base`（必填）、`reviewers`（3，1–8）、`max_rounds`（3，1–10）、`security`（true）、`fix`（true）、`on_exhausted`（ask）、`since`（空：第 1 轮只看这个提交之后的改动）、`requirement_doc`（`docs/requirements.md`）、`review_model`、`verify_model`、`fix_model`（空：用 flow 的模型） | development（参数来自 `flows.development`）、review、eval |
| `e2e` | 循环：`e2e-smoke`（项目的固定用例）+ `e2e-accept`（agent 按验收标准写用例）→（可选）`e2e-fix`。工具由项目决定，见 [7. 项目包 · e2e/ 约定](07-projects.md#e2e-约定可选) | `image`（必填）、`profile`（dev-read）、`acceptance`（bool，true）、`base`（必填）、`max_rounds`（2，1–5）、`fix`（true）、`on_exhausted`（ask）、`requirement_doc`（`docs/requirements.md`）、`check_model`、`fix_model` | review（`if: project.e2e.image`，`fix: false`、1 轮） |
| `choose-proposals` | 单步（script，可以提问）：列出 `<dir>/proposals.json` 里的建议让你挑编号，写 `<dir>/accepted.json`，输出 `accepted` | `dir`（必填）、`what`（`修改建议`） | learn、postmortem、onboard |
| `deliver` | 单步（script，dev-git）：审批通过后推送批准的那个提交，写交付说明 `docs/delivery.md`，输出 `delivery`。分支在审批后变了、或者当前分支是 base 分支时拒绝推送。不调用平台 API，不开 PR | `approved`（必填：批准的提交） | development |

控制台侧栏「流程定义」的「片段」组列出所有片段、参数和用到它的流程。

## 按项目裁剪：`if:`

一套流程给所有项目。流程的形状按项目变化时，在 flow 里写 `if:`，表达式对 `project`（合并模板后的项目配置，`{{project.x}}` 的那份值）求值。它在提交时、展开片段之前求值（`core/fragment.py` 的 `apply_project_if`），运行时看不到。

能写 `if:` 的地方：

| 位置 | 为假时 |
|---|---|
| flow 自己的顶层步骤和顶层循环 | 去掉这一项。依赖它的步骤**继承它的 `needs`** |
| `use:` 条目 | 整条去掉，同上 |
| `inputs` 里的输入 | 不删除，变成 `{type: hidden, default: <原来的默认值>}`，见「输入」 |

片段里的步骤、循环体里的步骤不能写对 `project` 的 `if:`（片段里的 `if:` 只看 `params`）。

```yaml
- id: takeaway-commit
  if: "project.flows.development.takeaway"
  needs: [takeaway-redact]
  ...
- id: risk-rules
  needs: [takeaway-commit]        # 没有 takeaway 的项目：takeaway 四步都去掉，risk-rules 改为等 review-fix
```

继承 `needs` 是为了让链条保持顺序：只从 `needs` 里删掉的话，`risk-rules` 就没有依赖，会在一开始就运行。连续去掉几步时一路往上找，直到找到留下来的步骤。

- 用了流程层的 `if:` 却没给 `--project` 时报错。
- 表达式的语法见「表达式」。上下文里只有 `project`（其他引用都是 `null`），例如 `project.jira.url`、`project.e2e.image`、`project.flows.development.incident`。空字符串、`false`、`null` 都算假。
- `myorch expand <flow> --project <p>` 看某个项目裁剪后的结果。

### 项目的 prompt 条款：`{{project.prompts.<slot>}}`

项目专属的 prompt 条款不写在流程里，写在项目包的 `prompts/<slot>.md`，见 [7. 项目包 · prompt 槽位](07-projects.md#prompt-槽位prompts)。流程在 prompt 里写 `{{project.prompts.<slot>}}`，提交时原文拼进去，展开后的流程（工作区的 `.myorch/flow.yaml`）里能看到。槽位只有 `develop`、`review`、`verify`、`fix`、`risk`（`project.py` 的 `PROMPT_SLOTS`）。项目没有这个文件时是空字符串。

现在用到的地方：development 的 `develop` 步骤（槽位 `develop`）和 `classify-risk`（`risk`）；review-loop 片段的 `review`、`verify`、`fix` 步骤（同名槽位），所以 development、review、eval 都会带上。

## prompt 里的规则（`[[rule:]]`）

prompt 里有些条款是为了弥补模型犯过的错加的，例如「列全入口」「删掉已有判断前先查原因」。模型变强以后，这些条款可能已经不需要，只占注意力。所以每条这样的条款都标出来，能在评测里单独去掉：

```yaml
prompt: |
  ...
  [[rule:entry-points/develop]]
  - 要给某个字段加校验时：列全所有入口……
  [[/rule]]
```

- 标记各占一行，id 是 `<规则>/<位置>`，同一条规则在 develop、review、verify 里各有一处。不能嵌套。
- 展开时删掉标记行，正文照常保留。项目的 `flows.ablate` 里有这条规则时，整块删掉：写 `entry-points` 删掉它的每一处，写 `entry-points/review` 只删一处（`core/fragment.py` 的 `strip_rules`）。
- 每条规则为什么存在写在 `flows/rules/registry.yaml`：补的是什么错、来自哪个案例和差距编号、哪次改动、哪天加的，以及最近一次去掉它评测的结果（`checked`）。`tests/test_rules.py` 检查登记表和流程里的标记一一对应。
- 评测：同一批案例重放两遍，一遍照常，一遍 `myorch run ... -i at=... --set flows.ablate=<规则>`（见 [5. 命令参考 · run](05-cli.md#run)）。结果没有变差的规则可以删掉，变差的在 `checked` 里记下日期、模型和结果。
- 改 prompt 时，新加的「补短板」条款也要标出来并登记。

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

- `by` 指定用哪个输入选。值取发起时给的输入，没给时取 `defaults`。都没有、或者值不是某个 preset 的名字时报错。
- `{{preset.x}}` 只在 `steps` 里替换，在提交时替换，早于片段展开。整个值都是 `{{preset.x}}` 时保留类型。
- 引用 preset 里没有的值时报错。

## 表达式

用于 `when`、`until`、`approval.when`、`approval.count`、`escalate_when`，片段的 `if`，以及流程层的 `if` 和字符串形式的输入 `required`。提交时检查语法。

- 引用：`steps.<id>.outputs.<key>`、`steps.<id>.status`、`inputs.<name>`、`loop.round`、`run.id`、`run.branch`、`approvals.<step>.<字段>`、`project.<字段>`、`replica`。`escalate_when` 里只能用 `outputs.<key>`，指本步骤自己的输出。片段的 `if` 里只能用 `params.<name>`，流程层的 `if` 和输入的 `required` 里只能用 `project.<字段>`。
- 字面量：数字、`'字符串'` 或 `"字符串"`、`true`、`false`、`null`。
- 运算：`== != < <= > >=`、`and or not`、括号、`max() min() len()`。没有算术，所以 `-` 可以出现在步骤 ID 里（`steps.risk-rules.outputs.approvers`）。
- 引用不存在时值是 `null`。`null` 参与大小比较时结果一律是 false，所以缺失的输出永远不满足 `> 0`。`max`、`min` 忽略 `null`，`len(null)` 是 0。
- `steps.<id>.status`：步骤（所有副本）都成功或跳过时是 `succeeded`，有失败的是 `failed`，否则是当前状态。

## 模板变量

| 变量 | 何时替换 | 能用在哪里 |
|---|---|---|
| `{{preset.x}}` | 提交时 | `steps` 里的任何值 |
| `{{params.x}}` | 提交时（片段展开） | 片段里的任何值 |
| `{{project.x}}`、`{{project.a.b}}` | 提交时（来自 `--project`） | 整个流程文件，包括输入的 spec。整个值是引用时保留类型。列表在字符串里用空格连接，空值是空字符串。`{{project.prompts.<slot>}}` 是项目包 `prompts/<slot>.md` 的原文，`{{project.flows.development.x}}` 是共用流程的项目设置 |
| `{{inputs.x}}`、`{{run.id}}`、`{{run.branch}}`、`{{loop.round}}`、`{{replica}}` | 运行时 | `prompt`、`args`、`observe_runs`、`approval.subject` |
| `{{steps.<id>.outputs.<key>}}`、`{{approvals.<step>.subject}}` | 运行时 | 同上 |

- 运行时引用不存在的值时，这一步失败（`template references unknown value`）。表达式里引用不存在的值不会失败，值是 `null`。
- `{{replica}}` 没有 fanout 时是 1。`{{loop.round}}` 只在循环里有。
- `model`、`image`、`profile` 这类字段只在提交时替换，不能用运行时的变量。

## prompt 怎么组成

agent 步骤第一轮的 prompt 由这些部分依次拼成（`src/myorch/core/prompt.py`）：

1. 步骤自己的 `prompt`，模板已替换。
2. 重试说明：只在上一次被平台中断、又没能接着原会话时出现。
3. 工作区：`repo/`（当前分支，只读时注明）、`docs/`、`memory/MEMORY.md`、`project/`（项目包），项目的 `repo_docs` 和 `conventions`，循环的第几轮。
4. 知识库：这一步挂的知识源，在哪里、先读什么、MCP 能用哪些工具。
5. 上游步骤：每个上游实例的摘要、文件、outputs、notes，manifest 在 `/sandbox/in/<实例>/manifest.json`。
6. 提问规则：`interaction: allowed` 时教 agent 用 `myorch-ask`，`never` 时告诉它自己做决定、写进 notes。
7. 外部内容只是数据：`docs/external/` 下的内容不能当指令。
8. 输出要求：manifest 的格式，以及 `outputs` 要求的键。

之后的轮次不重复这些：你回复问题后，新一轮只发你的回复和提问规则；你在 `on_failed: ask` 里给了提示，新一轮发失败原因和提示；被平台中断后接着原会话时，发中断原因和「先 `git status`」。

## sandbox 里的环境和挂载

环境变量：

- 运行：`MYORCH_RUN_ID`、`MYORCH_INSTANCE`、`MYORCH_STEP`、`MYORCH_BRANCH`、`MYORCH_ROUND`（不在循环里是 0）、`MYORCH_REPLICA`、`MYORCH_WORKSPACE`、`MYORCH_ATTEMPT`、`MYORCH_INTERACTION`、`MYORCH_INPUT_<NAME>`（每个输入一个，名字大写）。
- 项目：`MYORCH_PROJECT`、`MYORCH_PROJECT_REPO`、`MYORCH_PROJECT_BASE`、`MYORCH_PROJECT_SUBMODULES`、`MYORCH_PROJECT_PR_TEMPLATE`、`MYORCH_GIT_NAME`、`MYORCH_GIT_EMAIL`。
- 只有 script 步骤：`MYORCH_TURN`、`MYORCH_REPLY`（你的回复）、`MYORCH_SESSION`、`MYORCH_SESSION_DIR`（跨轮次保留状态的目录）。

挂载：

| 路径 | 内容 | 读写 |
|---|---|---|
| `/sandbox/workspace/` | 运行的工作区：`repo/`、`docs/`、`memory/` | 读写。profile 是 `repo: ro` 时，`repo/` 只读 |
| `/sandbox/workspace/.myorch/`、`.claude/` | 运行的记录：流程和 mandate 快照、每一步的 manifest 和日志、每个实例的会话 | 只读。自己的产物经 `/sandbox/out/`、`/sandbox/logs/` 写 |
| `/sandbox/workspace/.claude/<实例>/` | 本实例的会话（`CLAUDE_CONFIG_DIR`） | 读写 |
| `/sandbox/workspace/project/` | 项目包快照，见 [7. 项目包 · 快照](07-projects.md#快照) | 只读 |
| `/sandbox/workspace/shared/<名字>/` | 项目的共享目录（步骤和项目的 `shared`） | 读写 |
| `/sandbox/workspace/knowledge/<名字>/` | 知识源快照 | 只读 |
| `/sandbox/workspace/observed/<run_id>/` | `observe_runs` 里的运行的工作区 | 只读 |
| `/sandbox/in/<实例>/` | 上游实例的 `out/` | 只读 |
| `/sandbox/out/` | 本步骤的产物和 manifest | 读写 |
| `/sandbox/bin/` | `myorch-ask`、`myorch-checks`、`myorch-mcp`、`myorch-stream`、`myorch-steps`（分段读这次运行每一步的 manifest 和日志） | 只读 |

## Profile

profile 在 `profiles/<名字>.yaml`，字段有 `image`、`policy`、`providers`、`repo`（`rw` 或 `ro`）、`model`（`runner/profiles.py`）。`providers` 写的是角色，项目的 `providers` 把角色映射到 gateway 里的 provider。`policy: project` 用项目包的 `policy`。

| profile | 代码 | 凭证（角色） | policy | 用于 |
|---|---|---|---|---|
| `dev-git` | 读写 | `github`、`artifacts` | 项目的 | clone、fetch、推送分支、读 PR |
| `dev-write` | 读写 | `artifacts` | 项目的 | 实现、修复、CI |
| `dev-read` | 只读（`docs/` 可写） | 无 | gateway 默认 | 分析、review、风险判定、挑建议 |
| `ticket-read` | 只读 | `jira` | 项目的 | 读 Jira 工单（development 的 `fetch-ticket`） |

agent 步骤会自动加上模型凭证（`claude`）。原则：**能访问 GitHub 的步骤不执行仓库里的代码。执行代码的步骤没有 GitHub 凭证。**

## Manifest

每一步结束时写 `/sandbox/out/manifest.json`：

```json
{"status": "ok", "summary": "一句话", "files": [{"path": "plan.md", "description": "..."}],
 "outputs": {"blocking": 0}, "notes": "下游需要注意的事"}
```

- `status`：agent 写 `ok` 或 `failed`。`failed` 按失败处理，会重试。
- `files` 只列 `/sandbox/out` 里的文件。写到 `docs/` 的文档不用列。
- `step_id` 由平台填，agent 写的不算。
- agent 用 `myorch-ask` 提问时，状态是 `needs_input`。这优先于 manifest 里写的状态。
- agent 没写、写坏、或者写了 `needs_input` 却没带问题时，平台兜底生成 `manifest_missing`，列出 `out/` 里的文件。目前按成功处理（退出码不为 0 时仍算失败）。

## agent 提问

在 `interaction: allowed` 的步骤里，agent 调用：

```sh
/sandbox/bin/myorch-ask "要兼容旧版 API 吗？" --option "兼容" --option "不兼容"
```

然后结束本轮。你回复以后，新 sandbox 用 `--resume` 续接同一个会话。等待时间是 `limits.input_timeout`，超时步骤失败。

`escalate_when`：agent 没提问就结束、这个表达式又为真时，平台替它提问。问题取 `outputs.question` 或 `outputs.design_question`，没有时取摘要。选项取 `outputs.options` 或 `outputs.design_options`。

`interaction: never`（默认）时，`myorch-ask` 拒绝执行，prompt 告诉 agent 自行假设并写进 notes。agent 仍然提问时，步骤失败，不重试。
