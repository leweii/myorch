# channel 层：myorch 和各个平台之间的转发层（2026-11-02）

状态：**2026-11-02 定稿，迁移 4 步都已实现。** 要点已写进 ARCHITECTURE.md §14e。

这份稿子取代 [2026-11-02-channels-design.md](2026-11-02-channels-design.md) 里的这几部分：凭证、控制台的位置、进程形态。交付的细节见 [2026-11-02-delivery-interface.md](2026-11-02-delivery-interface.md)。

## channel 是什么

channel 只是一个转发层。它把 myorch 和各个平台（控制台、GitHub、Slack、Jira……）接起来。它不保存运行状态，也不做额外的判断。

```
 控制台（浏览器）─┐                                   ┌─ 命令 ──► Temporal（start / signal）
 GitHub ─────────┼── 平台适配器 ◄──► channel（转发）──┼─ 事件 ◄── myorch-data/events.db（worker 写）
 Slack、Jira ────┘                                   └─ 内容 ◄── run 的工作区（docs/、diff、日志）
```

它只转发三样东西：

| 转发什么 | 方向 | 做法 |
|---|---|---|
| **事件** | myorch → 平台 | 按 seq 读 `events.db`，交给订阅了这个 run 的适配器。适配器决定在自己的平台上怎么呈现 |
| **命令** | 平台 → myorch | 适配器把人的操作变成命令：`start`、`reply`、`approve`、`reject`、`request_changes`、`cancel`。channel 用 `channels.yaml` 的 `identities` 填上「这是谁」，再调 Temporal client |
| **内容** | myorch → 平台 | 适配器要看 run 的文件、diff、日志时，channel 从工作区读出来交给它。内容不复制 |

和某个平台有关的事，都在这个平台的适配器里做：

- 怎么连接：控制台用 HTTP 和 SSE，GitHub 轮询，Slack 用长连接。
- 收谁的消息：例如 GitHub 的允许名单和召唤词（现在的 `channels/triage.py`）。
- 怎么措辞：例如事件写成 GitHub 评论还是 Slack 消息。
- 交付：例如 GitHub 适配器收到 `run_finished` 里的 `delivery` 后开 PR。

## 已经定下的事（用户，2026-11-02）

1. **控制台是一个平台适配器**，和 GitHub、Slack 平级。控制台不再直接查 Temporal、不再直接读 Docker volume，只通过 channel 收事件、发命令、读内容。
2. **通知和写都走 channel。** myorch 的流程和引擎只写事件，不调用任何外部系统。
3. **凭证分两类。**
   - **渠道凭证**：myorch 在平台上的身份，用来和人沟通，例如 Slack bot token、在 GitHub 上发评论的账号。由 channel 管理（原计划用 `channels/secrets.py` 和 macOS Keychain，2026-11-03 随 GitHub 适配器一起删除）。适配器直接连接平台，可以保持长连接。
   - **工作凭证**：做事时用的身份，例如推分支、开 PR。只在 OpenShell gateway 里。适配器要做这类动作时（例如开 PR），起一次性 sandbox（`runner/jobs.py`），由 supervisor 注入。
   - agent 碰不到任何一类凭证。
4. **扩充事件。** 适配器要能只靠事件拼出运行状态，所以新增这些事件（`core/events.py`）：

   | 新事件 | data |
   |---|---|
   | `step_started` | step、instance、attempt、turn、model |
   | `step_finished` | instance、status、summary、outputs、error |
   | `approval_progress` | step、needed、approvers（已批准的人）、rejected_by |
   | `loop_round` | loop、round、status |

   另外删除 `approval` 事件里没用的 `draft_pr_url`。改 workflow 时用 `workflow.patched()` 加版本标记。
5. **CLI 不走 channel。** 它是开发和排错工具，仍然直接调用 Temporal client。
6. **不做的事：** channel 不维护读模型，不做统一的 Gate，不做审计。

## 进程

一个进程：`myorch serve`。里面跑 channel 和启用的平台适配器（控制台在 127.0.0.1:8800）。它取代 `myorch ui` 和 `python -m myorch.channels`（后者已于 2026-11-03 删除）。

