# channels：对接外部系统的一层（设计草案，2026-11-02）

状态：**已删除（2026-11-03）**。这份设计实现的 GitHub 适配器（`channels/service.py`、`github.py`、`triage.py` 等）已经删除，只作历史记录保留。现在的 channel 层见 [2026-11-02-channel-layer.md](2026-11-02-channel-layer.md)。原状态：第一阶段已实现，2026-11-02 起暂停。人和 myorch 只在本地控制台（`myorch ui`）交互。下文是 channels 的设计，重新启用时以它为起点。intake 已删除，下文提到 intake 的地方都是背景。

## 为什么要这一层

现在 myorch 和外部世界之间只有 intake：一个常驻的 Temporal workflow，每 60 秒在 sandbox 里轮询 GitHub，
研判后启动 run，再轮询 run 的状态，在原 issue 里发通知。问题：

- **只认 GitHub**。Slack、邮件要再写一套；输入、输出、状态散在 intake、runner/jobs.py、CLI 三处。
- **流程引擎在做通信**。轮询、去重、话题订阅、通知措辞都写在 Temporal workflow 里，和流程编排混在一起。
- **没有流式输出**。runner 只在每轮结束后读日志；人只能等结果。
- **反馈不完整**。研判判为 `none`、研判失败时用户收不到任何回复（见 G0 的讨论）。

目标：把"和外部系统打交道"的事集中到本机一层，叫 **channels**。流程引擎只管流程：收到"有消息"，发出"有事件"。

## 边界

```
 Slack ─┐                         ┌──────────────── channels（本机进程 myorch channels）────────────────┐
 GitHub ┼─ channel 适配器 ─► 收件 ─┤ triage（G0）→ conversation ↔ run 绑定 → 命令          事件 → 渲染 ─┼─ channel 适配器 ─► 发件
 Email ─┤                         └──────────────────────────┬──────────────────────────▲────────────┘
 CLI/Web┘                                                    │ start/reply/approve/…     │ run 事件、流式输出
                                                             ▼                           │
                                              引擎（Temporal：FlowWorkflow + worker）── 事件日志（events.db）
```

| 归 channels | 不归 channels |
|---|---|
| 连接外部系统（轮询、websocket、IMAP）、发消息、改消息 | 流程编排、重试、审批规则（仍在 core / 引擎） |
| 识别是谁、该不该理（Gate）、要不要接（研判） | sandbox、凭证注入（仍在 OpenShell） |
| conversation 和 run 的对应关系 | run 的状态本身（仍在 Temporal） |
| 把 run 的事件变成每个渠道合适的消息，包括流式 | 项目知识、检查（仍在项目包） |

## 概念

- **Channel**：一个外部系统的适配器（`slack`、`github`、`email`、`cli`，以后 `web`）。接口：
  - `listen() -> 异步收件流`（轮询、Socket Mode、IMAP IDLE，按渠道选）
  - `send(conversation, Outbound) -> MessageRef`、`edit(MessageRef, Outbound)`（流式和"进度消息"靠编辑）
  - `capabilities`：有没有话题、能不能编辑、能不能按钮、消息长度上限、是否适合流式。
- **Conversation**：渠道里的一个对话位置，统一成 URI：`github://leweii/myorch-java-lab/issues/4`、
  `slack://T123/C456/1730530000.123`（频道 + 话题）、`email://<Message-ID 线程>`、`cli://jakobhe`。
  一个 conversation 属于一个项目，可以订阅多个 run；一个 run 可以有多个 conversation（issue 和它开的 PR）。
- **Inbound / Outbound**：统一的收件、发件。Outbound 的种类：`ack`（收到）、`question`（提问，带选项）、
  `approval`（审批请求，带"改动在哪看"）、`progress`（进度/流式）、`result`（结果）、`declined`（没接，附理由）、`error`。
