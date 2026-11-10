# 1. 概览：目录和核心概念

## 一句话

你写一个**流程**（flow），对一个**项目**（project）发起一次**运行**（run）。运行里的每个**步骤**（step）在一个新的 sandbox 里执行。步骤可以是一个 agent（claude-code），也可以是一段 shell 脚本。步骤之间通过共享的**工作区**交接文件。遇到提问或审批时，运行会停下来等人。你在控制台、Slack 或命令行里回复。

## 目录和存储

myorch 的东西放在四个地方。前三个是同一个父目录下的文件夹（`deploy/bootstrap-mac.sh` 默认用 `~/orca/projects/`），第四个是 Docker volume。

```
<父目录>/
├── myorch/              ① 代码仓库：平台本身，和项目无关
├── myorch-knowledge/    ② 项目包仓库：每个项目怎么做
└── myorch-data/         ③ 本机运行数据：Temporal 和 worker 的记录

Docker volume myorch-artifacts   ④ 运行时的文件：工作区、快照、缓存，sandbox 挂的都是它
```

| # | 位置 | 放什么 | 谁写 | git | 换电脑时 |
|---|---|---|---|---|---|
| ① | `myorch/` | 代码（`src/`）、流程（`flows/`）、权限（`profiles/`）、sandbox 镜像、辅助命令（`tools/`）、部署（`deploy/`）、文档 | 你（改平台） | `leweii/myorch` | clone |
| ② | `myorch-knowledge/` | 每个项目的项目包：仓库地址、检查脚本、风险规则、项目知识（`guide/`）、开发镜像。同类项目的模板。知识源的登记 | 你；控制台（登记知识源）；learn、onboard、postmortem 采纳的建议 | 私有仓库 `leweii/myorch-knowledge` | clone |
| ③ | `myorch-data/` | Temporal 数据库、事件日志、评测集、控制台和 Slack 的记录 | Temporal、worker、控制台 | 不进 git | 不用带。新电脑从零开始 |
| ④ | volume `myorch-artifacts` | 每个运行的工作区、项目包和知识源的快照、项目的共享缓存、辅助命令 | worker、sandbox、控制台、`project sync`、`knowledge sync`、`myorch init` | 不进 git | 不用带。`myorch init` 新建，`project sync` 重新做快照 |

凭证不在这四个地方。它们只在 OpenShell gateway 里，由 `deploy/secrets/providers.yaml` 导入（见 [deploy/README.md](../deploy/README.md)）。

### 它们之间的关系

<!-- diagram: storage-relations.html -->

图的文件：[diagrams/storage-relations.html](diagrams/storage-relations.html)。

一次运行里的数据流：

1. **发起**：控制台、Slack、Claude Code channel、CLI 或定时任务读 ① 的流程文件和 ② 的 `project.yaml`、`prompts/`，按项目裁剪流程（`if:`），把 `{{project.x}}` 填好。运行要挂的本机目录和 git 仓库知识源先同步成 ④ 里的快照。然后提交给 Temporal。
2. **执行**：worker 为每个步骤起一个 sandbox，挂上 ④ 里这次运行的工作区（读写）、项目包快照、知识源快照和辅助命令（只读）。宿主机动作（例如 `project-apply`）由 worker 直接执行，不起 sandbox。
3. **记录**：Temporal 把运行状态写进 ③ 的 `temporal.db`，worker 把事件和实时输出写进 ③ 的 `events.db`。控制台读这两个地方。
4. **学习**：learn、onboard、postmortem 提出建议。你采纳后，宿主机动作 `project-apply` 改 ② 并提交一个 git 提交，然后重新做快照。下一次运行就用上了。
5. **交付**：审批通过后，分支推到**项目自己的** GitHub 仓库（`project.yaml` 的 `repo`），不推到这四个地方。myorch 不开 PR，开 PR 和合并由人做。

两条规则：

- sandbox 只看得到 ④，看不到 ①、②、③。项目包和知识源先做成快照放进 ④，再只读挂进去。所以改了 ② 的项目包要 `project sync`，运行才能看到。
- 项目包和知识源的快照都分版本，每次同步写一个新目录。运行开始时，mandate 记下用的是哪个版本，每一步都挂这个版本。所以之后 ② 再改，正在跑的运行不受影响。例外：运行自己通过 `project-apply` 写回了 ②，之后的步骤改挂它写出的新版本。