## 迁移

改同一批文件的会话不止一个。每一步开始前，先说好谁改哪些文件。

| 步 | 内容 | 改哪些文件 |
|---|---|---|
| 1 | `deliver` 步骤：workflow 只推分支，`run_finished` 带 `delivery`（development workflow related，进行中） | `flows/`、`core/events.py` |
| 2 | 扩充事件 | `core/events.py`、`engines/temporal/workflow.py` |
| 3 | channel：转发事件（SSE 推给控制台）、转发命令、转发内容。`myorch serve`。交付从 `ui/delivery.py` 搬到适配器 | `src/myorch/channels/`、`cli.py` |
| 4 | 控制台改用 channel，不再直接碰 Temporal 和 volume（ui related） | `src/myorch/ui/`、`channels/serve.py` |

## 实现记录（2026-11-02）

- **第 2 步：扩充事件。** `core/events.py` 的 `derive_events(detail=True)` 产生 `step_started`、`step_finished`（包括 skipped）、`approval_progress`（带 `missing`，规则文字由 `core/run.py` 算）、`loop_round`（带 `until`，跑满时带 `question`）、`changes_requested`。workflow 在 signal 里记下被接受的回复，发成 `replied` 事件，所以从 CLI 发的回复也能看到。`run_started` 带上 inputs、发起人和 flow 快照的路径（`.myorch/flow.yaml`），不带整个流程结构。`approval` 事件删除了 `draft_pr_url`，加了 `missing`。
  - 版本标记 `event-detail-v1`：只有打上这个标记以后启动的 run 才发新事件。最近 40 个运行历史用新代码回放，39 个通过。没通过的 `development-javalab8-issue8` 在这次改动之前就回放失败。
  - worker 重启以后，新启动的 run 才会有新事件。
- **第 3 步：channel 转发层。**
  - `channels/relay.py`：`Relay` 转发事件（`events`、`follow`）、命令（`start`、`reply`、`approve`、`reject`、`request_changes`、`cancel`，用 identities 填上是谁）、内容（`list_files`、`read_file`、`step_files`、`changes`）。`runs()` 的状态取自 Temporal，因为从外部 terminate 的 run 不会写 `run_finished`。
  - `channels/http.py`：挂在 `/channel` 的 HTTP 和 SSE 接口，接口列表在文件开头。只接受本机 Host，POST 要带 `X-Myorch-UI` 头。
  - `channels/serve.py` 和 `myorch serve`：一个进程里跑转发层、控制台，以及可选的 GitHub 适配器（`--github`）。GitHub 适配器和 `--github` 已于 2026-11-03 删除。
- **第 4 步：控制台改用 channel**（ui related 会话）。
  - 读：`ui/service.py` 不再直接调用 Temporal client，也不再读 Docker volume。运行状态由 `ui/runview.py` 从 `relay.events` 拼出来，流程结构取自 `relay.read_file(".myorch/flow.yaml")`。运行列表用 `relay.runs()`，文件、diff、步骤产物用 relay 的内容接口。
  - 写：start、reply、approve、reject、request_changes、cancel 都经过 relay，带 `via="web"` 和本机用户。
  - 实时：浏览器订阅 `/channel/events`（SSE）。轮询只作兜底：每 30 秒刷新运行列表。SSE 断开时，或者看的是旧运行时，每 5 秒刷新详情。
  - 交付：`ui/delivery.py` 移到 `channels/delivery.py`，由 `channels/serve.py` 直接启动。状态文件路径不变（`myorch-data/ui/deliveries.json`）。
  - `myorch ui` 现在等于 `myorch serve`。
- **迁移期回退**（旧运行用完后删除）：
  - `run_started` 里没有 `flow_file` 的运行（没有 `event-detail-v1` 标记），控制台仍然用 Temporal query 拼状态。代码只在 `Service._engine_view` 一处。
  - 旧运行缺发起人等字段时，从 volume 里的 mandate 补齐（`_legacy_records`）。
- **还没做：** `relay.start` 从 `cli.py` 导入 `_load_project`、`_flow_inputs`、`_auto_runs`、`_run_records`。以后应该把它们移到一个公共模块。