- **Identity**：把各渠道的账号对到同一个人：`jakobhe` = GitHub `leweii` = Slack `U0…` = `lewei.me@gmail.com`。
  允许名单、Mandate 的发起人和审批人都用这个人，而不是某个渠道的账号。渠道本身验证身份（Slack 用户 id、GitHub 登录），
  比 CLI 的 `--as` 可信。
- **Triage（G0）**：从 intake 搬过来，和渠道无关：Gate（自己的消息？名单内？召唤了？已绑定的 run 在等它？）
  → 固定指令直接解析 → 其余交给 Haiku 研判（无凭证的 sandbox）。顺带落实 G0 讨论的改进：
  - 把原始请求（标题、正文、召唤的那条、话题里最近几条）作为 `docs/external/request.md` 交给 run，Haiku 的一句话只当标题；
  - 召唤后一定有回复：`none` 回"没接：理由，缺什么"；研判失败回"稍后再召唤"并在下一轮重试；
  - 研判不能用工具；数据边界用随机分隔符。

## 和引擎之间的接口

**命令（channels → 引擎）**，用现有的 Temporal client：`start_run(flow, project, inputs, initiator, source=conversation, request)`、
`reply`、`approve`、`reject`（审批人来自 Identity）、`cancel`。run id 仍由"渠道 + 消息 id"决定，重复的消息不会启动两次。

**事件（引擎 → channels）**：现在 intake 每 60 秒查询一次 run 状态再比对出该说什么。改为引擎写事件、channels 读：

- **事件日志** `myorch-data/events.db`（SQLite，单机足够）：`(seq, run_id, time, kind, step, payload)`。
- 写入方：
  - FlowWorkflow 在状态变化时经一个本地 activity 写：`run_started`、`step_started`、`step_finished`、
    `waiting_input`（问题和选项）、`waiting_approval`（人数、规则、草稿 PR 或 `myorch review`）、
    `step_failed_ask`（另一个会话正在加的 `on_failed: ask`）、`pr_opened`、`run_finished`（结果）。
  - runner 在步骤运行中写 `stream`（agent 的输出片段）。
- 读取方：channels 按 seq 增量读（每秒一次，本机 SQLite 很便宜），每个 conversation 记住读到哪；
  `myorch watch <run>`、以后的网页也读同一份日志。
- 好处：引擎不需要知道有哪些渠道；渠道断开重连后从 seq 接着读，不丢不重。

## 流式输出

1. **来源**：runner 在步骤运行时就开始读输出，不再等一轮结束。
   - claude-code 改用 `--output-format stream-json`：得到结构化事件（agent 的文字、调用了什么工具、工具结果），
     比原始日志好读，也能只转发"正在做什么"（例如"运行 checks.sh"、"改 OrderService.java"）。manifest 仍按文件取，不受影响。
   - 脚本步骤：增量读日志文件。
   - 读法：一个只读挂载 volume 的辅助容器 `tail -F` 这个步骤的日志（每个运行中的步骤一个，结束即停），或者读
     viewer 方案 B 同步到 Mac 的目录（如果那边先做）。
2. **按渠道决定怎么呈现**（注意力原则：只在人在看的地方流式）：

| 渠道 | 呈现 |
|---|---|
| CLI（`myorch watch <run>`）、以后的网页 | 完整流式 |
| Slack | 话题里一条"进度"消息，每几秒编辑一次（只放当前步骤和最近几行动作）；提问、审批、结果另发新消息 |
| GitHub | 不流式。只在节点发评论（收到、提问、审批、结果），和现在一样 |
| Email | 不流式。只在需要人做事和有结果时发信 |

## 每个渠道怎么接（都在本机，不需要公网地址）

| 渠道 | 收 | 发 | 备注 |
|---|---|---|---|
| GitHub | 轮询 REST（带 ETag，省配额），或通知 API 只取提到 myorch 的 | issue / PR 评论 | 本机收不到 webhook，所以仍是轮询；延迟约 60 秒 |
| Slack | Socket Mode（websocket，无需公网） | 消息、话题、按钮、编辑 | 审批和选项用按钮，点的人就是审批人 |
| Email | IMAP IDLE | SMTP | 用 Message-ID / In-Reply-To 归到线程 |
| CLI | 本机 socket | 终端 | `myorch watch`、以后 `myorch chat` |

