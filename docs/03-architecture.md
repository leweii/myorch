# 3. 架构

服务怎么起、谁依赖谁见 [2. 服务和依赖](02-services.md)。完整的设计理由见 [ARCHITECTURE.md](../ARCHITECTURE.md)。

## 技术栈

| 层 | 用什么 | 负责 |
|---|---|---|
| 入口 | Python CLI（argparse）。`myorch serve`：控制台（Starlette + uvicorn，静态 HTML/JS）、Slack 适配器（slack-sdk，Socket Mode）。Claude Code channel（MCP 服务器，aiohttp 访问 HTTP 接口） | 发起、查看、回复、审批 |
| 流程引擎 | Temporal dev server，Python SDK `temporalio` | 持久状态、重试、超时、等待人工信号、取消、定时触发（Schedule） |
| 核心库 | Python 3.12，pydantic、pyyaml | 流程 schema、调度规划、表达式、输入规则、prompt 组装、manifest、事件 |
| 沙箱 | OpenShell 0.1.6（gateway + supervisor），Docker driver | sandbox 生命周期、凭证注入、网络 policy、Landlock 文件系统隔离 |
| 存储 | Docker volume `myorch-artifacts`。宿主机目录 `myorch-data`（SQLite `events.db` 等） | volume：工作区、项目包和知识源快照、共享缓存、辅助命令。`myorch-data`：事件日志和本机状态 |
| agent | Claude Code CLI（`claude -p`），装在 sandbox 镜像里（`images/claude-code`） | 执行 agent 步骤 |
| 开发工具 | uv、pytest、import-linter | 依赖、测试、分层检查 |

## 分层

<!-- diagram: layers.html -->

图的文件：[diagrams/layers.html](diagrams/layers.html)。

依赖方向：`engines → runner → storage → core`。`uv run lint-imports` 检查 `pyproject.toml` 里的两条规则：

- 这四层只能向下依赖。
- `core`、`storage`、`runner` 不能 import `temporalio`（间接 import 也不行）。

`core` 不做 IO、只 import `core` 自己，这一条靠约定，lint 不检查。

四层之外还有两类模块，lint 的分层规则不管它们：

- **领域模块**：`project.py`、`knowledge.py`、`evals.py`、`takeaways.py`、`trail.py`、`runs.py`、`localrepo.py`、`flowfile.py`。它们只依赖 `core`。`runner`（宿主机动作）和 `storage` 用它们。
- **入口**：`cli.py`、`channels/`、`ui/`、`watchdog.py`、`review_page.py`。它们可以用所有下层，发命令经过 `engines/temporal/client.py`。

## 组件图

<!-- diagram: components.html -->

图的文件：[diagrams/components.html](diagrams/components.html)。

## 职责边界

| 关注点 | 负责方 | 说明 |
|---|---|---|
| 原始凭证 | OpenShell gateway | 其他组件只接触 provider 的名字 |
| 网络和文件系统权限 | OpenShell policy | 步骤的 profile 选择用哪个 policy |
| 调度、重试、取消、等人 | 流程引擎 | 调度逻辑在 `core/run.py`，引擎只驱动 |
| 产物和 manifest | volume `myorch-artifacts` | **以 volume 为准**。Temporal 里的状态只是缓存 |
| 交付 | 流程的 `deliver` 步骤（脚本，profile `dev-git`） | 审批后只推送批准的那个提交，写 `docs/delivery.md`。不调用平台 API，不开 PR |
| 人的交互 | 控制台、Slack | 发起、回复、审批。CLI 能做同样的事。Claude Code channel 能做除审批以外的事 |
| 和外部系统通信 | channel 层（`channels/`） | 引擎只写事件，不轮询外部系统。适配器：控制台、Slack、Claude Code channel |

## 代码地图

