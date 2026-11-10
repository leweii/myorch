# myorch 文档

English: [docs/en/README.md](en/README.md)（英文版是网页版的首页）

myorch 是一个流程驱动的 agent 平台。流程用 YAML 写，例如「需求分析 → 实现 → 多人 review → 风险审批 → 推分支」。Temporal 负责调度、重试和等人。每个步骤在一个独立的 OpenShell sandbox 里执行，sandbox 里没有真实凭证。仓库、身份、检查脚本、知识这些项目专有的信息放在项目包里，同一个流程可以用在多个项目上。人在本地控制台（`myorch ui`）或 Slack 私信里和 myorch 交互。本机的 Claude Code 会话可以通过 channel 收到运行结果，再发起后续流程。整套服务用 docker compose 运行（`deploy/`）。

## 目录：四个地方

| # | 位置 | 是什么 | git |
|---|---|---|---|
| ① | `myorch/` | 代码仓库：平台本身（代码、流程、权限、镜像、辅助命令、文档），和项目无关 | `leweii/myorch` |
| ② | `myorch-knowledge/` | 项目包仓库：每个项目怎么做（仓库地址、检查脚本、风险规则、项目知识）。learn 等流程采纳的建议写回这里 | 独立的 git 仓库（私有 `leweii/myorch-knowledge`）。myorch 只在本机提交，不推送 |
| ③ | `myorch-data/` | 本机运行数据：`temporal.db`、`events.db`、评测集、Slack 适配器的状态 | 不进 git |
| ④ | Docker volume `myorch-artifacts` | 运行时的文件：每个运行的工作区、② 的快照、共享缓存。sandbox 只看得到这里 | 不进 git |