### ① myorch 仓库的目录

| 目录 | 内容 |
|---|---|
| `src/myorch/` | Python 代码，见 [3. 架构](03-architecture.md#代码地图) |
| `flows/` | 流程。一个角色一个，所有项目共用：`development`、`review`、`learn`、`postmortem`、`eval`、`sweep`、`onboard`。项目之间的差异在项目包里。有哪些流程见 [6. 流程清单](06-flows.md)。`flows/<流程>.md` 是流程手册的文字来源 |
| `flows/lib/` | 片段：`sync-base`、`review-loop`、`e2e`、`deliver`、`choose-proposals` |
| `profiles/` | 权限配置：`dev-git`、`dev-write`、`dev-read`、`ticket-read` |
| `images/claude-code/` | 默认的 sandbox 镜像（装了 claude CLI） |
| `openshell/providers/` | 导入 OpenShell gateway 的 provider profile（Claude、GitHub、Jira、Atlassian MCP 的访问规则） |
| `tools/` | 装进 ④、挂进每个 sandbox 的辅助命令：`myorch-ask`（提问）、`myorch-checks`（跑项目检查）、`myorch-stream`（把 agent 的输出转成可读日志）、`myorch-mcp`（给 agent 接 MCP 知识源，只放行登记的工具）、`myorch-steps`（分段读这次运行前面步骤的 manifest 和日志） |
| `channels.yaml` | 人和各渠道账号的对应关系（`identities`：GitHub、Slack），以及 watchdog 的配置。compose 把它只读挂进容器 |
| `deploy/` | docker compose 和它用到的文件：myorch 镜像、gateway 配置、凭证导入脚本、冒烟测试、Slack 应用清单、安装脚本、发布脚本。凭证在 `deploy/secrets/`（不进 git，不进镜像）。见 [deploy/README.md](../deploy/README.md) |
| `.mcp.json` | 把 myorch 注册成本机 Claude Code 的 MCP 服务器和 channel（`channels/claude_code.py`） |
| `tests/` | 单元测试，不依赖 Temporal 和 OpenShell |
| `docs/` | 本文档。`docs/workflows/` 是流程手册，`docs/notes/` 是设计记录 |
| `ARCHITECTURE.md`、`PLAN.md` | 设计文档、实施计划 |

### ② myorch-knowledge 的结构

```
myorch-knowledge/
├── README.md
├── _templates/core-ng/      同类项目的模板：Core-NG 的 Java 项目
├── blog/                    个人博客 leweii/leweii.github.io
├── mdm/                     公司仓库 example-org/master-data-management，继承 core-ng
└── myorch/                  myorch 自己（leweii/myorch）
```

全局的知识源登记在 `_sources/<名字>.yaml`，现在还没有这个目录。

每个项目目录（模板目录也一样）里的文件：

| 文件 | 作用 | 谁读 |
|---|---|---|
| `project.yaml` | 仓库、base 分支、提交身份、provider 角色映射、镜像、分支名模板、约定、审批人、Jira、sweep 范围、e2e 配置、共用流程的设置（`flows:`）。写了 `extends: <模板>` 时，没写的字段和文件从模板取 | CLI 和控制台在发起时读 |
| `prompts/` | 拼进共用流程 prompt 的项目条款：`develop.md`、`review.md`、`verify.md`、`fix.md`、`risk.md`。模板的在前，项目的在后 | 发起时展开流程 |
| `guide/` | 项目知识：开发指南、review 指南、写作约定 | agent 步骤。learn、onboard、postmortem 会改它 |
| `checks.sh` | 项目的检查：读改动列表，写 `report.md` 和 `result.json` | review 循环的检查步骤、`myorch-checks` |
| `setup.sh` | 准备新的 checkout，例如链接预装的依赖 | 检出分支的步骤 |
| `risk-rules.yaml` | 改了哪些路径要几个人审批 | `risk-rules` 步骤 |
| `security.md` | 安全 review 的重点 | `security` reviewer |
| `policy.yaml` | 需要联网的步骤用的 OpenShell 网络策略 | runner |
| `sources/` | 这个项目登记的知识源（mdm 有） | 知识源挂载。不进快照 |
| `providers/` | 这个项目的 provider profile（mdm 的私有依赖源） | compose 的 `providers` 服务导入 gateway。不进快照 |
| `e2e/` | 端到端测试的用例和脚本（blog） | `lib/e2e.yaml` |
| `image/` | 开发镜像的 Dockerfile 和构建脚本 | 你手动构建。不进快照 |

详细字段见 [7. 项目包](07-projects.md)。

### ③ myorch-data 的内容

| 路径 | 写入方 | 内容 |
|---|---|---|
| `temporal.db` | Temporal dev server | workflow 的状态和历史。已结束的运行保留 24 小时（dev server 的默认值），之后只在事件日志里 |
| `events.db` | worker | 事件日志（`events` 表），以及运行中步骤的实时输出（`output` 表，保留 7 天）。宿主机上的进程不要直接打开它，见 [2. 服务和依赖](02-services.md#文件和配置的读写方) |
| `evals/` | `myorch eval`、postmortem | 评测案例和标准答案 |
| `learn/<项目>/takeaways.json` | learn 的宿主机动作 | learn 已经学过哪些运行的 takeaway |
| `ui/runs/<run_id>.jsonl` | 控制台 | 你在控制台里说过的话 |
| `watchdog.json` | 控制台进程（`myorch serve`） | watchdog 已经报过的告警 |
| `slack/state.json` | Slack 适配器 | 事件游标，每个运行在 Slack 里的线程和消息 |
| `local-repos.yaml` | 你 | 本机克隆的位置，新运行从这里复制代码（compose 里不生效，见 [deploy/README.md](../deploy/README.md#已知限制)） |
| `reviews/<run_id>.html` | `myorch review` | 审批前看改动的单页 |
| `runs/<run_id>/` | `myorch export` | 从 volume 导出的工作区副本。真正的工作区在 ④ |

### 改了什么要做什么

`dc` 是 `docker compose -f deploy/compose.yaml` 的简写。`dc up -d --build` 会打断正在执行的步骤。发布 origin/main 上的提交用 `deploy/update.sh`，它等正在执行的步骤结束再重启（见 [deploy/README.md](../deploy/README.md#发布-originmain)）。

| 改了 | 要做 |
|---|---|
| ① 的 `src/`、`flows/`、`profiles/` | `dc up -d --build`。流程文件打进了镜像，所以改流程也要重建 |
| ① 的 `tools/` | `dc up -d --build`（`init` 服务每次启动都会把 `tools/` 装进 volume） |
| ① 的 `images/claude-code/` | `docker build -t openshell-claude-code:latest images/claude-code` |
| ① 的 `channels.yaml` | `dc up -d --force-recreate ui worker` |
| ① 的 `pyproject.toml`、`uv.lock` | `dc up -d --build`，再重建 myorch 项目的开发镜像：在 ② 里运行 `myorch/image/build.sh <myorch 的路径>` |
| `deploy/secrets/providers.yaml` | `dc run --rm providers` |
| `deploy/secrets/slack.env` | `dc up -d --force-recreate ui` |
| ② 的项目包 | `uv run myorch project sync <项目>`，然后在 ② 里提交 |
| ② 的 `image/` | 运行这个项目的 `image/build.sh` |
| 知识源的内容（本机目录或 git 仓库） | 不用做。发起运行时自动同步：本机目录每次都同步，git 仓库的快照超过 1 小时才重新同步。也可以手动 `uv run myorch knowledge sync <名字>` |

## 名词表

| 名词 | 是什么 | 在哪里 |
|---|---|---|
| **flow（流程）** | 一组步骤，加上它们的依赖、循环和审批。用 YAML 写。开头的 `role:` 说明它的角色。所有项目共用，按项目设置（`flows:`、`prompts/`）和 `if:` 裁剪 | `flows/*.yaml` |
| **fragment（片段）** | 可复用的步骤或循环。流程用 `use:` 引用 | `flows/lib/*.yaml` |
| **run（运行）** | 一次流程执行。ID 是 `<流程名>-<8 位十六进制>`，例如 `development-38b24c5b`，等于 Temporal 的 workflow ID | Temporal，以及 volume 的 `runs/<run_id>/` |
| **label（标签）** | 用来找运行的标记，例如 Jira 工单号 `MD-18700`。工单类输入自动加 | mandate 的 `labels` |
| **step / instance** | 步骤，以及展开后的每个具体实例。例如 `review.r2.x1` 是第 2 轮的第 1 个 reviewer | |
| **harness** | 步骤的执行方式：`claude-code`（agent）或 `script`（bash，别名 `shell`） | `src/myorch/runner/harnesses/` |
| **宿主机动作** | worker 在 sandbox 外执行的确定性步骤，只能是注册过的几个，例如 `project-apply` 写回项目包 | `src/myorch/runner/actions.py` |
| **profile** | 步骤的权限：网络 policy、注入哪些凭证、代码只读还是可写 | `profiles/*.yaml` |
| **provider** | OpenShell gateway 里存的凭证，例如 Claude token、GitHub token。myorch 只知道名字，看不到值 | `docker compose -f deploy/compose.yaml exec worker openshell provider list` |
| **project pack（项目包）** | 一个项目的全部专有信息：仓库、base 分支、账号、镜像、检查脚本、风险规则、知识 | `myorch-knowledge/<name>/` |
| **template（模板）** | 同类项目共享的项目包。项目用 `extends:` 继承 | `myorch-knowledge/_templates/<name>/` |
| **knowledge source（知识源）** | 挂给运行的只读知识：本机目录、git 仓库或 MCP 服务。项目的 `knowledge` 字段写默认挂哪些 | `myorch-knowledge/_sources/`、模板或项目的 `sources/` |
| **workspace（工作区）** | 一个 run 的全部文件：代码、文档、记忆、每步的产物和日志 | volume 的 `runs/<run_id>/`。sandbox 里是 `/sandbox/workspace` |
| **manifest** | 每一步结束时写的 `manifest.json`：状态、摘要、文件列表、结构化输出（`outputs`） | `.myorch/steps/<instance>/out/manifest.json` |
| **outputs** | manifest 里给流程用的值，例如 `{"blocking": 2}`。条件、循环、审批人数都读它 | |
| **mandate** | run 开始时固定的授权记录：发起人、输入、流程版本、项目包版本、知识源版本、标签 | 工作区的 `.myorch/mandate.json` |
| **控制台** | 本地网页（`myorch ui`）。在这里发起运行、看对话和改动、回复提问、审批 | `src/myorch/ui/` |
| **事件日志** | worker 写的 SQLite 表，记录每个 run 的事件（开始、步骤开始和结束、提问、审批、结束） | `myorch-data/events.db` |
| **channels** | myorch 和各平台之间的转发层：转发事件、命令和运行内容。适配器有控制台、Slack、Claude Code channel | `src/myorch/channels/` |

## 两种需要你的时刻

| | 谁决定要问 | 你怎么回应 |
|---|---|---|
| **提问**（`waiting_input`） | agent 在执行中判断需要你做决定。只有写了 `interaction: allowed` 的步骤可以提问 | 在控制台或 Slack 的对话里直接回复。回复原样发给提问的步骤。也可以用 `myorch reply <run> <step> "回答"` |
| **审批**（`waiting_approval`） | 流程规定这一步执行前要有人放行。人数可以由风险判定算出 | 控制台里的「批准」「驳回」按钮。流程允许时还有「提修改意见」，运行回到前面的循环按意见改。命令行用 `myorch approve / reject / changes <run> <step>` |

另外两种情况也会问你：

- 循环跑满轮数后还没收敛：再跑一轮、接受当前结果、还是终止。
- 写了 `on_failed: ask` 的步骤在重试用完后仍失败：重新开始、跳过、终止，或者给一句提示，agent 接着上次的会话再试。

每个流程什么时候需要你，见[流程手册](workflows/README.md)。

## 一个 run 的生命周期

<!-- diagram: run-lifecycle.html -->

图的文件：[diagrams/run-lifecycle.html](diagrams/run-lifecycle.html)。
