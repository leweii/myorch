# 2. 服务和依赖

安装步骤见 [4. 安装和启动](04-setup.md)。

## 服务清单

2026-11-05 起，日常环境是 docker compose（`deploy/compose.yaml`）。Docker Desktop 启动后，服务自动起来。`dc` 是 `docker compose -f deploy/compose.yaml` 的简写。

| 服务 | 怎么起 | 地址 | 数据 | 必需 |
|---|---|---|---|---|
| **Docker Desktop** | 打开 Docker Desktop 应用 | 本机 Docker socket | volume `myorch-artifacts`、`myorch_gateway-state`，镜像 | 是 |
| **temporal** | compose 服务（`temporalio/temporal:1.9.1`，`server start-dev`） | gRPC `127.0.0.1:7233`，界面 http://127.0.0.1:8233 | `myorch-data/temporal.db`。已结束的运行保留 24 小时 | 是 |
| **gateway**（OpenShell） | compose 服务，官方镜像 `ghcr.io/nvidia/openshell/gateway:0.1.6` | `127.0.0.1:17671`。容器里用 `http://gateway:17671` | provider（凭证）、provider profile、sandbox 记录，在 volume `myorch_gateway-state` | 是 |
| **worker** | compose 服务（`myorch worker`） | 不监听端口。从 Temporal 的 task queue `myorch` 取任务 | 写 `myorch-data/events.db` | 是 |
| **ui**（控制台，`myorch serve`） | compose 服务 | http://127.0.0.1:8800/，channel 接口在 `/channel/` | 写 `myorch-data/ui/`、`slack/`、`watchdog.json`。Watchdog、Slack 适配器也在这个进程里 | 是。同时只能开一个 |
| gateway-init、init、providers | compose 里跑一次就退出 | — | gateway-init：没有签名密钥时生成。init：建 volume，装 `tools/`。providers：把 `deploy/secrets/providers.yaml` 写进 gateway | `up` 时自动跑 |
| Slack 适配器 | 在 ui 进程里，有 `deploy/secrets/slack.env` 时启动 | 主动连 Slack（Socket Mode），不监听端口 | `myorch-data/slack/state.json` | 否 |
| Claude Code channel | 本机的 Claude Code 会话启动它（`.mcp.json`） | 连 `127.0.0.1:8800` | `~/.cache/myorch/claude-code-<人>.json`（读到的位置） | 否 |

