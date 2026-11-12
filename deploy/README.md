# 用 docker compose 运行 myorch

服务之间的调用关系和启动顺序见 [2. 服务和依赖](../docs/02-services.md#docker-compose-里的服务) 里的图。

状态：**2026-11-05 起是日常环境（macOS，Docker Desktop）**。冒烟测试通过，8 个凭证都从 sandbox 里测过（包括 github.com 的 git 读取）。Linux 主机还没有测。调研结果见 [docs/notes/2026-11-04-gateway-in-docker.md](../docs/notes/2026-11-04-gateway-in-docker.md)。

compose 里有这些服务：

| 服务 | 内容 |
|---|---|
| `temporal` | `temporal server start-dev`，数据库是数据目录里的 `temporal.db` |
| `gateway-init` | 每次 `up` 跑一次：没有密钥时，生成 gateway 签发 sandbox 凭证用的 Ed25519 密钥 |
| `gateway` | OpenShell gateway 官方镜像，docker driver |
| `providers` | 每次 `up` 跑一次：从凭证文件导入 provider profile 和凭证（`deploy/provision.py`） |
| `init` | 每次 `up` 跑一次：执行 `myorch init`，建 volume、打审批标签、装 `tools/` |
| `worker` | `myorch worker`。`init` 和 `providers` 成功以后才启动 |
| `ui` | `myorch ui`：控制台，以及 Slack 适配器和 watchdog |

worker 和 ui 挂载宿主机的 `myorch-data/`（`/data`）、`myorch-knowledge/`（`/knowledge`）、`channels.yaml`（只读），以及本机目录知识源的根目录 `MYORCH_KB_ROOT`（只读，默认 `~/orca/kb`，容器内外路径相同）。

sandbox 仍然是宿主机 Docker 上的容器，和这些服务平级。gateway 和 worker 通过挂进来的 `/var/run/docker.sock` 操作宿主机的 Docker，不是 Docker-in-Docker。挂 docker.sock 等于给这些容器宿主机的 root 权限。sandbox 的隔离不受影响，但这套服务本身不是隔离边界。

## 安装

所有命令都在 `myorch/` 目录下执行。

### ① 目录

`myorch/`、`myorch-knowledge/`、`myorch-data/` 放在同一个父目录下，和 [4. 安装和启动](../docs/04-setup.md) 一样。放在别处时，复制 `deploy/.env.example` 为 `deploy/.env`，改里面的路径。`deploy/.env` 里还可以设端口、卷名、时区、`MYORCH_KB_ROOT`，以及控制台和 CLI 以谁的名义操作（`MYORCH_USER`，必须是 `channels.yaml` 里的人；不设时用宿主机的 `$USER`）。

### ② 凭证文件

凭证写在 `deploy/secrets/providers.yaml` 里。每次 `docker compose up`，`providers` 服务读这个文件，写进 gateway：

```sh
mkdir -p deploy/secrets
cp deploy/providers.example.yaml deploy/secrets/providers.yaml
chmod 600 deploy/secrets/providers.yaml
```

用编辑器填入 token。格式见 `deploy/providers.example.yaml` 里的注释：

```yaml
github_profiles: [blog]              # 要生成 GitHub profile 的项目
providers:
  claude:                            # 名字必须是 claude
    type: claude-code-oauth
    credentials:
      CLAUDE_CODE_OAUTH_TOKEN: "..."   # claude setup-token 生成的 token
  github-blog:
    type: github-blog
    credentials:
      GITHUB_TOKEN: "..."
```

- `credentials` 的键是 provider profile 里声明的环境变量名（`env_vars`）。
- `deploy/secrets/` 在 `.gitignore` 和 `.dockerignore` 里，不会提交，也不会打进镜像。只有 `providers` 服务挂载它，worker、控制台和 sandbox 都看不到。
- 值只通过子进程的环境变量交给 `openshell`，不出现在命令行参数和日志里。
- 不管文件里有没有列出，这些 provider profile 都会导入：`openshell/providers/*.yaml`，以及 `myorch-knowledge/<项目>/providers/*.yaml`。gateway 里已有的 profile 按文件更新。
- gateway 里有、文件里没有的 provider 保持不变，日志里会列出来。
- 文件不存在时，`providers` 服务什么也不做。
- 改了 token 以后，执行 `docker compose -f deploy/compose.yaml run --rm providers` 就能生效，不用重启其他服务。
- provider 的值全部为空时跳过，日志里写 `skipped, no value yet`，可以以后再填。
- 改了 provider 的 `type`（比如 `claude` 从 `claude-code-oauth` 换成 `claude-code-vertex`）时，先删掉旧的再按新类型建。
- 有 `refresh:` 的 provider，由 gateway 自己换取短期 token：`service_account_json:` 指向 secrets 目录里的 GCP service account JSON key，`client_email` 和 `private_key` 作为刷新材料交给 gateway（私钥只在 gateway 里），然后立即换一次 token。key 不对或没有权限时，`providers` 在这一步失败。
- 用 Vertex AI 时（`claude` 的类型是 `claude-code-vertex`），还要在 `deploy/.env` 里设 `MYORCH_VERTEX_PROJECT`（和 `MYORCH_VERTEX_REGION`，默认 `global`），写法见 `deploy/providers.example.yaml`。
- 文件写错（只填了一部分值、类型不存在）时，`providers` 以失败退出，worker 不启动。用 `docker compose -f deploy/compose.yaml logs providers` 查看原因。

gateway 的状态（凭证、provider profile）在 volume `<项目名>_gateway-state` 里（默认 `myorch_gateway-state`）。

### ③ 构建镜像

```sh
docker build -t openshell-claude-code:latest images/claude-code    # 默认 sandbox 镜像
docker compose -f deploy/compose.yaml build                          # myorch 镜像
```

项目的开发镜像用项目包里的 `image/` 构建，见 [4. 安装和启动 · 项目的开发镜像](../docs/04-setup.md#项目的开发镜像)。

### ④ 启动

```sh
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps -a     # gateway-init、providers、init 是 exited (0)，其他是 running
```

### ⑤ 冒烟测试

不需要凭证，也不调用模型：

```sh
docker compose -f deploy/compose.yaml exec worker myorch run deploy/smoke.yaml --wait
```

两个步骤都是 `succeeded`，第二步的摘要是 `upstream said: hello from ...`。

### ⑥ 项目包和知识源

下面的命令用一个 shell 函数（bash 和 zsh 都能用）：

```sh
mo() { docker compose -f deploy/compose.yaml exec worker "$@"; }
mo myorch project sync <name>
```

知识源不用手动同步：发起运行时自动同步。要提前同步时用 `mo myorch knowledge sync <名字> --project <name>`。

然后打开控制台：http://127.0.0.1:8800/

## 新机器

用于长期运行的机器，例如 Mac mini。

新机器从零开始，不带旧机器的历史（以前运行的工作区、评测案例、Temporal 记录）。learn、eval 在新机器上从头积累。

### 旧机器上先做

1. myorch 和 myorch-knowledge 都已经推到 GitHub（myorch-knowledge 是私有仓库 `leweii/myorch-knowledge`）。在两个目录里各执行一次 `git status`，确认没有没提交的改动。
2. 准备好凭证文件 `deploy/secrets/providers.yaml`（和 `slack.env`，如果新机器要跑 Slack）。用 AirDrop 或 `scp` 传过去，不要经过聊天或邮件。

### 新机器上

1. 装 Docker Desktop，启动它，并在设置里打开「登录时启动」。没有 git 时执行 `xcode-select --install`。装 Homebrew，再 `brew install gh`，然后 `gh auth login`（账号 `leweii`）。
2. 克隆 myorch，把凭证文件放进 `deploy/secrets/`：
   ```sh
   mkdir -p ~/orca/projects && gh repo clone leweii/myorch ~/orca/projects/myorch
   mkdir -p ~/orca/projects/myorch/deploy/secrets   # 把 providers.yaml（和 slack.env）放进这里
   ```
3. 运行安装脚本：
   ```sh
   bash ~/orca/projects/myorch/deploy/bootstrap-mac.sh
   ```
   它会克隆 myorch-knowledge、建 `myorch-data`、构建所有镜像（第一次要下载几 GB）、起服务、同步项目包、跑冒烟测试。三个目录默认放在 `~/orca/projects/` 下，用 `MYORCH_ROOT` 改。哪一步需要你动手，它会停下来说明。可以反复运行：已经做好的步骤会跳过。`FORCE=1` 强制重建镜像。
4. 新机器的登录名不是 `jakobhe` 时，按脚本的提示加上 `MYORCH_USER=jakobhe` 再运行。这个值写进 `deploy/.env`，控制台和 CLI 以这个人的名义操作。

### 长期运行

- **不要睡眠**：系统设置 → 能耗，打开「防止自动进入睡眠」。
- 服务设成 `restart: unless-stopped`，Docker Desktop 启动后自动起来。
- **Slack 只能在一台机器上跑**：两台机器用同一个令牌连 Slack 时，Slack 会把事件随机分给两边。新机器起 Slack 之前，先在旧机器上停掉：删掉旧机器的 `deploy/secrets/slack.env`，再执行 `docker compose -f deploy/compose.yaml up -d --force-recreate ui`。
- **myorch-knowledge 以新机器为准**：learn、onboard、postmortem 采纳的建议会提交到新机器上的副本。在新机器上推到 GitHub，其他地方只拉取。

### 从另一台电脑使用

myorch 只监听 `127.0.0.1`，并且只接受 Host 是 `127.0.0.1:8800` 或 `localhost:8800` 的请求（防 DNS rebinding），不要改成对外开放。在新机器上打开「远程登录」（系统设置 → 通用 → 共享），然后在你用的电脑上开一个 SSH 端口转发：

```sh
ssh -N -L 8800:127.0.0.1:8800 <用户>@<新机器>.local
```

之后这台电脑上的 http://127.0.0.1:8800/ 就是新机器的控制台。Claude Code channel 也一样：它在你用的电脑上运行（需要 myorch 仓库和 uv），连的 `127.0.0.1:8800` 经过转发到达新机器。

## Slack

Slack 适配器（`src/myorch/channels/slack.py`）在控制台进程里运行：私信发起人、回答提问、审批、`/myorch` 发起运行。设计见 [docs/notes/2026-11-05-slack-adapter.md](../docs/notes/2026-11-05-slack-adapter.md)。

1. 建应用：打开 https://api.slack.com/apps → Create New App → From a manifest → 选 workspace → 粘贴 `deploy/slack-app-manifest.yaml`。
2. 安装：OAuth & Permissions → Install to Workspace。复制 **Bot User OAuth Token**（`xoxb-` 开头）。
3. 建 app-level token：Basic Information → App-Level Tokens → Generate，scope 选 `connections:write`。复制它（`xapp-` 开头）。
4. 写令牌：
   ```sh
   cp deploy/slack.env.example deploy/secrets/slack.env && chmod 600 deploy/secrets/slack.env
   ```
   用编辑器填入两个令牌。`deploy/secrets/` 不进 git，也不进镜像。只有 `ui` 服务读取它。
5. 登记自己：在 Slack 里打开自己的资料 → ⋮ → Copy member ID，写进 `channels.yaml`：`jakobhe: {github: leweii, slack: U...}`。
6. 生效：`docker compose -f deploy/compose.yaml up -d --force-recreate ui`。`docker compose -f deploy/compose.yaml logs ui` 里出现 `slack: connected (Socket Mode)`。
7. 试一下：在 Slack 里给 myorch 发私信，或者输入 `/myorch`。

没登记的人给 bot 发消息时，bot 回复他的 member ID，不执行任何操作。

## Claude Code

本机的 Claude Code 会话可以收到 myorch 的结果（成功、失败、提问、审批），并用工具读日志、发起流程。设计见 [docs/notes/2026-11-05-claude-code-channel.md](../docs/notes/2026-11-05-claude-code-channel.md)。

1. 在 `myorch/` 目录下启动会话（`.mcp.json` 注册了服务器 `myorch`）：
   ```sh
   claude --dangerously-load-development-channels server:myorch
   ```
   第一次会有两个确认：开发用 channel 的警告（选 I am using this for local development），以及新的 MCP 服务器（选 Use this MCP server）。
2. 启动横幅下面出现 `Channels (experimental) messages from server:myorch ...`，说明 channel 生效了。`/mcp` 能看到 `myorch` 和它的工具。
3. 运行结束、提问、审批时，会话里会收到 `<channel source="myorch" ...>` 消息。

- 订阅范围用 `.mcp.json` 里的 `MYORCH_SCOPE` 设置：`mine`（默认）、`session`、`all`。`MYORCH_PROJECTS=blog,mdm` 限定项目。
- 在别的目录也想用时，加到用户级配置：`claude mcp add --scope user myorch -- uv run --quiet --project <myorch 的绝对路径> python -m myorch.channels.claude_code`，启动命令不变。
- Agent 不能审批。审批在控制台或 Slack 里做。
- **channel 只支持 Anthropic 认证**（claude.ai 账号或 Console API key），不支持 Vertex AI、Bedrock、Foundry：在这些提供方上，客户端会静默丢弃通知。这时可以用轮询代替：定时读 `GET http://127.0.0.1:8800/channel/events.json?after=<seq>`，自己记住游标。channel 和轮询不要同时用，同一个事件会被处理两次。

## 常用命令

```sh
docker compose -f deploy/compose.yaml logs -f worker            # worker 日志
docker compose -f deploy/compose.yaml up -d --build worker ui   # 改了 src/ 以后
docker compose -f deploy/compose.yaml exec worker myorch list   # 任何 myorch 命令都这样执行
docker compose -f deploy/compose.yaml down                      # 停止。数据保留在数据目录和 volume 里
```

- 从容器里发起的运行，发起人是 `deploy/.env` 的 `MYORCH_USER`（不设时是宿主机的 `$USER`），不是 root。要以别人的名义发起时加 `--as <名字>`。

## 发布 origin/main

```sh
deploy/update.sh --check   # 要发布哪些提交、会等什么
deploy/update.sh           # 快进 main → 等没有正在执行的步骤 → 重建并重启 worker 和控制台 → 冒烟 → 打 tag deploy-<时间>
```

- 只在 `main` 分支上运行，只能快进。
- 重启会打断正在执行的步骤，所以先等到没有步骤在执行（最多 `WAIT_MAX` 秒，默认 6 小时）。等回答、等审批的运行可以留着。它通过控制台的接口（`MYORCH_URL`，默认 `http://127.0.0.1:8800`）看运行的状态。
- 改了 `src/myorch/engines/` 或 `src/myorch/core/run.py` 时，等所有未结束的运行结束（Temporal 用新代码重放未结束的运行）。
- 镜像从目标提交的干净检出（临时 git worktree）构建，不用工作区：工作区里别人未提交或已暂存的改动不会进镜像。改了 `images/claude-code/` 时也重建 sandbox 镜像。
- 冒烟失败时自动重建上一次发布的提交，失败的提交记在 `deploy/.failed`，origin/main 有新提交之前不会再发布它。
- 开发评测的调度 agent 合并白名单里的改动以后，也用它发布。
- Temporal UI：http://127.0.0.1:8233/

## 端口

全部只发布在 `127.0.0.1`。

| 端口 | 服务 | 能不能改 |
|---|---|---|
| 8800 | 控制台 | `MYORCH_UI_PORT`。容器内外用同一个端口，因为控制台只接受 Host 为 `127.0.0.1:<它自己的端口>` 或 `localhost:<它自己的端口>` 的请求（防 DNS rebinding） |
| 7233 / 8233 | Temporal / Temporal UI | `TEMPORAL_PORT` / `TEMPORAL_UI_PORT` |
| 17671 | gateway | 固定。sandbox 的 supervisor 经宿主机回连这个端口，`gateway.toml` 的 `grpc_endpoint` 写死了它。要改就两处一起改 |

gateway 没有 TLS，也没有用户认证。能连到 17671 的人都能操作它，所以只能发布在 `127.0.0.1`。

## 已知限制

- **本机克隆的 seed 不生效。** `myorch-data/local-repos.yaml` 里写的是宿主机路径（例如 `~/Github/...`），容器里没有这些路径，所以 seed 跳过，`sync-master` 从 GitHub 完整克隆。seed 失败不影响运行。要用 seed，克隆要按宿主机的同一个绝对路径挂进 worker，因为 `seed_repo` 把这个路径交给宿主机的 Docker 解析（`src/myorch/storage/docker_volume.py` 的 `seed_repo`）。
- **`dir` 类型的知识源** 的文件夹要放在 `MYORCH_KB_ROOT`（默认 `~/orca/kb`）下面。compose 只把这个目录挂进控制台和 worker，别处的文件夹在控制台里会报问题。
- **Linux 主机没有测。** supervisor 通过 `host.docker.internal` 回连 gateway。在 Linux 上它指向 docker0 网桥，发布在 `127.0.0.1` 的端口从网桥连不到。见调研笔记的「supervisor 回连 gateway 的路线」。
- `myorch review` 会调用 macOS 的 `open`，在容器里没有这个命令。用 `--no-open`。
- **宿主机上的进程不要直接打开 `myorch-data/events.db`**：它会让容器里的查询报 `disk I/O error`。宿主机上的 CLI 经过控制台读事件，见 [2. 服务和依赖](../docs/02-services.md#文件和配置的读写方)。
- sandbox 镜像和项目开发镜像不在 compose 里，要单独构建。