## 配置

`channels.yaml` 取代 `intake.yaml`：

```yaml
identities:                      # 谁是谁
  jakobhe: {github: leweii, slack: U0XXXX, email: lewei.me@gmail.com}
channels:
  github: {poll_interval: 60}    # 看哪些仓库、召唤词、身份仍来自各项目包
  slack:
    conversations:               # 哪个频道属于哪个项目
      "#myorch-blog": blog
      "#mdm-dev": mdm
  email: {address: …, projects: {mdm: "subject 前缀 [mdm]"}}
mode: observe                    # 没召唤的新内容只记录（和现在一样）
assess_model: claude-haiku-5-5
```

## 凭证：要先定的事

现在 GitHub 的轮询和发评论都在一次性 sandbox 里跑，凭证从不离开 OpenShell。channels 是本机常驻进程，要连 Slack
的 websocket、IMAP，每次起 sandbox 不现实。两个选择：

- **A（推荐）**：channels 在本机进程里运行，各渠道的 token 放 macOS Keychain，每个 token 只给最小权限
  （GitHub 只读 issue + 发评论、Slack bot 只在指定频道）。channels 不执行任何外部内容；研判仍在无凭证的 sandbox 里。
  代价：凭证不再全部在 OpenShell 里，"凭证不离开 OpenShell"这条原则要改成"agent 碰不到凭证"。
- **B**：channels 本身跑在一个常驻的 OpenShell sandbox 里，凭证用 provider 注入、出站用 policy 限制。
  代价：要让 sandbox 连本机的 Temporal 和事件库，常驻 sandbox 的生命周期要自己管；复杂得多。

## intake 怎么去掉

1. `core/intake.py` 的 Gate、指令解析、研判决策、通知措辞搬到 `channels/triage.py` 和 `channels/render.py`，测试一起搬。
2. `engines/temporal/intake.py`（IntakeWorkflow）、`runner/jobs.py` 的 POLL / NOTIFY、`intake.yaml`、`myorch intake` 命令删除；
   研判 job（ASSESS）保留，由 channels 调用。
3. intake 的状态（游标、已处理事件、话题订阅）一次性导入 SQLite，或直接从头开始（现在没有在跑的 run）。
4. Mandate 的 `source` 改为 conversation URI；"PR 订阅"改为收到 `pr_opened` 事件时绑定。

## 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1 | 事件日志（引擎写 lifecycle 事件）+ channels 骨架 + GitHub 渠道（和现在的 intake 等价，加上 G0 的改进）；删掉 intake | java-lab：issue 召唤 → 草稿 PR → 审批 → 结果，全程只靠 channels；`none` 有回复 |
| 2 | 流式：runner 运行中读输出（stream-json）、`stream` 事件、`myorch watch <run>` | 终端里实时看到一次 development 的每一步 |
| 3 | Slack 渠道：Socket Mode、话题、按钮审批、进度消息 | 在 Slack 发需求 → 选项和审批用按钮 → 结果 |
| 4 | Email 渠道 | 发邮件下需求、回邮件审批 |
| 5 | 网页（viewer）作为一个渠道，读同一份事件日志 | 和 ARCHITECTURE §12 合并 |

## 待你决定

1. 凭证放哪：A（本机 Keychain，推荐）还是 B（常驻 sandbox）。
2. Slack 用哪个 workspace（公司的还是个人的）；能不能装一个自己的 Slack app（Socket Mode 需要）。
3. Email 用哪个邮箱，是否值得做（排在 Slack 之后）。
4. 事件日志用本机 SQLite（`myorch-data/events.db`）可以吗。
5. 第 1 阶段 GitHub 仍是 60 秒轮询，可以接受吗（本机没有公网地址，收不到 webhook）。
