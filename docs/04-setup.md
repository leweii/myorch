# 4. 安装和启动

2026-11-05 起，日常环境是 docker compose：Temporal、OpenShell gateway、worker、控制台都在容器里。所有命令都在 `myorch/` 目录下执行。服务之间的关系见 [2. 服务和依赖](02-services.md)。compose 的详细步骤、凭证文件、Slack、Claude Code channel、端口和已知限制见 [deploy/README.md](../deploy/README.md)。

## 4.1 依赖的软件

宿主机上要装的：

| 软件 | 本机版本（实测可用） | 用途 |
|---|---|---|
| macOS + Docker Desktop | Docker 29.9 | 跑 compose 的服务、sandbox 容器、volume `myorch-artifacts`、辅助容器。只在 Apple Silicon 上跑过 |
| git、gh | | 克隆 myorch 和私有仓库 myorch-knowledge。`deploy/bootstrap-mac.sh` 要求 gh 已登录 |
| uv | 0.13 | 宿主机上的 CLI（`uv run myorch ...`）、测试、Claude Code channel。项目要求 Python ≥ 3.12 |
| claude CLI | | 生成 Claude 凭证（`claude setup-token`），以及接收 myorch 推送的 Claude Code 会话 |

Temporal 和 OpenShell 不用装在宿主机上：

| 组件 | 版本 | 在哪里 |
|---|---|---|
| Temporal | 镜像 `temporalio/temporal:1.9.1`（`server start-dev`） | compose 服务 `temporal` |
| OpenShell gateway | 镜像 `ghcr.io/nvidia/openshell/gateway:0.1.6` | compose 服务 `gateway` |
| openshell CLI | 0.1.6 | 打进 myorch 镜像（`deploy/Dockerfile`），worker、控制台、`providers` 用它 |

Python 依赖（`pyproject.toml`）：`temporalio`、`pydantic`、`pyyaml`、`starlette`、`uvicorn`、`markdown`、`aiohttp`、`slack-sdk`。开发依赖：`pytest`、`pytest-asyncio`、`import-linter`。`uv sync` 全部装好。

Docker 镜像：

| 镜像 | 来源 | 用途 |
|---|---|---|
| `myorch:local` | `deploy/Dockerfile`，`docker compose build` 构建 | worker、控制台、`init`、`providers` |
| `openshell-claude-code:latest` | 本仓库 `images/claude-code/`，自己构建 | 默认的 sandbox 镜像，也是项目开发镜像的基础 |
| `myorch-dev-<项目>:latest` | 项目包的 `image/`，自己构建 | 项目的开发镜像。blog 还有 `myorch-e2e-blog:latest`（浏览器测试） |
| `alpine/git:latest`、`nvcr.io/nvidia/base/ubuntu:24.04` | 自动拉取 | 读写 volume 的辅助容器 |

## 4.2 一次性安装

