# 5. 命令参考

所有命令的形式是 `uv run myorch <命令>`，在 `myorch/` 目录下执行。参数以 `uv run myorch <命令> -h` 的输出为准。每个命令依赖哪些服务，见 [2. 服务和依赖](02-services.md#每个命令依赖哪些服务)。

## 全局参数

全局参数写在子命令**前面**，例如 `uv run myorch --address host:7233 list`。

| 参数 | 环境变量 | 默认值 | 作用 |
|---|---|---|---|
| `--address` | `MYORCH_TEMPORAL` | `localhost:7233` | Temporal 地址 |
| `--volume` | `MYORCH_VOLUME` | `myorch-artifacts` | 存放工作区的 Docker volume |
| `--config` | `MYORCH_CONFIG` | `.` | myorch 代码目录。在这里找 `flows/`、`profiles/` 和 `channels.yaml` |
| `--knowledge` | `MYORCH_KNOWLEDGE` | `<config 的父目录>/myorch-knowledge` | 项目包目录 |
| `--data` | `MYORCH_DATA` | `<config 的父目录>/myorch-data` | 运行数据目录。worker 的事件日志 `events.db` 在这里（compose 里是 `/data`） |

## 在哪里执行

日常环境是 docker compose（见 [4. 安装和启动](04-setup.md)）。下面的 `dc` 是 `docker compose -f deploy/compose.yaml` 的简写。

| 在哪里 | 怎么执行 | 说明 |
|---|---|---|
| 容器里 | `dc exec worker myorch <命令>` | 环境变量都已设好。所有命令都能用 |
| 宿主机 | `uv run myorch <命令>` | 连 compose 里的 Temporal（`localhost:7233`）和同一个 volume。下面几种情况有限制 |

宿主机上的限制：

| 情况 | 原因 | 怎么办 |
|---|---|---|
| 要读事件日志的命令：`run` 带 `-i from_run=...` 或输入里写了另一个运行的 id、learn 流程、`eval report` | 宿主机上的进程通过控制台的 `/channel/events.json` 读事件，不直接打开 `events.db`（原因见 [11. 排错](11-troubleshooting.md) 的「事件日志」一节） | 先开控制台。控制台不在 `http://127.0.0.1:8800` 时，设 `MYORCH_URL` |
| 要用 sandbox 的命令：`knowledge check`、git 知识源的同步 | 宿主机上的 `openshell` 默认连 brew 版 gateway，它已经停了 | 在容器里执行 |
| `worker`、`ui`、`serve` | 它们直接打开 `events.db` | compose 在跑时，不要在宿主机上再起这三个命令 |

## 按用途分类

| 用途 | 命令 |
|---|---|
| 安装、启动 | `init`、`worker`、`ui`、`serve` |
| 发起运行 | `run`、`expand`、`schedule` |
| 查看运行 | `list`（`runs`）、`status`、`review`、`logs`、`out`、`ws`、`export` |
| 介入运行 | `reply`、`approve`、`reject`、`changes`、`cancel`、`resume` |
| 项目包 | `project list / show / sync / github-profile` |
| 知识源 | `knowledge list / sync / check` |
| reviewer 评测 | `eval collect / list / run / report` |

运行 id 的格式是 `<流程>-<8 位十六进制>`，例如 `development-1f29e33a`。下面的例子都用它。2026-11-10 之前流程按项目分开，旧运行的 id 是 `ck-development-…` 这样。

---

## 安装、启动

### `init`

```sh
uv run myorch init [--tools tools]
```

1. 建 volume `myorch-artifacts`，打上 OpenShell 的审批标签。volume 已存在时不重建，也不补标签。
2. 把 volume 的根目录交给 uid 1000。
3. 把 `--tools` 目录（默认 `tools/`）下的辅助命令装进 volume 的 `tools/`。sandbox 里挂在 `/sandbox/bin`，只读。

| 辅助命令 | 作用 |
|---|---|
| `myorch-ask` | agent 向人提问，这一步暂停到人回复 |
| `myorch-checks` | 对这个运行改过的文件跑项目的 `setup.sh` 和 `checks.sh` |
| `myorch-mcp` | 带上运行的 MCP 知识源启动 agent，只放行登记过的工具。`--check` 给 `knowledge check` 用 |
| `myorch-stream` | 把 Claude Code 的 stream-json 输出转成可读的日志 |
| `myorch-steps` | 列出这次运行的每一步，分段读某一步的 manifest 和日志（也能读 `observed/` 下挂进来的运行）。fix、reflect、takeaway 的 prompt 提到它，reviewer 和 verify 不提 |

可以重复执行。改了 `tools/` 以后要重跑。compose 的 `init` 服务每次 `up` 都执行它。

### `worker`

```sh
uv run myorch worker
```

启动 Temporal worker。所有步骤都由它执行：起 sandbox、等结果、读 manifest、执行宿主机动作、写事件日志（`--data` 下的 `events.db`）。前台运行，按 Ctrl-C 退出。

- **改了 `src/` 下的代码要重启它。** compose 里执行 `dc up -d --build worker`，或者用 `deploy/update.sh` 发布。
- 删除 sandbox 之前，它把 supervisor 容器的日志存成这一步的 `logs/supervisor-<attempt>-<turn>.log`。

### `ui`

```sh
uv run myorch ui [--port 8800] [--host 127.0.0.1]
```

和 `serve` 是同一个命令，见下一节。启动本地控制台 http://127.0.0.1:8800/。

- `--host` 默认是 `127.0.0.1`（或环境变量 `MYORCH_UI_HOST`）。只在容器里设成 `0.0.0.0`，compose 再把端口发布到宿主机的 `127.0.0.1`。
- 控制台只接受 Host 为 `127.0.0.1:<端口>` 或 `localhost:<端口>` 的请求，其他的返回 403 `bad host`。所以容器内外要用同一个端口。

控制台里没有 agent。人在这里发起、回复、审批。控制台是一个工作台：每样东西在编辑区开成一个标签页。运行的标签页就是它的对话，你写的内容原样交给这个运行。

| 区域 | 内容 |
|---|---|
| 活动栏（最左边） | 切换侧栏：「运行」「等你处理」（带数字）「流程定义」。再点一次当前的图标收起侧栏。最下面是主题 |
| 侧栏「运行」 | 项目 › 运行 › 步骤的树。按项目筛选，或按[标签](#标签按工单找运行)或 run id 搜（搜全部运行，不只最近的）。点运行打开它的对话，点 ▸ 展开它的步骤：按 `needs` 的顺序，∥ 表示并行，循环下面是当前这一轮的步骤，有副本的步骤每个副本一行 |
| 侧栏「等你处理」 | 提问、审批、跑满的循环、watchdog 的提醒。点一条打开那个运行的对话，在那里回复或审批 |
| 侧栏「流程定义」 | 所有流程定义、片段、评测集和知识库。点一个在编辑区打开 |
| 编辑区的标签页 | 运行的对话：请求、每一步的进度和摘要、提问、回复、审批、结束，底下是输入框。标题行有「改动」「文档」「流程定义」。步骤：错误、提问、摘要、outputs、产物和最后一轮的日志、知识库读取记录，打开时直接读取。「改动」：相对 base 的 diff、review 结论、风险判定。「文档」：工作区的 `docs/`，图片（例如工单截图）直接显示。文件、新运行、流程定义也各开一个标签页。从树里单击打开的是预览标签（斜体），下一次单击会替换它；双击或在里面输入就固定下来。标签页刷新后还在，地址对应当前标签页（`#run=…&step=…`），可以直接分享 |
| 新运行 | 一个标签页：先选项目，再选任务（这个项目的流程），然后填字段、第一条消息和知识源，右边是这次会跑的步骤。不常用的字段（spec 里 `more: true`）收在「更多选项」里。⌘+Enter 或「启动」。第一条消息原文写进运行的 `docs/external/request.md`，`task` 取第一行 |
| 底部面板 | 实时输出。打开一个正在执行的步骤时自动出现，也可以点步骤的「实时输出」。每个步骤一个标签，可以拉高、最大化、收起。输出保留 7 天，结束的步骤也能回看；「完整日志」打开最后一轮的日志文件 |
| 状态栏 | 事件流是否连上、运行中的个数、等你处理的个数、读不到数据卷的提醒、显示或收起底部面板 |

输入框的内容发到哪里取决于运行的状态，输入框上方会写明：

| 运行的状态 | 输入框和按钮 |
|---|---|
| 有步骤在提问 | 回复原样发给这个步骤，它接着原来的会话继续 |
| 等审批 | 「批准」「驳回」。审批配置了 `changes` 时还有「提修改意见」：输入框里写意见，运行回到 review 循环再改。输入框里的内容同时作为审批备注 |
| 正在执行 | 不能发送。显示正在执行的步骤。右下有「取消这个运行」 |
| 失败或取消 | 不能发送。右下有「重新执行失败的步骤」，等于 [`resume`](#resume) |
| 已结束 | 不能发送。development 类流程有「在这个分支上接着改」：发起一个新运行，`from_run` 填这个运行（见 [`run`](#run)）。其他流程要继续时，发起一次新运行 |

你在控制台里说的话另外记在 `myorch-data/ui/runs/<run_id>.jsonl`。步骤往后走了，对话里仍然能看到问答。控制台怎么工作见 [3. 架构](03-architecture.md#控制台怎么工作)。

### `serve`

```sh
uv run myorch serve [--port 8800] [--host 127.0.0.1]
```

在一个进程里启动控制台、channel 转发层和平台适配器。启动时打印 `myorch serve: console http://127.0.0.1:8800/ · relay /channel/`。

| 路径 | 内容 |
|---|---|
| `/` | 控制台 |
| `/channel/...` | channel 转发层的 HTTP 和 SSE 接口。接口列表见 `src/myorch/channels/http.py` 开头的说明 |

- `myorch ui` 等于 `myorch serve`。
- Watchdog 和 Slack 适配器在这个进程里运行。Slack 只在设了两个令牌时启动（compose 里来自 `deploy/secrets/slack.env`）。
- 同时只能开一个 `myorch serve` 或 `myorch ui`。

设计见 [notes/2026-11-02-channel-layer.md](notes/2026-11-02-channel-layer.md)。

---

## 发起运行

### `run`

```sh
uv run myorch run <flow文件> [--project <name>] [-i KEY=VALUE ...] [--label <标签> ...] [--kb <名字,...>] [--set flows.KEY=VALUE ...] [--wait] [--as <name>]
```

启动一个流程。run_id 打印到标准输出。挂载的知识源和自动同步的提示打印到标准错误。

| 参数 | 作用 |
|---|---|
| `<flow文件>` | 例如 `flows/development.yaml`。流程所有项目共用，按 `--project` 裁剪 |
| `--project` | 项目包名。流程用了 `{{project.x}}` 或 `if:` 时必须给（现有的软件开发流程都用）。流程文件写了 `projects:` 时必须是其中一个。会检查项目要求的输入（例如 mdm 要 `ticket`） |
| `-i KEY=VALUE`（`--input`） | 流程输入，可以给多次。没给的输入取 flow 的 `defaults` |
| `--label` | 再给运行挂一个标签，例如 `--label MD-18700`，可以给多次。工单输入和输入里的工单号不用写，会自动挂上，见[标签](#标签按工单找运行) |
| `--kb` | 在项目默认的知识源上增减：`a,b` 加上，`-a` 去掉。参数叫 `--kb`，因为 `--knowledge` 是指定项目包目录的全局参数 |
| `--set` | 只用于重放（`-i at=...`）：这次运行改用另一个流程设置，给评测做对比。只能改 `flows.` 下的键，例如 `flows.development.reviewers=1`，或者 `flows.ablate=nit-cap,entry-points` 去掉 prompt 里的规则（见 [8. 流程语法 · prompt 里的规则](08-flow-syntax.md#prompt-里的规则rule)）。改过的项目设置记进 mandate，运行挂上 `set:<键>=<值>` 标签，评测按标签分组 |
| `--wait` | 等运行结束，然后打印最终状态（和 `status` 一样） |
| `--as` | 发起人名字，记进 mandate。默认是本机用户名。需要审批时，发起人一般必须是审批人之一 |

提交前，客户端依次做下面这些。任何一步出错，命令直接报错，不会启动运行：

1. 按项目读流程的输入 spec（`if:` 不成立的输入变成 hidden，`required` 表达式求值，前缀来自项目的 Jira key），再读输入。工单输入只留数字（`MD-18700` 和 `18700` 都变成 `18700`，分支名模板再加上前缀）。
2. 流程写了 `projects:` 时，检查 `--project` 在里面。
3. 流程有 `runs` 输入（learn、postmortem）而没给，或给的是 `auto` 时，自动选运行，见下面的「learn 和 postmortem」。
4. 有 `type: run` 的输入（例如 development 的 `from_run`）时，检查那个运行，见下面的「接着一个运行改」。
5. 检查必填的输入（缺了报 `请先填：<字段名>`）和项目要求的输入。
6. 同步并选定知识源的快照：`dir` 知识源每次都同步（没变就不复制），`git` 知识源最新快照超过 1 小时才重新同步，同步失败时用上一次的快照。
7. 读 flow 文件 → 用 presets 选参数 → 按项目裁剪（`if:`，`with:` 里填项目值）→ 展开 `use:` 片段 → 填入项目值（包括 `prompts/` 的条款）→ 检查。

```sh
uv run myorch run flows/development.yaml --project mdm -i ticket=18700
uv run myorch run flows/development.yaml --project blog -i task="给文章页加字数统计"
uv run myorch run flows/development.yaml --project myorch -i task="..." -i incident=development-1a2b3c4d
uv run myorch run flows/review.yaml --project blog -i pr=13 -i depth=quick
```

**接着一个运行改。** development 类流程有 `from_run` 输入（`type: run`）：

```sh
uv run myorch run flows/development.yaml --project mdm -i from_run=development-1f29e33a -i task="自测发现：..."
```

- 那个运行必须已经结束，属于同一个项目，角色也是 development。否则报错。
- 新运行从那个运行的分支接着改。没给 `ticket`、`incident` 时，沿用那个运行的。
- 人对那个运行说过的话（问答、修改意见、审批备注）写进新运行的 `docs/previous/conversation.md`。
- 控制台的「在这个分支上接着改」做的是同一件事。

**learn 和 postmortem。** 不给 `runs`，或者 `-i runs=auto` 时：

- learn：这个项目上一次 learn 之后已结束的 development 和 review 运行。选中的运行打印到标准错误。一个都没有时报错。
- postmortem：这个项目最近的 development 和 review 运行。
- 两种都必须给 `--project`。也可以手动选：`-i runs=<id>,<id>`。

各流程有哪些输入、每一步做什么，见[流程手册](workflows/index.html)。

### `expand`

```sh
uv run myorch expand <flow文件> [--project <name>] [-i KEY=VALUE ...] [--set flows.KEY=VALUE ...]
```

打印展开后的完整流程（片段、presets、项目值和 `prompts/` 条款都代入，按项目的 `if:` 裁剪过），不启动任何东西。改了流程或片段以后，先用它检查，最好对每个项目各看一次（`--project mdm`、`--project blog`、`--project myorch`）。`-i depth=deep` 这类输入用来选 presets。不检查必填的输入。`--set` 和 `run` 的一样，用来先看去掉某条规则以后的 prompt，不要求是重放。

### `schedule`：定时触发

```sh
uv run myorch schedule add <名字> --flow <流程> [--project P] (--cron '0 2 * * *' | --every 1h) [-i k=v ...] [--kb a,b] [--paused] [--allow-overlap] [--as <name>]
uv run myorch schedule list                       # 什么时候触发、下一次是什么时候
uv run myorch schedule pause|unpause|delete|trigger <名字>
```

| 参数 | 作用 |
|---|---|
| `--flow` | `flows/<名字>.yaml` 或只写 `<名字>` |
| `--cron` / `--every` | 二选一。cron 按本机时区。`--every` 写 `1m`、`6h`、`1d` 这样的间隔 |
| `--kb` | 要挂载的知识源，完整列表（和 `run` 的增减写法不同）。不给时用项目默认的 |
| `--paused` | 建好以后先暂停 |
| `--allow-overlap` | 上一次启动的运行还没结束时也启动。默认跳过这一次 |
| `--as` | 它启动的运行的发起人。默认是建 Schedule 的人 |

- 用的是 Temporal Schedule。`trigger` 立刻触发一次。
- 每次触发时才展开流程、解析知识源版本，所以改了 `flows/` 或同步了新快照，下一次触发就会用上。
- 运行的来源记为 `schedule:<名字>`。

---

## 查看运行

### `list`

```sh
uv run myorch list [--limit 20] [--project <name>] [--flow <flow>] [--label <标签> ...]
```

最近的运行，一行一个：时间、项目、流程、状态、run_id、标签、最后一列。`myorch runs` 是同一个命令。

```
11-10 11:19  mdm       development COMPLETED  development-b3538c83               MD-18633
11-10 10:30  mdm       eval-validate-checks FAILED     eval-validate-checks-dbd70585      MD-18633
11-10 09:02  mdm       development RUNNING    development-1f29e33a               MD-18700     等你处理: human-review
```

- 状态是 Temporal 的状态：`RUNNING`、`COMPLETED`、`FAILED`、`CANCELED` 等。Temporal 只保留结束 24 小时内的运行，更早的显示 `UNKNOWN`。控制台的列表会从事件日志里补上它们的状态。
- 最后一列：正在运行时列出等你处理的步骤（提问、审批、跑满的循环）。其他时候是来源链接；2026-11-06 以前的运行可能是它开的 PR（myorch 现在不开 PR）。
- `--flow` 写完整的流程名，例如 `development`。只按名字精确匹配，2026-11-10 之前的运行要写旧名字（`ck-development`）。
- `--label MD-18700`：只列带这个标签的运行（不分大小写，只写 `18700` 也行），或 run_id 里包含它的（`--label 1f29e33a`）。先在全部运行里找，再取 `--limit` 条。给多次时都要满足。

### 标签：按工单找运行

每个运行都带标签（labels），主要是它对应的 Jira 工单，用来找运行。标签来自：

1. 发起时显式给的：CLI `--label`，HTTP `POST /channel/runs` 的 `labels`，Claude Code channel `start` 工具的 `labels`，评测重放（`myorch eval run`）带上案例的工单（如果案例有）。
2. 流程输入：`type: ticket` 的输入加上它的 `prefix`（mdm 的 development 里 `ticket=18700` 就是 `MD-18700`；前缀按运行的项目解析，旧的 `ck-development` 运行按 `development.yaml` 算）；普通字符串输入里写的工单号（`tickets: "MD-1,MD-2"`、分支名里的 `MD-18700`）。只认大写的 `ABC-123`，`UTF-8`、`SHA-256` 这类不算；`task` 这种自由文字不扫。
3. 输入里引用的另一个运行的标签，只看一层：`from_run`，或者值正好是一个运行 id 的输入（例如 `-i run=development-1f29e33a`）。逗号分隔的一批运行（learn 的 `runs`）不算。

发起时，1、2 和能读到的 3 记在 mandate（`.myorch/mandate.json` 的 `labels`）和 `run_started` 事件里。列运行时按 2、3 的规则再算一遍，所以加标签之前的旧运行也能搜到。规则在 `core/inputs.py`（标签）和 `runs.py`（列表时的推导）。

搜索的地方：控制台侧栏「运行」的搜索框；`myorch list --label`；`GET /api/runs?label=`、`GET /channel/runs?label=`（也认 `q=`）；Claude Code channel `runs` 工具的 `label` 参数。控制台和接口连 Temporal 已经删掉的旧运行（结束超过 24 小时）也会列出来，状态取自它的 `run_finished` 事件。

### `status`

```sh
uv run myorch status <run_id>
```

一个运行的完整状态：mandate（发起人、时间、流程和项目包版本）、审批记录和修改意见、每个步骤和循环的状态、摘要、outputs、错误、用的模型。运行在等你时，`status` 直接打印要执行的命令：

```
run development-1f29e33a  flow=development  status=running
  mandate: 发起人 jakobhe · 2026-11-10T01:02:11+00:00 · flow 2367c7f4 · project mdm@c4f0a3d1
  ...
  ⏸ human-review         waiting_approval
      还需要 1 人批准 → myorch approve development-1f29e33a human-review
      要修改 → myorch changes development-1f29e33a human-review "<修改意见>"
```

```
  ? develop              waiting_input     [opus-5-5]
      question: 要兼容旧版 API 吗？
        1. 兼容
        2. 不兼容
      → myorch reply development-1f29e33a develop "<回复>"
```

状态图标：

| 图标 | 状态 |
|---|---|
| `·` | 等待 |
| `▶` | 运行中 |
| `⏸` | 等审批 |
| `?` | 等回答 |
| `✓` | 成功 |
| `✗` | 失败或驳回 |
| `-` | 被上游阻塞 |
| `↷` | 条件不满足，跳过 |
| `⊘` | 取消 |
| `⟳` | 循环（后面是轮次和 `until` 条件） |

步骤行后面可能有 `attempts=N`（尝试次数）、`turns=N`（轮数）、`exit=N`（非 0 的退出码）、`[模型]`。

`status` 读的是 Temporal。结束超过 24 小时的运行，Temporal 已经删了，会报 `workflow not found`。这时用控制台，或用 `ws`、`export` 读它的工作区。

### `review`

```sh
uv run myorch review <run_id> [--no-open]
```

把审批要看的东西写成一个本地网页，然后用 macOS 的 `open` 在浏览器里打开。网页包括：

1. 需求（agent 的理解）和原始请求
2. 改动：提交列表和 diff（相对 `origin/<base>`）
3. 检查结果。项目有端到端测试时，还有冒烟和验收用例的结果
4. 最后一轮核实过的 review 发现
5. 风险判定
6. 实现记录

运行在等审批时，页首写明还差什么，以及 approve / reject 命令。页面写在 `myorch-data/reviews/<run_id>.html`，命令打印这个路径。`--no-open` 只写不打开，在容器里必须用它。控制台的「改动」页显示同样的内容。

### `logs`

```sh
uv run myorch logs <run_id> <instance> [--bytes 20000]
```

一个实例最新一轮的日志（最后 N 字节）：attempt 最大的那次尝试的最后一轮。实例 ID 用 `status` 里显示的，例如 `review.r2.x1`。

- 执行中也能读，内容是到目前为止的输出。
- agent 步骤的日志是可读的活动记录：agent 写的文字、`→` 开头的工具调用、`←` 开头的工具结果（只留前几行）、最后的结果。
- 原始的 stream-json 事件在同一目录的 `turn-<attempt>-<turn>.jsonl`。
- 之前尝试的日志也在这个目录：`.myorch/steps/<实例>/logs/turn-<attempt>-<turn>.log`，用 `myorch ws` 读。2026-11-06 之前的运行是 `turn-<turn>.log`，只有最后一次尝试的。

### `out`

```sh
uv run myorch out <run_id> <instance>
```

一个实例的 manifest 和 `out/` 下的文件列表。没有 manifest 时打印 `manifest: (none)`。宿主机动作（例如 learn 的 `project-apply`）的 manifest 也写在 `out/` 里。

### `ws`

```sh
uv run myorch ws <run_id>                         # 列出工作区文件（默认 3 层）
uv run myorch ws <run_id> --depth 5
uv run myorch ws <run_id> docs/requirements.md    # 读一个文件
```

查看运行的工作区（volume 里的 `runs/<run_id>/`）：

| 路径 | 内容 |
|---|---|
| `repo/` | 代码 |
| `docs/` | 需求、review 记录、CI 报告。`docs/external/` 是外部内容（请求、工单），`docs/previous/` 是接着改时上一个运行的材料 |
| `memory/MEMORY.md` | 同一个运行里各步骤共享的笔记 |
| `.myorch/` | flow 快照、mandate、每步的产物和日志（`steps/<实例>/`） |
| `.claude/<实例>/` | agent 的会话记录 |

文件不存在时打印 `(no such file: <路径>)`。

### `export`

```sh
uv run myorch export <run_id> [目标目录]
```

把工作区从 volume 复制到本地，打印复制了多少个文件。默认目标是 `../myorch-data/runs/<run_id>/`。想用编辑器翻产物时用。不复制 `repo/.git`、`repo/node_modules`、`repo/_site`、`repo/.jekyll-cache` 和 `.claude/`。

---

## 介入运行

### `reply`

```sh
uv run myorch reply <run_id> <step或loop> "<回复>"
```

| 等回答的对象 | 回复 |
|---|---|
| agent 的提问 | 回复作为下一轮的输入，在新 sandbox 里续接同一个会话 |
| 跑满的循环 | `1`（再跑一轮）、`2`（接受当前结果，继续后面的步骤）或 `3`（终止流程） |
| 失败的步骤（`on_failed: ask`） | `1`（重新开始，新会话）、`2`（跳过这个步骤，继续后面的）或 `3`（终止流程） |
| learn / onboard 的 `choose` 步骤 | 要采纳的建议编号，例如 `1,3,4`，或者 `all`、`none` |

只在对象正在等回答（`waiting_input`）时接受，否则报 `'<step>' is not waiting for this (status: ...)`。回复以后不能修改。

### `approve` / `reject`

```sh
uv run myorch approve <run_id> <step> [--as <name>]
uv run myorch reject  <run_id> <step> [--as <name>]
```

审批一个 `waiting_approval` 的步骤。

- 需要多人时，每个人用不同的 `--as` 各批一次。`--as` 默认是本机用户名。
- 审批写了 `require_initiator`（默认）时，发起人必须是审批人之一。还差什么，`status` 里会写。
- 驳回后这一步失败，运行终止，下游步骤不再执行。
- `--as` 是自报的，没有认证，只防误操作，不防伪造。

### `changes`

```sh
uv run myorch changes <run_id> <step> "<修改意见>" [--as <name>]
```

不批准也不驳回，带着修改意见把运行从审批关口送回去。只有审批配置了 `changes` 的步骤接受（例如 development 的 `human-review`：回到 `review-fix` 循环，最多再跑 2 轮）。

- 修改意见写进审批记录，交给循环里的步骤去改。之后的步骤（包括这个审批）重新执行，改完再来审批。
- 修改意见不能为空。没有配置 `changes` 的步骤会报错，只能批准或驳回。
- 控制台的「提修改意见」按钮和 Slack 里的同名按钮做的是同一件事。

### `cancel`

```sh
uv run myorch cancel <run_id>
```

取消运行。myorch 停掉正在执行的步骤、删除它的 sandbox，不再重试。未完成的步骤标为 `cancelled`。

### `resume`

```sh
uv run myorch resume <run_id> [--step <步骤> ...] [--wait] [--as <名字>]
```

从失败的步骤接着执行一个已失败或已取消的运行。用于步骤因为外部原因失败（超时、token 权限、网络），原因排除之后。打印 `<run_id>: running again <实例,...>`。

- 用同一个运行 ID、同一个工作区和同一个 mandate。已成功的步骤（包括已批准的审批）保留结果，不再执行。
- 重新执行的步骤：失败和取消的步骤，以及因为它们而阻塞的步骤。尝试次数接着算（第 2 次、第 3 次）。
- `--step` 只重新执行指定的步骤，可以写实例 ID（`review.r1.x2`）或步骤 ID（`review`，即它所有失败的副本）。可以写多次。
- 被驳回的审批不会重新打开。运行还没结束时会拒绝。Temporal 已经删掉的运行（结束超过 24 小时）不能接着执行。
- `--as` 记进 `run_resumed` 事件，默认是本机用户名。
- 控制台里也可以做。失败或取消的运行，在对话结尾和输入框右下有「重新执行失败的步骤」按钮。失败步骤的详情页有「从这一步重新执行」按钮。

运行会写一个 `run_resumed` 事件（谁、哪些步骤），结束时写一个新的 `run_finished`。

---

## 项目包

```sh
uv run myorch project list                                 # 所有项目和模板，是否已同步，仓库和 base 分支
uv run myorch project show <name>                          # 合并模板之后的完整配置（JSON）
uv run myorch project sync <name>                          # 把项目包快照写进 volume（改了项目包后必须做）
uv run myorch project github-profile <name> [--read-only]  # 生成这个项目的 OpenShell GitHub provider profile
```

- `sync` 打印 `project <name>: N files synced (sha256:...)`。每次同步是一个新版本，新运行用最新的版本，mandate 记下用的是哪个。
- `github-profile` 默认放行这个仓库的推送和 API 读取，以及子模块的只读克隆。`--read-only` 去掉推送、PR 和评论，够 onboard 和读 PR 用。

详见 [7. 项目包](07-projects.md)。

---

## 知识源

```sh
uv run myorch knowledge list [--project <name>]         # 登记了哪些知识源、最新快照、有什么问题
uv run myorch knowledge sync <名字> [--project <name>]  # 给一个 dir 或 git 知识源做快照，写进 volume
uv run myorch knowledge check <名字> [--project <name>] [--image <镜像>] [--call <工具> [--args '<JSON>']]
```

- 不给 `--project` 时只看共享的知识源（`myorch-knowledge/_sources/`）。给了还包括这个项目和它的模板的知识源。
- `list` 每行是名字、类型（`dir`、`git`、`mcp`）、层级、最新快照、位置。不能用的原因在下一行，以 `!` 开头。
- 运行开始时会自动同步，所以平时不用 `sync`（见 [`run`](#run) 第 6 步）。`sync` 用来提前同步，或者看同步失败的原因。没变时打印 `unchanged`。
- `sync` 发现疑似密钥时不写快照，列出「文件:行号」。确认是误报后写进登记文件的 `allow_findings`。
- git 知识源的同步在 sandbox 里做（名字 `kb-<知识源>-<哈希>`），要在容器里执行。

**`knowledge check`** 只检查 `mcp` 知识源，方式和步骤用它时一样：

1. 起一个 sandbox `kcheck-<随机>`：项目的镜像（或 `--image`）、知识源的 provider、`/sandbox/bin`。
2. 在里面运行 `myorch-mcp --check`：连接服务器，把它的工具和登记的工具对比。
3. 给了 `--call` 时，再用登记过的这个工具调用一次，参数是 `--args` 的 JSON（默认 `{}`）。

全部正常时退出码是 0。token 不离开 gateway。要用 sandbox，所以在容器里执行：

```sh
dc exec worker myorch knowledge check atlassian --project mdm --call getJiraIssue --args '{"issueIdOrKey": "MD-18785"}'
```

登记方法见 [7. 项目包 · 知识源](07-projects.md#知识源)。

---

## reviewer 评测

```sh
uv run myorch eval collect                                    # 从过去运行第 1 轮的 review 发现里找候选案例
uv run myorch eval list                                       # 案例，是否已确认
uv run myorch eval run [--depth quick --depth standard] [--case <id> ...] [--as <name>]
uv run myorch eval report [--batch <B>]                       # 每种配置抓到、漏掉、多报了多少
```

1. `collect` 找出候选案例。在控制台的「流程 → 评测集」里勾选确实成立的发现，确认案例。
2. `run` 给每个已确认的案例、每个 `--depth`（`quick`、`standard`、`deep`，默认 `standard`）各起一个重放运行。`--case` 只跑指定的案例。打印批次号。
3. `report` 汇总。`--batch` 只看这一批；也可以是一个 postmortem 运行的 ID，只看它的两次回放。

`report` 要读事件日志，在宿主机上执行时要先开控制台。评测流程的步骤见[流程手册](workflows/index.html)。

---

## 常用的其他命令

`openshell` 要在容器里执行（`dc exec worker openshell ...`），宿主机上的 `openshell` 连的是已经停了的 brew 版 gateway。

| 命令 | 用途 |
|---|---|
| `dc exec worker openshell provider list` | 看 gateway 里有哪些凭证 |
| `dc exec worker openshell sandbox list` | 看现在有哪些 sandbox |
| `dc exec worker openshell sandbox delete <名字>` | 删掉残留的 sandbox |
| `dc restart gateway` | 重启 gateway |
| `dc logs <服务>` | compose 服务的日志，例如 `dc logs worker`、`dc logs ui` |
| Temporal 界面 http://localhost:8233 | 看 workflow 历史、事件、错误堆栈 |

sandbox 的名字：

| 名字 | 是什么 |
|---|---|
| `<流程>-<步骤>-<哈希>` | 一个步骤的一轮。例如 `dev-deve-3f2a9` 是 development 的 develop。流程缩写（`development` → `dev`，`review` → `rev`，`learn` → `lrn`，`postmortem` → `pm`，`sweep` → `swp`，`eval` → `evl`，`onboard` → `onb`；旧的 `ck-development` 运行是 `ck-dev`）。名字里没有项目，要看项目，用 label `myorch.run` 找到运行。步骤取前 4 个字母，哈希取自运行、步骤、尝试和轮次 |
| `kb-<知识源>-<哈希>` | git 知识源的同步 |
| `kcheck-<随机>` | `myorch knowledge check` |
| `job-<哈希>` | 其他一次性任务（例如 watchdog 的判断） |

OpenShell 限 19 个字符，所以名字都是缩写。完整的运行和步骤在 sandbox 的标签 `myorch.run`、`myorch.step`、`myorch.attempt`、`myorch.turn` 里。Docker 里的容器名是 `openshell-default--<名字>-<uuid>`。