不用 compose 的跑法见 [4.3](04-setup.md#43-每天开工) 的最后一段。

## 服务依赖图

服务之间的关系分三张图：

1. 总图：有哪些服务，谁调用谁。OpenShell 在这张图里只画一个框。
2. docker compose 里的服务：每个容器、挂载和端口。
3. OpenShell 内部：只画 OpenShell 自己的组件，说明 gateway 和 supervisor 怎么配合，sandbox 里的请求怎么经过 policy 到达 GitHub、Jira、Claude API 等第三方平台。Slack 不在其中：它由 `myorch serve` 里的 Slack 适配器直连，凭证不经过 OpenShell。

文档里的图用灰底白卡片画，只有状态和重点用颜色：蓝色表示出站和重点组件，红色表示不可信。图跟着页面右上角的主题（浅色、深色、跟随系统）变色。

| 画法 | 含义 |
|---|---|
| 灰色细箭头 | 调用。箭头从调用方指向被调用方，线旁写调用方式 |
| 蓝色粗箭头 | policy 放行的出站请求 |
| 浅灰虚线箭头 | 读写数据 |
| 蓝色描边卡片 | 这张图的重点组件 |
| 斜线阴影卡片 | 和隔离、出站有关的组件 |
| 深色描边卡片 | 人，或者需要你处理的地方 |
| 黑底卡片 | 不可信的 agent 进程 |
| 等宽字卡片 | 数据 |
| 红色虚线框、浅红底 | 不可信一侧（workload 容器） |

图是 `docs/diagrams/` 下的 HTML 文件。网页版 `docs/index.html` 把它们嵌在这一节，也可以直接用浏览器打开。

### 总图

<!-- diagram: services-overview.html -->

图的文件：[diagrams/services-overview.html](diagrams/services-overview.html)。

「入口」区里的控制台、CLI 和 channels 调用同一套 Python 函数（`engines/temporal/client.py`、`storage/docker_volume.py`），图里共用一组连线。

- 控制台和 CLI 只调用 Temporal，不直接调用 worker。worker 主动从 Temporal 的 task queue `myorch` 取任务。
- 只有 worker 会创建步骤的 sandbox。同步 git 仓库知识源、`myorch knowledge check` 也会起一次性的 sandbox，由发起它的进程起。
- 控制台、CLI 和 worker 读写 volume 时，用的是 `--network none` 的一次性辅助容器。
- 宿主机上的 CLI 读事件日志时，经过控制台的 `/channel/events.json`，不直接打开 `events.db`（见[文件和配置的读写方](#文件和配置的读写方)）。

channel 层上现在有三个平台适配器：控制台、Slack、Claude Code channel。GitHub 上的交互（评论里召唤、回复、审批）已经暂停。新平台在 relay 上写适配器，见 [notes/2026-11-02-channel-layer.md](notes/2026-11-02-channel-layer.md) 和 [3. 架构 · 其他平台适配器](03-architecture.md#其他平台适配器)。

### docker compose 里的服务

<!-- diagram: compose-services.html -->

图的文件：[diagrams/compose-services.html](diagrams/compose-services.html)。

- 你只和两个地方打交道：浏览器打开控制台，终端里执行 `myorch` 命令（宿主机上的 `uv run myorch`，或 `docker compose exec worker myorch`）。
- compose 里的服务在同一个网络里，用服务名互相访问：控制台和 worker 连 `temporal:7233`，worker、控制台和 `providers` 连 `http://gateway:17671`。
- 只有 `providers` 挂载凭证文件。它用 `openshell` CLI 把 provider profile 和凭证写进 gateway。只有 `ui` 读 `deploy/secrets/slack.env`。worker 和 sandbox 都拿不到这两个文件。
- worker 和 ui 挂载同样的宿主机目录：`myorch-data/`（`/data`）、`myorch-knowledge/`（`/knowledge`）、`channels.yaml`（只读），以及本机目录知识源的根目录（只读，默认 `~/orca/kb`，容器内外路径相同）。
- gateway 和 worker 都通过 `/var/run/docker.sock` 操作宿主机的 Docker。gateway 建 sandbox 的两个容器。worker、控制台、`init` 起辅助容器读写 volume `myorch-artifacts`。挂 docker.sock 等于给这些容器宿主机的 root 权限，所以这套服务本身不是隔离边界。
- supervisor 不在 compose 的网络里（`supervisor_network_mode` 只能选 `auto`、`host`、`bridge`）。它通过 `host.docker.internal:17671` 回连 gateway，所以 gateway 的 17671 端口要发布到宿主机。这是唯一一条从宿主机绕回 compose 的连接。
- `gateway-init` 把签名密钥写进 volume `gateway-state`，gateway 从这里读密钥，也把自己的数据库放在这里。

### OpenShell 内部

<!-- diagram: openshell-internals.html -->

图的文件：[diagrams/openshell-internals.html](diagrams/openshell-internals.html)。参照 [OpenShell 架构文档](https://docs.nvidia.com/openshell/about/architecture)。

- gateway 是控制面，是 compose 里常驻的 `gateway` 服务。它保存 provider 的真实凭证、policy 和 sandbox 记录，签发 JWT。它通过 docker driver 创建 sandbox。
- 每个步骤实例的 sandbox 在 Docker 上是两个容器，`docker ps` 能看到。名字由流程、步骤和一段哈希组成，例如 `dev-deve-3f2a9`（development 的 develop）。supervisor 容器的名字以 `-supervisor` 结尾，用 `bridge` 网络。workload 容器的网络是 `none`。
- supervisor 在隔离边界的可信一侧。它通过 `host.docker.internal` 回连 gateway，用 gateway JWT 认证，取得 policy 和凭证，推送日志。
- agent 进程在不可信一侧的 workload 容器里。Landlock 限制 agent 能读写的路径。seccomp 拦截 agent 的 TCP 连接和 DNS 查询，通过一条经认证的 Unix socket 交给 supervisor。这条 socket 是 agent 唯一的出站路径。
- supervisor 判定每个请求。policy 放行时，supervisor 把占位符换成真实凭证，再建立真正的连接。compute driver 只建边界，不判定请求。
- myorch 只传 provider 的名字（例如 `claude`、`github-blog`）。worker、控制台、CLI 和 agent 都拿不到真实凭证。

## 启动顺序

<!-- diagram: compose-startup.html -->

图的文件：[diagrams/compose-startup.html](diagrams/compose-startup.html)。

compose 按 `depends_on` 排序。`providers` 失败时 worker 不启动。控制台不等 gateway 和 `providers`。

## 每个命令依赖哪些服务

命令本身的用法见 [5. 命令参考](05-cli.md)。「控制台」一列指宿主机上执行的命令：它们经过控制台读事件日志。在容器里执行时直接读文件，不需要控制台。

| 命令 | Temporal | worker | Docker | OpenShell | 控制台 |
|---|---|---|---|---|---|
| `expand`、`project show`、`project github-profile` | | | | | |
| `init`、`project list`、`project sync`、`knowledge list` | | | ✓ | | |
| `knowledge sync` | | | ✓ | git 仓库知识源 | |
| `knowledge check` | | | | ✓（在容器里执行） | |
| `logs`、`out`、`ws`、`export` | | | ✓ | | |
| `run` | ✓ | 要推进运行时需要 | 有知识源时 | 要同步 git 仓库知识源时 | 接着某个运行改，或 learn 时 |
| `reply`、`approve`、`reject`、`changes`、`cancel`、`resume` | ✓ | 要推进运行时需要 | | | |
| `status` | ✓ | | | | |
| `list`、`review` | ✓ | | ✓ | | |
| `schedule` | ✓ | 到时间发起运行时需要 | | | |
| `eval` | ✓ | `eval run` 需要 | ✓ | | `eval report` |
| `worker` | ✓ | — | ✓ | ✓ | |
| `ui`、`serve` | ✓ | 要推进运行时需要 | ✓ | 要同步 git 仓库知识源时 | — |

「要推进运行时需要」指命令本身能成功，例如 `run` 能打印 run_id，但 worker 不在时运行不会往下走。

## 一个服务停了会怎样

| 停掉的服务 | 现象 | 恢复后 |
|---|---|---|
| Temporal | CLI 和控制台都连不上 `127.0.0.1:7233`。控制台起不来 | 状态在 `temporal.db` 里，不丢。运行接着走 |
| worker | 新运行一直 `pending`。正在执行的步骤在心跳超时后算一次失败 | 起 worker 后继续。失败的那一次按步骤的 `retries` 决定是否重跑 |
| OpenShell gateway | 创建 sandbox 失败，步骤失败 | 按步骤的 `retries` 重跑。没有重试次数时运行失败，用 `myorch resume <run>` 从失败的步骤接着跑 |
| providers（凭证导入失败） | worker 不启动（compose 的依赖关系） | 修好 `deploy/secrets/providers.yaml`，`dc run --rm providers`，再 `dc up -d` |
| Docker | compose 的所有服务都停了 | Docker Desktop 起来后服务自动起来（`restart: unless-stopped`）。当时正在执行的步骤和 worker 停掉时一样处理 |
| 控制台 | 不影响运行。等你的事仍在等，直到超时（提问默认 24 小时）。Slack 收不到通知。宿主机上要读事件日志的命令失败（见上表） | 重新打开即可。Slack 适配器从游标接着发，不重发 |
| Claude Code 会话 | 收不到推送 | 下次启动 channel 时，期间发生的事合成一条汇总推送 |

关机前不需要做什么。等你处理的运行状态存在 Temporal 里，下次服务起来后继续。

## 外部依赖

| 依赖 | 谁访问 | 经过什么 |
|---|---|---|
| `api.anthropic.com` | sandbox 里的 claude CLI | OpenShell 注入 provider `claude` 的 token |
| `github.com`、`api.github.com` | `dev-git` profile 的步骤（clone、fetch、push），review 流程读 PR | OpenShell 注入项目的 GitHub provider。规则只放行这个项目的仓库，子模块只读 |
| Jira（`example.atlassian.net`） | `ticket-read` profile 的步骤（读工单和附件） | OpenShell 注入 provider `jira-mdm`。规则只放行读 |
| Atlassian MCP（`mcp.atlassian.com`） | 挂了 Atlassian MCP 知识源的 agent 步骤 | OpenShell 注入 provider `atlassian-mcp`。`myorch-mcp` 只放行登记的工具 |
| 项目的依赖源（Maven、npm、私有源） | `dev-write`、`dev-git` 的步骤 | 项目包的 `policy.yaml` 放行。私有源用 `artifacts` provider |
| Docker 镜像仓库 | Docker：辅助镜像 `alpine/git`、`nvcr.io/nvidia/base/ubuntu:24.04`。compose 的 `temporalio/temporal`、`ghcr.io/nvidia/openshell/*`、`alpine`。构建 myorch 镜像用的 `python:3.12-slim`、`docker:cli`、`ghcr.io/astral-sh/uv` | 本机网络 |
| `github.com` 的 release | 构建 myorch 镜像时下载 openshell CLI | 本机网络 |
| Slack（`slack.com`、Socket Mode） | ui 进程里的 Slack 适配器 | bot token 和 app-level token，在 `deploy/secrets/slack.env` |

## 文件和配置的读写方

| 文件 | 读 | 写 |
|---|---|---|
| `flows/`、`profiles/` | CLI、控制台（提交前展开），worker（profile，定时任务发起运行） | 你 |
| `channels.yaml` | CLI、控制台、Slack 适配器、worker（定时任务）：`identities`，谁是谁，发起人的别名，谁能在 Slack 里操作 | 你 |
| `deploy/secrets/providers.yaml` | 只有 compose 的 `providers` 服务 | 你 |
| `deploy/secrets/slack.env` | 只有 compose 的 `ui` 服务 | 你 |
| `myorch-knowledge/` | CLI、控制台（提交前填项目值），worker（定时任务、宿主机动作），`project sync`，`providers` 服务（项目的 provider profile） | 你；worker 的 `project-apply`；控制台（登记知识源，自动提交） |
| volume `myorch-artifacts` | 所有组件 | worker（sandbox、辅助容器），控制台和 CLI（知识源快照、`project sync`），`init` |
| `myorch-data/events.db` | 控制台进程里的 channel 层（控制台、Slack、Claude Code channel、Watchdog 都经过它），容器里的 CLI | worker |
| `myorch-data/temporal.db` | Temporal | Temporal |
| `myorch-data/slack/state.json` | Slack 适配器 | Slack 适配器 |

worker 把 `events.db` 写到数据目录（`--data` 或 `MYORCH_DATA`，compose 里是 `/data`）。

`events.db` 只由容器里的进程打开。宿主机上的进程（例如 `uv run myorch run`）通过控制台的 `GET /channel/events.json` 读，控制台没开时报错，不退回到直接读文件。原因：宿主机的进程和容器不共享 SQLite 的锁，它关闭连接时会删掉 `events.db-shm`，容器里的查询全部报 `disk I/O error`（2026-11-09 遇到过，处理见 [11. 排错](11-troubleshooting.md)）。`MYORCH_EVENTS_DIRECT=1` 强制直接读文件，只在没有容器打开它时用。