```
src/myorch/
  cli.py                 所有命令的入口
  flowfile.py            读 flow 文件，按项目裁剪、展开片段，按项目看输入，读 role 和 projects（客户端）
  project.py             项目包：加载、模板继承、快照同步、可写范围（EDITABLE）、生成 GitHub profile
  knowledge.py           知识源：登记、快照、密钥扫描、MCP 配置
  runs.py                运行记录（list、learn 选运行用）；运行的标签
  trail.py               从事件日志整理多个运行的经过（learn 用）；接着改时的对话记录
  takeaways.py           learn 已经读过哪些 takeaway（存在 myorch-data）
  evals.py               reviewer 评测的案例
  localrepo.py           用本机克隆填充新运行的 repo/
  watchdog.py            检查运行中的步骤有没有进展（在 myorch serve 里跑）
  review_page.py         myorch review 生成的审批单页
  core/                  纯逻辑，不做 IO
    flow.py              流程 schema 和校验，role_of
    fragment.py          use: 片段、presets 和流程层 if:（按项目裁剪步骤和输入）的展开
    inputs.py            输入的类型和规则（CLI、控制台、Slack 共用），标签的匹配
    expr.py              条件表达式（不用 eval）
    run.py               RunState：实例展开、调度规划、审批、重试、reopen ← 调度的核心
    prompt.py            prompt 组装
    manifest.py          manifest schema 和兜底
    events.py            从 RunState 算出要发布的事件
    runtrail.py          一个运行到现在的经过（run-trail 动作用）
    types.py             StepKey、StepResult、RunContext
  storage/
    base.py              ArtifactStore 协议和 volume 布局
    docker_volume.py     用辅助容器操作 volume（macOS 宿主机看不到 volume 内部）
    events.py            事件日志（SQLite，只追加）。宿主机上经控制台读（HttpEventLog）
  runner/
    runner.py            StepRunner：一轮步骤从头到尾
    openshell.py         openshell CLI 封装、挂载参数，读 supervisor 日志
    profiles.py          profile 加载
    harnesses/           harness adapter：每种 agent 怎么启动（base.py）
      claude_code.py     Claude Code
      script.py          shell 脚本
    actions.py           宿主机动作（5 个，见 8. 流程语法）
    jobs.py              一次性 sandbox 任务：git 知识源的克隆、Watchdog 的判断
  engines/temporal/
    workflow.py          FlowWorkflow
    activities.py        activity：只做参数转换
    schedules.py         定时触发：ScheduledLaunch
    replay.py            起一个 eval 回放运行
    worker.py、client.py、models.py
  ui/
    app.py               控制台的网页服务和 JSON API
    service.py           控制台的读写逻辑，经过 channel 转发层
    runview.py           用事件拼出一个运行的状态
    flowgraph.py         流程定义的展开图
    static/              页面（HTML、JS、CSS）
  channels/              channel 层
    relay.py             转发层：事件、命令、内容
    http.py              转发层的 HTTP 和 SSE 接口（/channel）
    model.py             channels.yaml：身份对应、Watchdog 设置
    serve.py             myorch serve：转发层 + 控制台 + Slack 适配器 + Watchdog
    slack.py             Slack 适配器：私信线程、提问、审批按钮、/myorch 发起
    claude_code.py       Claude Code channel：本机 MCP 服务器，推送事件，提供工具（单独的进程）

flows/                   流程（flows/lib/ 是片段）。说明见流程手册 docs/workflows/
profiles/                sandbox 的 profile：dev-git、dev-write、dev-read、ticket-read
tools/                   sandbox 里的辅助命令（myorch init 装进 volume）
images/claude-code/      agent 步骤的默认镜像
openshell/providers/     provider profile
channels.yaml            身份对应（谁在哪个平台是哪个账号）
deploy/                  docker compose：服务、myorch 镜像、gateway 配置、凭证导入（provision.py）、
                         发布（update.sh）、新机器安装（bootstrap-mac.sh）、冒烟流程（smoke.yaml）
```

## 一个步骤怎么执行

<!-- diagram: step-execution.html -->

图的文件：[diagrams/step-execution.html](diagrams/step-execution.html)。

