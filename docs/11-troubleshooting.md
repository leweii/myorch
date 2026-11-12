# 11. 排错

`dc` 是 `docker compose -f deploy/compose.yaml` 的简写，在 `myorch/` 目录下执行。宿主机上的 `openshell` 默认连 brew 版 gateway（已经停了），所以 `openshell` 命令要在容器里执行（`dc exec worker openshell ...`）。

## 先看哪里

| 想知道 | 看 |
|---|---|
| 服务都在不在 | `dc ps -a`：`gateway-init`、`providers`、`init` 是 `exited (0)`，其他是 `running`。更多见 [4. 安装和启动 · 每天开工](04-setup.md#43-每天开工) |
| 运行卡在哪一步、错误信息 | 控制台侧栏里展开运行，看步骤的状态，点开看详情；或 `uv run myorch status <run>` |
| agent 或脚本输出了什么 | 控制台步骤的「日志（最后一轮）」和底部面板的实时输出，或 `uv run myorch logs <run> <instance>` |
| 产物、manifest | `uv run myorch out <run> <instance>`、`uv run myorch ws <run> [path]` |
| workflow 层面的异常（回放失败、activity 堆栈） | Temporal 界面 http://localhost:8233 → 找到运行 → History |
| sandbox 有没有残留 | `dc exec worker openshell sandbox list`。名字的规则见 [5. 命令参考 · 常用的其他命令](05-cli.md#常用的其他命令)，例如 `dev-deve-3f2a9` |
| 凭证在不在 | `dc exec worker openshell provider list` |
| 项目包同步了没有 | `dc exec worker myorch project list` |
| 重试之前的尝试做了什么 | `.myorch/steps/<实例>/logs/turn-<attempt>-<turn>.log`（`myorch ws <run> <路径>`，或 Claude Code channel 的 `log` 工具，`attempt=A`）。重试不覆盖它们，会话记录（`.claude/<实例>/projects/*/<会话ID>.jsonl`）也保留 |
| 平台中断后的重试接着哪个会话 | worker 日志（`dc logs worker`）里 `continues the session` 那一行。重试的 prompt 开头写明「上一次执行被平台中断了」 |
| 修改意见让步骤重跑后，日志编号对不上 | 重跑的 attempt 接着之前的编号（`attempts_before`），控制台显示的「第 N 次尝试」从 1 重新数。按文件名里最大的 attempt 找最新的 |
| 网络被拦、DNS、凭证没注入 | 这一步的 supervisor 日志：`.myorch/steps/<实例>/logs/supervisor-<attempt>-<turn>.log`（`myorch ws <run> <路径>`，或 Claude Code channel 的 `log` 工具，`which=supervisor`） |
| compose 服务的日志 | `dc logs <服务>`，例如 `dc logs ui`（控制台、Slack、watchdog）、`dc logs worker`、`dc logs providers` |

## 常见问题

### 服务和连接

| 现象 | 原因和处理 |
|---|---|
| 命令报连不上 `localhost:7233` | Temporal 没起。`dc ps` 看 `temporal`，`dc up -d temporal`。不用 compose 时运行 `temporal server start-dev --db-filename ../myorch-data/temporal.db` |
| 控制台打不开 | 控制台启动时要连 Temporal。`dc logs ui` 看原因。不用 compose 时先起 Temporal，再 `uv run myorch ui` |
| 宿主机上 `uv run myorch ui` 报端口被占用 | compose 的 `ui` 已经占了 8800。compose 在跑时不要在宿主机上再起 `ui`、`serve`、`worker`：它们会直接打开 `events.db`，见下面的「事件日志」一节 |
| 浏览器或脚本访问控制台得到 403 `bad host` | 控制台只接受 Host 为 `127.0.0.1:<端口>` 或 `localhost:<端口>` 的请求。用 `http://127.0.0.1:8800/` 打开，不要用局域网 IP 或别的主机名。从另一台电脑用时走 SSH 端口转发（见 `deploy/README.md`） |
| 脚本 POST 到控制台得到 403 `missing X-Myorch-UI header` | 控制台的写接口要带请求头 `X-Myorch-UI: 1`，防跨站请求 |
| 控制台和运行都没反应，`dc ps` 什么都没有 | Docker 没在运行。打开 Docker Desktop，服务会自动起来 |
| `run` 打印了 run_id，但一直 `pending` | worker 没起。`dc ps` 看 `worker`；它在等 `init` 和 `providers` 成功（见下一节）。不用 compose 时运行 `uv run myorch worker` |
| 报 `unrecognized arguments: --config .` | 全局参数（`--config`、`--address`、`--data` 等）要写在子命令前面：`uv run myorch --config . worker`。在 `myorch/` 目录下运行时不用写它 |
| 改了 `src/` 没生效 | 重建并重启：`dc up -d --build worker ui`。不用 compose 时重启 `uv run myorch worker` 和 `uv run myorch ui` |
| 改了 workflow 代码后旧运行卡住，Temporal 报 nondeterminism | 旧运行的历史和新代码对不上。取消它，重新发起。预防方法见 [10. 使用规范](10-conventions.md#代码)。`deploy/update.sh` 发现改了 `src/myorch/engines/` 或 `src/myorch/core/run.py` 时，会等所有未结束的运行结束再发布 |
| `status`、`resume` 报 `RPCError: workflow not found for ID: ...` | Temporal 只保留结束 24 小时内的运行。更早的运行用控制台看（状态取自事件日志），或用 `myorch ws`、`myorch export` 读工作区 |
| `myorch list` 里旧运行的状态是 `UNKNOWN` | 同上：Temporal 已经删了它。控制台的列表显示的是事件日志里的结束状态 |

### 事件日志

| 现象 | 原因和处理 |
|---|---|
| 宿主机上的命令报 `读不到事件日志：控制台（http://127.0.0.1:8800）没有响应` | 宿主机上的进程通过控制台读事件，不直接打开 `events.db`。用到事件日志的命令：`run` 带 `from_run` 或输入里写了另一个运行的 id、learn 流程、`eval report`。先开控制台；控制台在别的地址时设 `MYORCH_URL`。或者在容器里执行（`dc exec worker myorch ...`） |
| 控制台 500，日志里是 `sqlite3.OperationalError: disk I/O error` | 见下面的「控制台 500：disk I/O error」 |

### docker compose

| 现象 | 原因和处理 |
|---|---|
| `ui` 一直是 `Restarting` | 启动时读配置失败，看 `dc logs ui`。常见原因是 `channels.yaml` 写错：YAML 的流式映射里冒号后面要有空格，`{github: leweii, slack: U...}`，不能写成 `{github:leweii}` |
| 改了 `channels.yaml` 不生效 | 它在启动时读。执行 `dc up -d --force-recreate ui worker`（要重建容器，因为编辑器保存时会换掉文件） |
| worker 不启动，`providers` 是 `exited (1)` | 凭证文件写错了：只填了一部分值、类型不存在。`dc logs providers` 看原因，改好后 `dc run --rm providers`，再 `dc up -d` |
| 从控制台发起的运行，发起人是 `root` | 容器里没有宿主机的用户名。compose 会传入 `USER`。在没有登录 shell 的环境里起 compose 时，在 `deploy/.env` 里设 `MYORCH_USER` |
| 改了 `flows/`，控制台、Slack、定时触发还在用旧的 | 流程文件打进了镜像。`dc up -d --build worker ui`。宿主机上的 `uv run myorch run` 读的是工作区里的 `flows/`，所以两边可能不一样 |
| `deploy/update.sh` 发布后，本地的改动没进去 | 它用 origin/main 的干净检出构建镜像。没提交、没推送的改动不会进镜像 |
| `deploy/update.sh` 说「上次发布冒烟失败、已回滚」 | 这个提交记在 `deploy/.failed` 里。origin/main 有新提交之前不会再发布它。看冒烟为什么失败：`dc exec worker myorch run deploy/smoke.yaml --wait` |
| `deploy/update.sh` 说「等了 ...s 仍然有运行在跑，没有发布」 | 一直有步骤在执行（或改了 workflow 代码时有未结束的运行）。等它们结束，或调大 `WAIT_MAX` |
| 构建镜像时下载 openshell 超时 | 构建时要从 `github.com` 下载 openshell CLI。先检查本机能不能访问 github.com |
| 新运行从 GitHub 完整克隆，没有用本机克隆 | `local-repos.yaml` 里是宿主机路径，容器里没有。这是已知限制，见 `deploy/README.md` |

### Slack

| 现象 | 原因和处理 |
|---|---|
| bot 回复「你的 Slack 账号（`U...`）没有登记」 | 把回复里的 member ID（`U` 开头）写进 `channels.yaml`：`jakobhe: {github: leweii, slack: U...}`，再 `dc up -d --force-recreate ui worker` |
| 登记了还是没用 | 登记的可能是频道 ID（`D` 开头，私信频道）而不是 member ID。资料页 ⋮ → Copy member ID 复制出来的才是 `U` 开头的 |
| `/myorch` 没反应 | 适配器没连上。`dc logs ui` 里应该有 `slack: connected (Socket Mode)`。有 `SLACK_BOT_TOKEN must start with xoxb- ...; Slack stays off` 时，两个令牌填反或填错了。都没有时检查 `deploy/secrets/slack.env` 是否存在，以及应用是否开了 Socket Mode |
| 运行开始了，但私信里没有线程 | 发起人没有登记 `slack` 账号，这个运行不进 Slack |
| 发起弹窗点了启动，弹窗没关，写着「先填这个任务的字段，再点启动」 | 提交得太快，弹窗还没画出这个任务的字段。填好字段再点启动 |
| 发起弹窗报「没有启动：...」 | 后面是原因，和 CLI 的提交报错一样，见下面的「提交时报错」 |
| 在线程里回复，bot 说「这个运行现在没有在等回复」 | 运行没有在提问。要在完成的运行上接着改，在控制台里点「在这个分支上接着改」 |
| 在两台机器上起了 Slack，事件时有时无 | 两台机器用同一个令牌时，Slack 把事件随机分给两边。只在一台上留 `slack.env` |

### Claude Code channel

| 现象 | 原因和处理 |
|---|---|
| 启动横幅下面没有 `Channels (experimental) messages from server:myorch` | 会话不是用 `claude --dangerously-load-development-channels server:myorch` 启动的，或者启动目录下没有 `.mcp.json`（在别的目录用时，注册成用户级 MCP 服务器，见 `deploy/README.md`） |
| 收不到任何推送；Claude Code 的 MCP 日志（`~/Library/Caches/claude-cli-nodejs/<项目路径>/mcp-logs-myorch/*.jsonl`）里有 `Channel notifications skipped: channels are not available on third-party providers` | Claude Code 跑在 Vertex AI、Bedrock 或 Foundry 上（例如环境变量 `CLAUDE_CODE_USE_VERTEX`）。channel 只支持 claude.ai 账号或 Console API key。myorch 这边照常发出（游标会往前走），是客户端丢弃了。换用 Anthropic 认证启动会话（`env -u CLAUDE_CODE_USE_VERTEX ... claude ...`），或者不用 channel，改为轮询 `GET /channel/events.json?after=<seq>`（例如用 Monitor 定时查） |
| channel 注册了，但收不到推送 | `/mcp` 看 `myorch` 的状态。用 `claude --debug ...` 启动，看 `~/.claude/debug/<session-id>.txt`。`~/.cache/myorch/claude-code-<人>.json` 里的游标应该随运行往前走 |
| 只收到自己的运行 | 默认范围是 `mine`。要看全部运行，`.mcp.json` 里设 `MYORCH_SCOPE=all` |
| Agent 说不能审批 | 这是设计：审批、驳回、修改意见只能由人在控制台、Slack 或 CLI 里做。接口会拒绝 `claude-code` 发来的这些命令（403） |

### 提交时报错

`run` 在提交前检查，出错时不会启动运行。控制台、Slack、定时触发报的是同样的错误。

| 现象 | 处理 |
|---|---|
| `--input expects KEY=VALUE` | `-i` 后面要写 `名字=值` |
| `流程 X 只给项目 ... 用，--project 要写其中一个` | 流程文件的 `projects:` 限定了项目。加对的 `--project` |
| `请先填：<字段名>` | 流程的必填输入没给。例如 mdm 的 development 要 `-i ticket=18700` |
| `project X needs inputs: ticket (-i ticket=...)` | 项目要求这个输入。加上它，或在控制台里填 |
| `project 'X' not found: create ...` | 项目包目录不存在。检查名字，或 `--knowledge` 指向的目录 |
| 用了 `{{project.x}}` 却没给 `--project`，或者报 `... which depends on the project: run it with --project <name>` | 加 `--project <name>`。共用流程按项目裁剪（`if:`），必须知道项目 |
| `project 'X': .../prompts/<文件> is not a prompt slot` | 项目包 `prompts/` 里的文件名只能是 `develop.md`、`review.md`、`verify.md`、`fix.md`、`risk.md`。改名或删掉 |
| `flow references project.<字段>, which the project does not define` | 流程引用了项目没有的字段。检查拼写，或者在 `project.yaml` 里补上 |
| 运行一开始就失败，错误里有 `project not synced: X` | 运行 `uv run myorch project sync X` |
| 片段参数或 presets 报错 | 按报错改。用 `uv run myorch expand <flow> ...` 复现 |
| `runs=auto needs --project` | learn、postmortem 自动选运行时要知道是哪个项目。给 `--project` |
| `project X: 上次 learn ... 之后没有新的已结束的 development / review run` | 没有可以学的新运行。或者用 `-i runs=<id>,<id>` 手动选 |
| `没有这个运行：<id>` | `from_run` 写的运行不存在，检查 run id |
| `<id> 不是 development 运行，不能接着它做` | `from_run` 只能写同一角色（development）的运行 |
| `<id> 属于项目 A，不是 B` | `from_run` 的运行要和 `--project` 是同一个项目 |
| `<id> 还没有结束。等它结束，或者先取消它` | 接着改的运行必须已经结束 |

### 知识源

| 现象 | 原因和处理 |
|---|---|
| `知识源 X 没有登记，或者项目 P 不能用它` | 名字写错，或者是项目的知识源却没给 `--project`。`myorch knowledge list --project P` 看有哪些 |
| `知识源 X 没有快照（同步没有成功）` | 运行开始时的自动同步失败了。运行报错里给出的 `myorch knowledge sync ...` 命令能看到原因 |
| `知识源 X 里有疑似密钥，没有同步` | 删掉列出的内容。确认是误报后，把「文件:行号」写进登记文件的 `allow_findings` |
| `目录不存在：<路径>` | `dir` 知识源的 `path` 写错，或者在这台机器上不存在 |
| `控制台和 worker 只能读宿主机 ... 下的目录` | 容器只读挂载了 `MYORCH_KB_ROOT`（默认 `~/orca/kb`）。把文件夹放到它下面，或者从宿主机的命令行发起。`~` 在容器里指宿主机的 home |
| `... 是一个 Obsidian vault（有 .obsidian/），不能直接挂` | vault 里常有密钥。挂一个你整理过的文件夹 |
| `路径 ... 里的环境变量没有设置` | `path` 里用了没设置的环境变量 |
| `git 知识源 X 需要一个带 git 和 python3 的镜像` | 在登记文件里写 `image`，或者用 `--project` 指定项目（用项目的镜像） |
| 运行开始时提示 `知识源 X 同步失败，用上一次的快照` | git 知识源这次没克隆成功，运行照常开始。在宿主机上执行时多半是因为 `openshell` 连不上 gateway，在容器里执行就好 |
| `knowledge check` 报 `X 是 dir，只有 mcp 知识源能检查连接` | `dir` 和 `git` 用 `myorch knowledge sync` 检查 |
| `knowledge check` 报「要知道用哪个镜像」 | 加 `--project <项目>` 或 `--image <镜像>` |

### sandbox 创建和挂载

| 现象 | 原因和处理 |
|---|---|
| `lstat ... no such file or directory` | 挂载的子目录不存在。正常情况下 `prepare_step` 会建好。手动跑时先建目录 |
| `not admitted by required labels` | volume 没有审批标签。`myorch init` 只给新建的 volume 打标签，已有的不补。检查 `docker volume inspect myorch-artifacts` 的标签，以及 `deploy/gateway.toml` 的 `resource_admission` |
| 工作区写不进去，Permission denied | volume 属主不是 uid 1000。重跑 `myorch init`（或 `dc up -d`，`init` 服务会执行它） |
| 镜像里的工具报 Permission denied | 工具装在了 `/opt`，Landlock 不让读。改装到 `/usr` 下，重建镜像 |
| 挂到 `/sandbox` 以外的路径失败 | Landlock 拦截。挂载点统一放在 `/sandbox/` 下 |
| `image not found` | 镜像没构建。运行 `docker build -t openshell-claude-code:latest images/claude-code`。项目镜像见 [4. 安装和启动](04-setup.md#项目的开发镜像) |
| `sandbox create <名字> failed: ...` | gateway 没起，或镜像、provider 有问题。看后面的原因，`dc logs gateway` |
| sandbox 残留 | sandbox 在每轮结束、失败、取消时正常都会删除。残留的用 `dc exec worker openshell sandbox delete <名字>` 删除 |

### GitHub 和交付

| 现象 | 原因和处理 |
|---|---|
| 运行成功了，但没有 PR | 这是设计：myorch 只推送批准过的提交，不开 PR（2026-11-06 起）。对话和 Slack 里写着推送的分支，PR 由人自己开 |
| API 返回 404 | fine-grained token 没有 Contents 权限（新版界面默认什么都不给），或者仓库不在 provider 规则里 |
| git push 或 clone 返回 401 | git 要用 Basic 认证头传占位符（见 `openshell/providers/github-push.yaml` 的注释）。`flows/lib/` 里的 `*-sync-base.yaml` 和 `*-deliver.yaml` 已经这样做 |
| gateway 拒绝请求 | provider profile 没放行这个路径。改 profile，带 `resource_version` 更新 |
| bot 访问不了别人的仓库 | fine-grained token 只能访问所有者名下的仓库。bot 当协作者时要用 classic token |
| `deliver` 退出码 3：「HEAD 在审批之后变了」 | 批准后分支又有了提交。重新发起运行，重新审批 |
| `deliver` 退出码 3：「当前分支是 base 分支」 | myorch 从不推 base 分支。检查流程的分支设置 |

### 审批和等待

| 现象 | 原因和处理 |
|---|---|
| 批准了，但步骤还在等，`status` 写着「发起人 X 必须是审批人之一」 | 审批要求发起人也批准（`require_initiator`，默认开）。让发起人批一次。CLI 上用 `--as` 写对名字 |
| `approve` / `reply` 报 `'<step>' is not waiting for this (status: ...)` | 这一步不在等审批或回答。用 `status` 看它现在的状态和步骤 ID |
| `changes` 报「不接受修改意见（没有配置 approval.changes）」 | 这个审批只能批准或驳回 |
| `resume` 报「运行还没有结束」 | 还在运行的运行不能 resume。有步骤在等你时，直接回复或审批 |
| `resume` 报「只有失败或取消的运行可以接着执行」或「没有可以重新执行的步骤」 | 运行成功或被驳回了，或者 `--step` 写的步骤没有失败。驳回的审批不会重新打开 |
| 等回答超时失败（`等待用户回复超时`） | `input_timeout` 默认 24 小时。每次回来先看「等你处理」 |
| 审批超时失败（`审批超时：... 内没有人处理`） | 审批写了 `timeout`（development 是 7 天）。到时没人处理，运行失败，什么都不交付。要的话重新发起 |
| 「等你处理」里出现「没有新输出」「原地打转」「卡住了」「偏离了任务」 | watchdog 判断这一步可能没有进展。myorch 不会自动处理：看日志，决定取消、等待，或者等它提问时回复 |

### agent 和检查

| 现象 | 原因和处理 |
|---|---|
| 步骤成功，但状态是 `manifest_missing` | agent 没写或写坏了 manifest，平台兜底生成，下游照常运行。经常出现时，改 prompt 的输出要求 |
| `interaction: never` 的步骤失败，状态里有 question | agent 硬要提问。给它更多上下文，或者改成 `allowed` |
| 循环跑满 | 看 `docs/review/round-N/` 和反思的建议。在控制台回复，或 `myorch reply <run> <loop> 1/2/3` |
| 检查「通过」，但其实什么都没检查 | `checks.sh` 的 bug。工具起不来时必须算失败（core-ng 模板已修） |
| 中文文件名报 `invalid byte sequence in US-ASCII` | 镜像没设 UTF-8 locale。设 `LANG=C.UTF-8` |
| Java 联网报 `BindException: Permission denied` | policy 没放行这个地址。或者 Java 走了 IPv6，需要 `-Djava.net.preferIPv4Stack=true`（core-ng 镜像已处理） |
| Java TLS 握手失败 | 出站 TLS 由 supervisor 的 CA 重签，Java 不读 `SSL_CERT_FILE`。core-ng 镜像的 `java` 启动脚本会生成 truststore |
| onboard 的 `try-checks` 在新仓库上失败 | 仓库不足 5 个提交时 `git rev-list --skip=5` 为空。已改为退回空树（全部历史） |
| learn 的 `project-apply` 报「建议无法干净地应用，知识库未改动」 | 采纳的建议和 `myorch-knowledge` 现在的内容冲突。知识库没有被改动。重新跑 learn |
| learn 的 `project-apply` 报 `... is not a git repository; run git init there first` | `myorch-knowledge` 目录不是 git 仓库。learn 每条建议一个提交，需要 git |

## 步骤因外部原因失败

步骤因为外部原因（超时、token 权限、网络）失败，又不想整个运行重来时：

1. 排除原因（例如换 token、改 provider profile）。
2. `uv run myorch resume <run>`，或在控制台点「重新执行失败的步骤」。已成功的步骤保留结果，见 [5. 命令参考 · resume](05-cli.md#resume)。

流程里可以给步骤写 `on_failed: ask`：失败时运行停下来问你，在控制台里选「重新开始」「跳过」或「终止」，或者 `myorch reply <run> <step> 1/2/3`。

## 控制台 500：disk I/O error

**现象**：控制台返回 500，`dc logs ui` 里是 `sqlite3.OperationalError: disk I/O error`。

**原因**：事件日志 `myorch-data/events.db` 是 SQLite 的 WAL 模式，worker 和控制台在容器里打开它。宿主机上的进程如果也直接打开这个文件，它的锁和共享内存和容器不互通，关闭时会以为自己是最后一个连接，删掉 `events.db-shm`、清空 `events.db-wal`。之后容器里的每次读写都报 `disk I/O error`，事件也记不进去（2026-11-09 遇到过）。

**处理**：等没有正在执行的步骤，把控制台和 worker **一起**停掉再一起启动（`docker stop myorch-ui-1 myorch-worker-1 && docker start myorch-worker-1 myorch-ui-1`）。只重启其中一个不行：两边会各用一份共享内存，可能写坏数据库。

**预防**：

- 宿主机上的 `myorch` 命令通过控制台的 `/channel/events.json` 读事件（`storage/events.py` 的 `HttpEventLog`），不直接打开 `events.db`。
- 例外是 `worker`、`ui`、`serve`：它们直接打开文件。compose 在跑时，不要在宿主机上起这三个命令。
- 自己写脚本也一样：用接口，不要在宿主机上用 `sqlite3` 打开它。只读查看可以复制一份再打开。
- 不要在宿主机上设 `MYORCH_EVENTS_DIRECT=1`，它让命令直接打开文件。