**新的 Mac 用安装脚本 `deploy/bootstrap-mac.sh`**，步骤见 [deploy/README.md 的「新机器」](../deploy/README.md#新机器)。脚本可以反复运行，做好的步骤会跳过。它做的事是：

1. 检查 git、Docker Desktop、gh（已登录）。
2. 三个目录放在同一个父目录下（默认 `~/orca/projects/`，用 `MYORCH_ROOT` 改）：克隆 myorch 和 myorch-knowledge，建 `myorch-data`。各自的作用见 [1. 概览 · 目录和存储](01-overview.md#目录和存储)。
3. 检查凭证文件 `deploy/secrets/providers.yaml`（模板 `deploy/providers.example.yaml`）。Slack 用的话还有 `deploy/secrets/slack.env`。
4. 构建镜像：`openshell-claude-code`，再构建所有项目的开发镜像。
5. 把控制台和 CLI 以谁的名义操作写进 `deploy/.env`（`MYORCH_USER`，必须是 `channels.yaml` 里的人。`channels.yaml` 写的是真实账号，不进 git；没有时从 `channels.example.yaml` 生成，里面只有你），然后 `docker compose -f deploy/compose.yaml up -d --build`。
6. 同步所有项目包（`myorch project sync`）。
7. 冒烟测试：`docker compose -f deploy/compose.yaml exec worker myorch run deploy/smoke.yaml --wait`。两个脚本步骤，不调用模型。

手动安装的步骤和脚本相同，每一步的命令见 [deploy/README.md 的「安装」](../deploy/README.md#安装)。

装好以后，宿主机上的 CLI 也要装依赖：

```sh
uv sync
uv run myorch list      # 能连上 compose 里的 Temporal，列出最近的运行
```

## 4.3 每天开工

服务设成 `restart: unless-stopped`，Docker Desktop 启动后自动起来，不用开终端。

```sh
docker compose -f deploy/compose.yaml ps            # 都在跑：temporal、gateway、worker、ui
uv run myorch list                                   # 最近的运行，哪些在等你
```

- 控制台：http://127.0.0.1:8800/ 。Temporal 界面：http://127.0.0.1:8233/ 。
- Temporal 只保留已结束的运行 24 小时（dev server 的默认值，2026-11-10 用 `temporal operator namespace describe default` 查过）。更早的运行，控制台从事件日志里读状态和对话。
- 改了 `src/` 或 `flows/` 以后：`docker compose -f deploy/compose.yaml up -d --build`。这会打断正在执行的步骤。发布 origin/main 上的提交用 `deploy/update.sh`，它等正在执行的步骤结束再重启（见 [deploy/README.md](../deploy/README.md#发布-originmain)）。其他改动要做什么，见 [1. 概览 · 改了什么要做什么](01-overview.md#改了什么要做什么)。

确认服务都在：

```sh
docker compose -f deploy/compose.yaml ps -a                          # gateway-init、init、providers 是 exited (0)
docker compose -f deploy/compose.yaml exec worker openshell provider list   # claude 和各项目的凭证都在
docker compose -f deploy/compose.yaml logs providers                 # 凭证导入失败时看这里
```

关机前不需要做什么，见 [2. 服务和依赖 · 一个服务停了会怎样](02-services.md#一个服务停了会怎样)。

不用 compose 时，也可以在宿主机上开三个终端：`temporal server start-dev --db-filename ../myorch-data/temporal.db`、`uv run myorch worker`、`uv run myorch ui`，再起一个 OpenShell gateway（配置同 `deploy/gateway.toml`）。这要在宿主机上装 Temporal CLI 和 OpenShell。2026-11-05 以后没有再这样跑过（待验证）。

## 4.4 按需配置

### 项目的 GitHub 凭证

每个项目在 `project.yaml` 的 `providers.github` 里写它用哪个 provider。凭证写进 `deploy/secrets/providers.yaml`，再执行 `docker compose -f deploy/compose.yaml run --rm providers`：

```yaml
github_profiles: [blog, mdm, myorch]   # 为这些项目生成只放行自己仓库的 GitHub profile
providers:
  github-blog:                         # providers.github 的值
    type: github-blog                  # github-<项目>，即生成的 profile
    credentials:
      GITHUB_TOKEN: "..."
```

`providers` 服务对 `github_profiles` 里的每个项目执行 `myorch project github-profile <项目>`，把生成的 profile 导入 gateway（已有的就更新），再建 provider 或更新它的 token。文件格式见 [deploy/README.md 的「安装」](../deploy/README.md#安装)。

现有的 provider：

| provider | 账号 | 放行范围 | 用于 |
|---|---|---|---|
| `claude` | 你的 Claude 订阅（`claude setup-token`） | Claude API | 所有 agent 步骤。名字必须是 `claude` |
| `github-blog` | 你本人 `leweii` | 只放行 `leweii/leweii.github.io` | blog |
| `github-mdm` | 你本人 `leweii` | mdm 的仓库，子模块仓库只读 | mdm |
| `github-myorch` | 你本人 `leweii` | 只放行 `leweii/myorch` | myorch |
| `azure-artifacts` | 公司的私有 Maven / npm 源（PAT，只有 Packaging Read） | 私有依赖源 | mdm（`providers.artifacts`） |
| `jira-mdm`、`atlassian-mcp` | 你本人的 Atlassian API token | Jira 只读，Atlassian MCP 只读 | mdm（读工单、知识源） |

生成的规则放行这个仓库的 clone、fetch、push、读 API 和创建 PR（myorch 现在不开 PR），子模块只读，不放行发评论。只读的项目在 `myorch project github-profile` 后加 `--read-only`。

关于 token：

- GitHub 没有创建 token 的 API。token 只能在网页上建（Settings → Developer settings）。实验项目可以复用 `gh auth token` 的 token。这个 token 能访问你的所有仓库，但 gateway 只放行 profile 里的仓库。
- fine-grained token 要在 Repository permissions 里**手动加上** Contents，设为 Read and write。不加 Contents 时 API 返回 404。review 流程读 PR，还要 Pull requests 的读权限。

### 项目的开发镜像

项目包的 `image/` 里有构建脚本。在 `myorch-knowledge/` 下执行，先构建 `openshell-claude-code:latest`：

```sh
_templates/core-ng/image/build.sh               # myorch-dev-core-ng:latest（JDK 21、Node 22、yarn、gh）
mdm/image/build.sh                              # myorch-dev-mdm:latest（在 core-ng 上加 JDK 25、Node 24、pnpm 9）
blog/image/build.sh <博客仓库的路径>             # myorch-dev-blog:latest 和 myorch-e2e-blog:latest
myorch/image/build.sh <myorch 仓库的路径>        # myorch-dev-myorch:latest（依赖来自 uv.lock）
```

依赖变了（例如博客的 Gemfile.lock、myorch 的 uv.lock）就重新构建。镜像里的东西**必须装在 `/usr` 下**，因为 OpenShell 的 Landlock 不允许读 `/opt`。

### 项目包同步

run 用的是 volume 里的快照。项目包改了以后（手改或 git pull）必须同步：

```sh
uv run myorch project sync <project>
uv run myorch project list            # 看哪些项目已同步
```

### 本机目录知识源

`dir` 类型的知识源，文件夹要放在宿主机的知识根目录下（`MYORCH_KB_ROOT`，默认 `~/orca/kb`）。compose 把这个目录按同一个绝对路径只读挂进控制台和 worker，登记的路径里 `~` 指宿主机的 home。这样从控制台、Slack、定时任务发起的运行，和从宿主机 CLI 发起的一样能挂上。不挂整个 home，因为里面有 `~/.ssh` 这类文件。知识源怎么登记见 [7. 项目包 · 知识源](07-projects.md#知识源)。

## 4.5 开发环境

```sh
uv run pytest          # 单元测试（约 380 个，约 20 秒），不需要 Temporal 和 OpenShell
uv run lint-imports    # 检查分层依赖
```

改了 `src/` 以后：跑测试，重建并重启 worker 和控制台。改了 workflow 的确定性逻辑（`engines/temporal/workflow.py` 或 `core/run.py`）时，还没结束的旧 run 可能回放失败，见 [10. 使用规范](10-conventions.md#代码) 和 [11. 排错](11-troubleshooting.md)。

## 4.6 用 docker compose 运行

compose 里有哪些服务、谁调用谁、启动顺序，见 [2. 服务和依赖](02-services.md#docker-compose-里的服务)。sandbox 仍然是宿主机 Docker 上的容器。只在 macOS 上用过，Linux 主机还没有测。

宿主机上的命令和 compose 的分工：

- 宿主机上的 `uv run myorch ...` 照常能用：它连发布在 `127.0.0.1:7233` 的 Temporal，用宿主机的 Docker 读写 volume，经过控制台读事件日志。
- 要用 OpenShell 的命令（例如 `myorch knowledge check`、`openshell provider list`）在容器里执行：`docker compose -f deploy/compose.yaml exec worker <命令>`。宿主机上没有 gateway 的配置。
- 控制台和容器里的 CLI 以 `deploy/.env` 的 `MYORCH_USER`（没有时是宿主机的 `$USER`）的名义操作，不是 root。
- 路径、端口、卷名等设置在 `deploy/.env`（模板 `deploy/.env.example`），默认值适合新机器。