①②③ 放在同一个父目录下。sandbox 看不到 ①②③：项目包先 `project sync` 成 ④ 里的快照，再只读挂进去。工作凭证只在 OpenShell gateway 里，由 `deploy/secrets/providers.yaml` 录入。关系图、各目录的内容、改了什么要做什么，见 [1. 概览 · 目录和存储](01-overview.md#目录和存储)。

## 平台特点

| # | 特点 | 怎么做到 | 状态 |
|---|---|---|---|
| 1 | **可移植** | 所有配置都是文件：流程（`flows/*.yaml`）、权限（`profiles/*.yaml`）、项目包（`myorch-knowledge/<项目>/`）、渠道（`channels.yaml`）。框架代码不写任何项目专有的东西。流程引擎只在 `engines/` 这一层，调度逻辑在 `core/`，引擎可以替换。整套服务（包括 OpenShell gateway）打包成 docker compose，换一台机器只要 `docker compose up` 和一个凭证文件 | 本地运行和调试：已实现。docker compose：已实现（macOS）。云端多人：设计目标，现在只支持单机 |
| 2 | **和 agent 的 harness 解耦** | 每种 agent 通过一个 harness adapter 接入（`runner/harnesses/base.py`）。adapter 里写怎么拼命令行、要哪些模型凭证、能不能续接会话。流程只写 `harness: <名字>`，换 agent 不用改流程 | 已接入：claude-code、script。codex、pi、opencode：待接入 |
| 3 | **流程引擎维护开发流程** | 开发流程（需求分析、实现、review、审批、交付）写成 YAML，由 Temporal 调度、重试、等人。一个角色一个流程，所有项目共用。项目专有的信息都在项目包里，流程只读 `{{project.x}}`。流程形状的差异（review 强度、要不要 takeaway）写在 `project.yaml` 的 `flows:` 里，项目专属的 prompt 条款写在 `prompts/` 里，流程用 `if:` 按项目裁剪。同类项目可以共享项目包模板（`_templates/`） | 已实现 |
| 4 | **OpenShell 作为 sandbox 框架** | 每个步骤在一个新的 OpenShell sandbox 里执行，用完删除 | 已实现 |
| 4.1 | 凭证隔离管理 | 工作凭证只在 OpenShell gateway 里。sandbox 里只有占位符，请求出站时由 sandbox 外的 supervisor 换成真实凭证。agent 碰不到任何凭证 | 已实现 |
| 4.2 | sandbox 权限最小化 | 每个步骤按 profile 拿到最少的权限：镜像、网络 policy、要注入的凭证、代码只读还是可写。能访问 GitHub 的步骤不执行仓库代码，执行代码的步骤没有 GitHub 凭证 | 已实现 |
| 5 | **可挂载知识库** | 知识源登记在 `myorch-knowledge/` 里，有三种：本机目录、git 仓库、MCP 服务。每次运行开始时固定每个知识源的快照版本，只读挂进 agent 步骤的 sandbox（`/sandbox/workspace/knowledge/<名字>/`）。MCP 知识源不挂目录，由 agent 通过 MCP 查询，token 由 OpenShell 替换。项目包里还有 myorch 维护的 `guide/` 和仓库自带的文档（`repo_docs`）。learn 流程根据历史运行提出改进，你采纳后写回 `guide/` | 已实现 |
| 6 | **平台改自己** | 项目 `myorch` 用共用的 development 流程（加 `incident` 输入）改 myorch 自己的代码、流程和文档。给出出问题的运行时，先判断原因是不是在平台上 | 已实现，还没有真实运行过 |
| 7 | **多个入口** | 控制台、Slack 私信（发起、回答、审批）、本机 Claude Code 会话（通过 channel 收通知，用工具读日志和发起流程，不能审批）都接在同一个 channel 层上 | 已实现。Claude Code channel 是研究预览 |

## 阅读顺序

| # | 章节 | 什么时候看 |
|---|---|---|
| 1 | [概览：目录和核心概念](01-overview.md) | 第一次看，想知道各个目录的作用和关系，或者忘了某个名词的意思 |
| 2 | [服务和依赖](02-services.md) | 想知道要起哪些服务、谁依赖谁、哪个服务停了会怎样 |
| 3 | [架构](03-architecture.md) | 想理解系统怎么拼起来，或者改代码前 |
| 4 | [安装和启动](04-setup.md) | 新机器上装环境，每天开工时起服务 |
| 5 | [命令参考](05-cli.md) | 想知道某个 `myorch` 命令做什么、有哪些参数 |
| 6 | [流程清单](06-flows.md) | 想知道有哪些流程。每个流程的输入、步骤、什么时候需要你，在[流程手册](workflows/README.md)里 |
| 7 | [项目包](07-projects.md) | 接入新项目，或改项目的检查、风险规则、知识 |
| 8 | [写流程：YAML 语法](08-flow-syntax.md) | 要新写或改一个流程 |
| 9 | [设计原则](09-principles.md) | 想知道系统为什么这样设计，做取舍前 |
| 10 | [使用规范](10-conventions.md) | 凭证、审批、改代码、维护文档的规矩 |
| 11 | [排错](11-troubleshooting.md) | 跑不起来、卡住、报错 |

## 一页速查

```sh
# 服务（都在 myorch/ 目录下）。Docker Desktop 启动后自动起来
docker compose -f deploy/compose.yaml ps              # temporal、gateway、worker、ui 都在跑
docker compose -f deploy/compose.yaml up -d --build   # 改了 src/ 或 flows/ 以后
docker compose -f deploy/compose.yaml run --rm providers   # 改了 deploy/secrets/providers.yaml 以后

# 本机 Claude Code 会话收 myorch 的通知（在 myorch/ 目录下）
claude --dangerously-load-development-channels server:myorch

# 在控制台里发起运行、回复提问、审批。命令行也能做同样的事：
uv run myorch run flows/development.yaml --project blog -i task="..."
uv run myorch list                       # 最近的运行，哪些在等你
uv run myorch status <run_id>            # 一个运行的进度、提问、审批
uv run myorch reply <run_id> <step> "…"  # 回答 agent 的提问
uv run myorch approve <run_id> <step>    # 审批
```

| 界面 | 地址 |
|---|---|
| 控制台 | http://127.0.0.1:8800/ |
| Temporal 界面 | http://127.0.0.1:8233/ |
| Slack | 给 myorch 发私信，或者 `/myorch` |

## 其他文档

- [ARCHITECTURE.md](../ARCHITECTURE.md)：完整的设计文档。本目录讲「怎么用」，ARCHITECTURE.md 讲「为什么」。两者冲突时以代码为准，再修正文档。
- [PLAN.md](../PLAN.md)：实施计划和每一项的实测结果。
- [流程手册](workflows/README.md)（网页版 `docs/zh/workflows/index.html`，英文 `docs/workflows/index.html`）：每个流程做什么、输入、步骤图、每一步做什么、什么时候需要你。文字在 `flows/<流程>.md`（英文在 `docs/en/flows/`），改完重新生成：`uv run --with markdown python docs/workflows/build.py`。
- `myorch-knowledge/README.md`：项目包目录的说明。
- [deploy/README.md](../deploy/README.md)：docker compose 的安装、新机器的安装脚本、凭证文件、Slack、Claude Code channel、发布 origin/main、端口、已知限制。
- `docs/notes/`：各项设计的记录，例如 channel 层、交付接口、gateway 放进 Docker 的调研、Slack 适配器、Claude Code channel。
- `docs/zh/index.html`：本目录的网页版（单页，左侧导航）。英文版 `docs/index.html` 是首页，源文件在 `docs/en/`（写法见 `docs/en/TRANSLATING.md`），中英文一起改。改了任何一章后重新生成：`uv run --with markdown python docs/build-html.py`。
