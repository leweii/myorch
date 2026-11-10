# 交付的接口：workflow 交给渠道什么（2026-11-02）

状态：**workflow 一侧已实现**：审批门（提修改意见、提醒、超时）和 `deliver`（`flows/lib/deliver.yaml`）。workflow 一侧由 workflow review 的会话实现。渠道一侧（在 GitHub 上开 PR）由做 channels 的会话实现。

## 分工

- **workflow**（`development.yaml` 的最后一步 `deliver`，片段 `flows/lib/deliver.yaml`）：
  1. 检查当前分支不是 base 分支。
  2. 检查 HEAD 就是审批通过的那个提交。不一致时停止，不推送。
  3. 把这个 run 的分支推送到项目的 git 远端（`git push`，凭证来自项目的 `github` provider 角色）。
  4. 写 `docs/delivery.md`：这次改动的说明，格式见下面。
  5. 不调用任何平台 API：不开 PR、不发评论、不加标记。
- **渠道**：收到 `run_finished` 事件后，按目标系统交付。GitHub 上就是开 PR：
  - PR 标题：`delivery.title`。
  - PR 正文：`delivery.description`，后面接 `delivery.pr_template`（仓库自带的 PR 模板，有时）。
    请求来自本仓库的 issue 时，正文加 `Closes #<issue>`。渠道自己知道请求来自哪里（Mandate 的 `source`）。
  - head = `delivery.branch`，base = `delivery.base`。
  - 幂等：这个分支已经有 PR 时，不再开新的，只把已有 PR 的链接发回对话。
  - 隐藏标记（例如 `<!-- myorch-pr run=… -->`）由渠道自己加，用来认出自己的 PR。
  - 交付失败（推送成功但开 PR 失败）由渠道重试或在对话里说明。run 本身已经结束，不会重跑。

## 事件 `run_finished` 的 `data`

在现有字段（`status`、`failure` 等）之外，run 成功并且执行了 `deliver` 时，多一个 `delivery`：

```json
{
  "status": "succeeded",
  "delivery": {
    "branch": "LAB-5-myorch-development-javalab8-issue8",
    "base": "main",
    "commit": "<审批通过并推送的提交，40 位>",
    "title": "Reject null or blank id in OrderService.pay and cancel",
    "ticket": "5",
    "repo": "leweii/myorch-java-lab",
    "description": "<docs/delivery.md 的全文>",
    "pr_template": "<仓库里 PR 模板的全文，没有时为空字符串>"
  }
}
```

- 需求分析认定不需要改动（`proceed=false`）时，没有 `delivery`，`stopped` 写原因（和现在一样）。
- run 失败、取消、审批被驳回或超时，都没有 `delivery`。
- 旧字段 `pr_url` 不再由 workflow 产生。渠道开了 PR 以后，自己在对话里报告链接。

## `docs/delivery.md` 的格式

```
# {title}
工单：{ticket}（没有工单号时不写这一行）

## 改了什么
{implementation.md 的「改动」部分}

## 怎么验证的
- 检查：{最后一轮 CI 的结论}
- 验收标准：{最后一轮 summary.md 的验收标准表}

## review
{轮数}，阻塞问题 {n} 条，没解决的 {m} 条（列出），Nit {k} 条

## 风险
{risk.md 的「结论」和「请审批人重点检查」}

## 怎么撤回
合并前：放弃分支 {branch}。合并后：revert 这次合并带进来的提交（{base}..{commit}）。
```

完整的需求文档、实现记录、review 记录和风险说明留在 run 里（`myorch review <run>`、控制台）。

## 审批门的新事件（同一批改动，给渠道和控制台）

- `approval` 事件多一个字段 `actions: ["approve", "reject", "changes"]`。`changes` 是「提修改意见」：
  渠道把审批人的文字作为修改意见送回 run（CLI：`myorch changes <run> <step> "<意见>"`）。run 会回到 review 循环再修，然后重新判定风险、重新审批。
- `approval_reminder`：审批等待超过 `remind_after`（development 设 24 小时）时发一次。渠道转给审批人。
- 审批超过 `timeout`（development 设 7 天）还没处理时，这一步结束为 `failed`，`error` 是「审批超时：7d 内没有人处理」。run 结束，`run_finished.data.status` 是 `failed`，`failure` 里有这句话。没有新的状态值。
- 提修改意见后，同一个审批门会再开一次。第二次的 `approval` 事件 key 是 `approve:<step>:1`，第三次是 `approve:<step>:2`，以此类推。`approval_reminder` 的 key 同样带这个序号（`remind:<step>:1`）。第一次不带序号，和以前一样。
- `approval_reminder` 的 `data`：`needed`（需要几人批准）、`approved_by`（已经批准的人）、`waited`（`remind_after` 的值）。

## 渠道一侧（2026-11-02 实现，2026-11-06 删除）

**2026-11-06 起没有渠道一侧了**：开 PR 不是 myorch 要解决的问题，`channels/delivery.py`、`deliveries.json`、控制台的交付记录和「重试开 PR」都已删除。workflow 一侧不变，`run_finished.delivery` 只用来告诉人推送了哪个分支。下面是删除前的做法。

交付在 channel 层里做。代码在 `src/myorch/channels/delivery.py`，由 `myorch serve`（或 `myorch ui`，两者相同）运行。每个进程只有一个交付循环，所以同时只能开一个 `myorch serve`。状态文件仍在 `myorch-data/ui/deliveries.json`。控制台只显示交付记录，「重试开 PR」也调用同一个交付循环。

- 交付循环读 `myorch-data/events.db`。遇到带 `delivery` 的 `run_finished`，就开 PR。没有 `delivery` 的运行不处理，包括还在用 `push-and-pr` 自己开 PR 的运行。所以 `deliver` 上线前后都不会重复开 PR。
- 开 PR 在一次性 sandbox 任务里做（`runner/jobs.py`），用项目的 `github` provider。token 留在 OpenShell 里，channel 和控制台都拿不到。
- 幂等：分支已经有 PR（任何状态）时，只记下链接。已有 PR 的 head 不是 `delivery.commit` 时，不处理，报错。所以 `delivery.commit` 必须是 40 位的完整哈希。
- PR 正文：`delivery.description`，后面接 `delivery.pr_template`，最后是隐藏标记 `<!-- myorch-pr run=<run_id> -->`。`Closes #<issue>` 由控制台以外的渠道按 `source` 加，控制台发起的运行没有 issue。
- 状态记在 `myorch-data/ui/deliveries.json`：读到的 `seq` 和每个运行的交付结果。控制台停着的时候不会丢：下次启动从上次的 `seq` 接着读。失败时自动重试 3 次，每次间隔 60 秒，之后在运行的对话里显示「重试开 PR」按钮。控制台重启打断的交付，下次启动会重做。
- 控制台需要 `delivery` 里有 `branch`、`base`、`commit`、`title`、`description`。`repo` 和 `project` 可选：没有时，从运行的 mandate 找项目，再从项目包取仓库。
- 「提修改意见」：控制台已经留好按钮。它调用 `engines.temporal.client.request_changes(client, run_id, step_id, approver, text)`。这个函数出现之前，按钮不显示。实现时请用这个签名，或者告诉控制台改。
- `approval_reminder` 和 `expired`：控制台目前不读这两个事件。审批在「等你处理」里一直显示。`expired` 之后，运行状态是 failed，对话里显示失败原因。
