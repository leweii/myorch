# Slack 适配器（2026-11-05）

状态：**代码和单元测试完成，还没有接真实的 Slack。** 安装步骤见 `deploy/README.md` 的「Slack」。

## 用户定的事

- 装在个人 workspace。
- 第一版能做四件事：收通知、回答提问、审批、发起运行。
- 消息私信发起人，每个运行一个线程。

这推翻了 2026-11-02 起「只在控制台交互」的约定：Slack 和控制台一样，是 channel 层的平台适配器（见 [2026-11-02-channel-layer.md](2026-11-02-channel-layer.md)）。

## 怎么连接

- **Socket Mode**：myorch 用 app-level token（`xapp-`）主动连到 Slack，再用 bot token（`xoxb-`）调 Web API。机器不需要开放端口，也不需要隧道。
- 适配器在 `myorch serve` 进程里（compose 的 `ui` 服务），代码在 `src/myorch/channels/slack.py`。两个令牌都设置时才启动。
- 两个令牌是渠道凭证，放在 `deploy/secrets/slack.env`，只有 `ui` 服务读取。worker 和 sandbox 看不到。

## 出站：事件 → Slack

| 事件 | 在 Slack 里 |
|---|---|
| `run_started` | 在发起人的私信里发根消息：流程、项目、运行 ID、需求，以及「在控制台打开」的链接 |
| `step_started`、`step_finished`、`loop_round` | 线程里**一条**进度消息，原地更新，不逐条发 |
| `question` | 问题和选项按钮。线程里直接回复也算回答 |
| `replied` | 把问题消息改成「已回复：……」，去掉按钮（从控制台或 CLI 回复的也一样） |
| `approval` | 风险等级、还差谁、`docs/risk.md` 的「结论」，以及四个按钮：批准、要求修改、驳回、在控制台看 |
| `approval_progress` | 更新审批消息。批准人数够了，或者有人驳回时，去掉按钮 |
| `approval_reminder`、`changes_requested`、`run_resumed`、`pr_opened` | 线程里一条消息 |
| `run_finished` | 结果。有 `delivery` 时写推送了哪个分支和提交（2026-11-06 起 myorch 不开 PR） |

需要人处理的消息（提问、审批、提醒、结束、PR）同时发到私信主界面（`reply_broadcast`），这样会推送通知。进度不推送。这和 `core/events.py` 的原则一致：运行只在开始、需要人、开 PR、结束时说话。

只有发起人在 `channels.yaml` 的 `identities` 里登记了 `slack` 账号时，运行才会进 Slack。适配器第一次启动时从最新的事件开始，不补发历史。

## 入站：Slack → 命令

| 操作 | 命令 |
|---|---|
| 在运行的线程里回复 | `reply`，回答这个运行最近一个还在等的问题 |
| 点选项按钮 | `reply`，内容是这个选项 |
| 点批准、驳回 | `approve`、`reject` |
| 点要求修改，填弹窗 | `request_changes` |
| `/myorch`，或者私信里的「新运行」按钮 | 打开发起弹窗，提交后 `start` |

- **身份**：Slack 用户 ID 按 `channels.yaml` 的 `identities` 对应到人，命令以这个人的名义发出（`via=slack`）。审批人检查、`require_initiator` 照常由 workflow 做。
- **没登记的人**：bot 回复他的 Slack 用户 ID 和登记方法，不执行任何命令。
- **发起弹窗**：先选项目，再选任务（任务列表只列这个项目能用的流程；有不用项目的流程时，项目里多一个「无项目」），弹窗再按这个流程声明的输入项（`core/inputs.py` 的 spec）画出字段，和控制台的「新运行」表单一样。`message` 类型的输入是请求原文。`run` 类型列出这个项目最近的同角色运行。eval 不在列表里（由 postmortem 和 CLI 发起）。

## 状态

`myorch-data/slack/state.json`：事件游标，以及每个运行的私信频道、线程、进度消息、未回答的问题、审批消息。重启以后接着游标继续，不重发。

## 还没做

- 没有接真实的 Slack 测试，只有用假客户端的单元测试（`tests/test_slack.py`）。
- 发起时不能增减知识源，用项目的默认值。
- 一个运行同时有几个问题在等时，线程里的回复只回答最近的一个。其他的用按钮或控制台回答。