- 一个实例一个 sandbox，不复用。sandbox 创建后，policy 就锁死了。
- sandbox 名字是 `<流程>-<步骤>-<哈希5>`，例如 development 的 develop 步骤是 `dev-deve-3f2a9`。哈希由 run、步骤、attempt、turn 算出。流程共用，名字里没有项目。OpenShell 限制名字最长 19 个字符，完整的 id 放在 label 上（`myorch.run`、`myorch.step`、`myorch.attempt`、`myorch.turn`、`myorch.model`，以及项目 `myorch.project`）。
- sandbox 由 `run_step` 自己删除。删除前，supervisor 日志存到步骤的 `logs/supervisor-<attempt>-<turn>.log`。`cleanup_step` 只在 `run_step` 异常结束时（worker 崩溃、心跳超时）补删。
- 宿主机动作（`action:`）不起 sandbox，由 worker 直接执行（activity `run_action`）。只有 `runner/actions.py` 里注册的动作可用，目前有 5 个，见 [8. 流程语法 · 宿主机动作](08-flow-syntax.md#宿主机动作)。动作的 manifest 也写进 `out/`，后面的 sandbox 能挂载它。

## 运行怎么继续

一个步骤或一个运行没走完，有四种办法接着做。

<!-- diagram: run-continuation.html -->

图的文件：[diagrams/run-continuation.html](diagrams/run-continuation.html)。

| 办法 | 谁触发 | 运行 | 会话和 sandbox |
|---|---|---|---|
| 自动重试 | `limits.retries` | 同一个运行，同一个步骤，新 attempt | 新 sandbox。上一次是平台中断（超时、activity 丢失、sandbox 起不来）时，接着被中断的会话。运行被取消时不重试 |
| 失败后问人 | `on_failed: ask` | 同一个运行，等你选 | 选「重新开始」：新 attempt、新会话。写提示：同一个 attempt 的下一轮，接着原会话 |
| 恢复（`myorch resume`、控制台按钮） | 人，对失败或取消的运行 | 同一个 run id。Temporal 里起一个新的执行，带着原来的状态。工作区和 mandate 不变 | `RunState.reopen` 把失败、取消的步骤和被它们挡住的步骤重置。成功的步骤和审批保留。驳回不重开 |
| 接着改（输入类型 `run`，例如 development 的 `from_run`） | 人，对已结束的运行 | 新运行，新 run id，同一个项目和角色 | 新工作区。原运行的对话写进 `docs/previous/conversation.md`，`carry` 列出的输入和标签从原运行带过来 |

失败和重试的规则见 [8. 流程语法 · 失败和重试](08-flow-syntax.md#失败和重试)。`resume` 的用法见 [5. 命令参考](05-cli.md#resume)。

## 凭证怎么流动

图里是工作凭证的路径。工作凭证用来推分支、读工单、访问私有依赖源和模型。渠道凭证（例如 Slack bot token）由 channel 直接管理，不经过 OpenShell，见 [9. 设计原则](09-principles.md#1-凭证分两类agent-都碰不到)。

<!-- diagram: credential-flow.html -->

图的文件：[diagrams/credential-flow.html](diagrams/credential-flow.html)。

worker、控制台、CLI 和 agent 都拿不到真实 token。控制台进程也会起 sandbox（git 知识源的克隆、Watchdog 的判断，见 `runner/jobs.py`），同样只传 provider 的名字。

在 docker compose 里，两类凭证各有一个文件，都在 `deploy/secrets/`（不进 git，不进镜像）：

| 文件 | 凭证 | 谁读 | 去哪里 |
|---|---|---|---|
| `providers.yaml` | 工作凭证（Claude、GitHub、Azure Artifacts、Jira、Atlassian） | 只有 `providers` 服务（`deploy/provision.py`） | 通过子进程的环境变量交给 `openshell provider create/update`，存进 gateway。值不出现在命令行参数和日志里 |
| `slack.env` | 渠道凭证（Slack 的 bot token 和 app-level token） | 只有 `ui` 服务 | 留在 Slack 适配器的进程里 |

## sandbox 里看到什么

每个路径挂什么、能不能写，见 [8. 流程语法 · sandbox 里的环境和挂载](08-flow-syntax.md#sandbox-里的环境和挂载)。默认只有 agent 步骤挂知识源。

工作区内部：

| 路径 | 内容 |
|---|---|
| `repo/` | 代码。同一个 run 的所有步骤共享，不用 worktree |
| `docs/` | agent 写的文档：需求、实现记录、review、CI 报告、风险判定、交付说明 |
| `docs/external/` | 第三方写的内容（你的请求原文、PR 描述、工单附件）。只当数据 |
| `docs/previous/` | 接着改时，原运行的对话记录 |
| `memory/MEMORY.md` | 跨步骤的记忆 |
| `.claude/<instance>/` | 每个实例自己的 claude 会话。重试时保留，平台中断后的重试要接着用 |
| `.myorch/` | flow 快照、mandate、每步的产物和日志（`steps/<instance>/out/`、`logs/`） |

## volume 布局

```
myorch-artifacts
  tools/                 myorch init 装的辅助命令，挂到 /sandbox/bin
  projects/<project>/<哈希>/  myorch project sync 和 project-apply 写的项目包快照（只读，每个版本一个目录）。
                         projects/<project>/.version 是最新的版本，新的运行用它。保留最近 3 个版本和 30 天内写的版本
  shared/<project>/<n>/  跨 run 共享的目录（例如 Gradle 缓存、mdm 的 bare mirror）
  knowledge/<名字>/<哈希>/  知识源快照（只读，每个知识源保留最近 5 个版本）
  runs/<run_id>/         每个 run 的工作区
  jobs/<key>/            一次性 sandbox 任务的输入和输出
```

不同 run 互相看不到。唯一的例外是 `observe_runs`，它只读。

## 宿主机上的数据目录

`myorch-data`（compose 里挂到容器的 `/data`）放本机的状态。它不在 volume 里，不进 sandbox。

| 路径 | 内容 | 谁写 |
|---|---|---|
| `events.db` | 事件日志（`events` 表）和运行中步骤的实时输出（`output` 表） | worker |
| `temporal.db` | Temporal 的数据。已结束的运行保留 24 小时（dev server 默认值） | temporal |
| `watchdog.json` | Watchdog 的检查记录和告警 | myorch serve |
| `ui/`、`slack/` | 控制台和 Slack 适配器自己的状态 | myorch serve |
| `learn/<project>/takeaways.json` | learn 已经读过的 takeaway | 宿主机动作 `takeaways-learned` |
| `evals/` | 评测数据。reviewer 评测的案例在 `evals/cases/` | `myorch eval collect`、宿主机动作 `postmortem-replay` |
| `reviews/<run>.html` | `myorch review` 生成的页面 | CLI |
| `local-repos.yaml` | 本机克隆的位置（seed 用） | 你 |

**宿主机上的进程不直接打开 `events.db`。** worker 和控制台在容器里用 SQLite WAL 打开它，宿主机进程不共享它们的锁。宿主机上的 CLI 通过控制台的 `/channel/events.json` 读事件（`storage/events.py` 的 `HttpEventLog`）。排错见 [11. 排错](11-troubleshooting.md)。

## channel 层

<!-- diagram: channel-layer.html -->

图的文件：[diagrams/channel-layer.html](diagrams/channel-layer.html)。

channel 层在 myorch 和人用的平台之间转发，自己不保存运行状态。转发层是 `channels/relay.py`，三个方向：

| 方向 | 从哪里 | 到哪里 |
|---|---|---|
| 事件 | worker 写进 `events.db`（`FlowWorkflow` 的 `publish_events`） | 适配器。按 `seq` 顺序读，断开后从上次的 `seq` 接着读，不漏不重 |
| 命令 | 适配器（发起、回复、审批、驳回、要求修改、取消、恢复） | Temporal client。按 `channels.yaml` 的 `identities` 换成人名 |
| 内容 | run 的工作区（文档、diff、日志、步骤产物） | 适配器。用的时候读，不复制 |

- 事件只在运行开始、需要人、结束时说话（`core/events.py`）。新运行另外发详细事件（步骤开始和结束、循环轮次、审批进度、回复），控制台只靠事件就能画出整个运行。
- 每个运行带标签：调用方给的、工单输入（`18700` → `MD-18700`）、输入里的 Jira 编号、输入里引用的运行的标签。标签存在 mandate 和 `run_started` 事件里。按标签搜索时扫描整个事件日志，Temporal 已经删掉的运行也能找到。
- 运行列表的状态来自 Temporal。没有标签条件时，只列出 Temporal 还保留的运行。

## 控制台怎么工作

<!-- diagram: console.html -->

图的文件：[diagrams/console.html](diagrams/console.html)。

- 控制台是 channel 层的 web 适配器。它不直接调用 Temporal，也不直接读 volume，所有读写都经过 `channels/relay.py`。运行状态由 `ui/runview.py` 按事件拼出，浏览器通过 SSE（`/channel/events`）收到新事件。没有详细事件的旧运行，暂时回退到 Temporal query。
- 控制台里没有 agent。运行的标签页就是它的对话。你写的第一条消息是请求，原文写进 `docs/external/request.md`。之后的消息原样发给正在提问的步骤。
- 审批是按钮。审批人是本机用户。在 compose 里，容器拿到宿主机的用户名（`USER`、`LOGNAME`），所以不是 `root`。
- 控制台显示的内容来自 run，不可信。控制台有这些防护：
  - Host 必须是 `127.0.0.1:<端口>` 或 `localhost:<端口>`，防 DNS rebinding。
  - 每个 POST 必须带 `X-Myorch-UI` 头，防跨站请求。
  - Markdown 渲染时不保留原始 HTML，链接只允许 http(s) 和相对路径。
  - CSP 只允许 `/static` 下的脚本。
  - 工作区里的图片（png、jpg、gif、webp，最大 10 MB）经 `/api/runs/<id>/raw` 显示，响应带 `Content-Security-Policy: sandbox`。其他类型不能这样打开。
- 实时输出：步骤执行时，worker 用一个只读挂载 volume、关闭网络的辅助容器 `tail -F` 这一轮的日志（`/sandbox/logs/turn-<attempt>-<turn>.log`），每 0.5 秒把新内容写进 `events.db` 的 `output` 表。浏览器打开执行中的步骤时，通过 SSE（`/channel/output?run=&step=`）收到这些内容。
  - agent 步骤：claude 用 `--output-format stream-json` 输出，经 sandbox 里的 `myorch-stream`（`tools/`）转成可读的行再写日志。原始事件留在 `turn-<attempt>-<turn>.jsonl`。重试不覆盖之前尝试的日志。volume 上没有这个工具时，claude 照旧输出纯文本。
  - `output` 是单独的表，不进 `events`，所以运行列表、适配器、Watchdog 读事件时不受影响。每轮最多 200 万字符，超过的部分只在日志文件里。worker 启动时删除 7 天前的内容。
  - 步骤结束后，辅助容器最多再读 1.5 秒就删除。辅助容器带 `timeout`，worker 意外退出时它也会自己结束。
- diff 用 `alpine/git` 辅助容器计算。volume 只读挂载，网络关闭。git 关闭了外部 diff、textconv、fsmonitor 和 hooks，仓库里的配置不能在本机执行任何东西。

## 其他平台适配器

控制台之外，还有两个适配器接在 channel 层上。三者看到的是同一个运行，在哪边处理都行。

| 适配器 | 进程 | 能做什么 | 设计 |
|---|---|---|---|
| Slack（`channels/slack.py`） | `myorch serve` 进程里，Socket Mode | 私信发起人：每个运行一个线程、一条原地更新的进度消息。提问有选项按钮，线程里回复也算回答。审批有批准、要求修改、驳回按钮。结束时说推送了哪个分支。`/myorch` 打开发起弹窗：先选项目，再选这个项目的流程 | [notes/2026-11-05-slack-adapter.md](notes/2026-11-05-slack-adapter.md) |
| Claude Code channel（`channels/claude_code.py`） | 本机 Claude Code 会话启动的 MCP 服务器（stdio），只通过 HTTP 访问 myorch | 推送运行结束、提问、审批和审批提醒、失败的步骤。工具：读运行（可按标签搜索）、事件、完整日志、supervisor 日志、文件、diff，发起、回复、恢复、关注。**不能审批** | [notes/2026-11-05-claude-code-channel.md](notes/2026-11-05-claude-code-channel.md) |

- **身份**：Slack 用户按 `channels.yaml` 的 `identities` 对应到人，没登记的人不能操作。Claude Code channel 以运行 myorch 的人的名义发出命令。
- **来源**：命令带来源（`via`）：`web`、`slack`、`claude-code`。HTTP 接口用 `X-Myorch-Via` 头声明来源，并拒绝 `claude-code` 发来的审批、驳回、要求修改。审批只能由人做。
- **推送什么**：两个适配器都只在运行开始、需要人、结束时说话。Claude Code channel 另外推送失败的步骤，方便它分析。

## 本机部署形态

<!-- diagram: deployment.html -->

图的文件：[diagrams/deployment.html](diagrams/deployment.html)。

日常用 docker compose 运行（`deploy/compose.yaml`）。sandbox 仍然是宿主机 Docker 上的容器，和 compose 的服务平级：gateway 和 worker 通过挂进容器的 `/var/run/docker.sock` 操作宿主机的 Docker。服务之间的调用关系见 [2. 服务和依赖 · docker compose 里的服务](02-services.md#docker-compose-里的服务)。

只支持单机。团队化（服务端事件存储、网页界面加登录、按项目的权限）和 Kubernetes 都还没做。
