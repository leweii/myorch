# onboard

让 myorch 了解一个项目。它读仓库，拿最近几个提交试跑一次项目包的检查，然后提出补充或修正项目包的建议；你挑选采纳哪几条，写回 `myorch-knowledge/<项目>/`。以后的开发、review 和风险判定都依赖这些知识。

新接一个项目时跑一次，仓库结构变化大时再跑。它对项目仓库什么都不推送，只改本地的 `myorch-knowledge`。和其他流程一样，所有项目共用它。和 [learn](learn.md) 的区别：learn 从跑过的运行里学，onboard 只看仓库本身，能改的文件也更多（检查脚本、准备脚本、审批规则、安全重点）。

```sh
uv run myorch run flows/onboard.yaml --project <项目>
uv run myorch run flows/onboard.yaml --project <项目> -i focus="数据库迁移"
```

控制台：先选项目，任务选「接入项目」。第一条消息就是「要重点看的地方」，可以不写。

<!-- flow-graph -->

## 开始之前

项目包至少要有 `project.yaml`，并且已经 `uv run myorch project sync <项目>`。项目的 GitHub provider 要建好，开发镜像要构建好。步骤见 [7. 项目包 · 接入一个新项目](../docs/07-projects.md#接入一个新项目)。

`focus` 原样接在 analyze 的任务说明后面，例如「前端检查」「数据库迁移」，让它在这些地方多花功夫。

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `sync` | 克隆仓库，切到最新的 base 分支，初始化子模块（[`lib/sync-base.yaml`](lib/sync-base.yaml)） | `repo/` |
| `try-checks` | 有 `setup.sh` 时先跑它，再把最近 5 个提交当作一次样本改动跑 `checks.sh`，记录结果和用时 | `docs/onboard/checks/` |
| `analyze` | 读仓库、仓库自带的文档、当前项目包和试跑结果，写项目概况和修改建议。不改仓库，也不改项目包 | `docs/onboard/report.md`、`proposals.json`、`proposals.md`、`open-questions.md` |
| `choose` | 列出建议，等你回复编号。没有建议时跳过（[`lib/choose-proposals.yaml`](lib/choose-proposals.yaml)） | `docs/onboard/accepted.json` |
| `apply` | 宿主机动作 `project-apply`，`scope: onboard`：校验、写回项目包、在 `myorch-knowledge` 里提交、重新同步快照。一条都没采纳时跳过 | `myorch-knowledge` 的一个提交 |

### try-checks：试跑检查

- 目的：看 `checks.sh` 是否找对了模块、要跑多久，哪些检查因为和改动无关的原因失败（环境、网络、缓存）。
- 样本改动是 base 分支最近 5 个提交。仓库不足 6 个提交时，用全部历史（从空树算起）。改了哪些文件写在 `changed.txt`。
- 检查按 [`checks.sh` 的约定](../docs/07-projects.md#checkssh-约定) 运行，`CHECKS_DIR` 是 `docs/onboard/checks/`。
- 检查没通过、`setup.sh` 失败、没有 `checks.sh`，都只记进结果，这一步照样成功。这些正是 analyze 要看的。

### analyze：要弄清楚什么

| 方面 | 内容 |
|---|---|
| 结构 | 模块怎么划分，依赖关系，哪些是对外契约（接口、协议、数据库迁移），前端在哪 |
| 构建和检查 | 怎么构建，怎么只测受影响的部分，需要哪些私有依赖或环境；`checks.sh` 有没有找对模块、漏了检查、因为无关原因失败 |
| 约定 | 代码风格、测试写法、提交和 PR 约定，和 `guide/` 里写的是否一致 |
| 风险 | 哪些路径的改动需要更多人审批（`risk-rules.yaml` 有没有漏的或误伤的），安全上要特别注意什么 |

- 每条结论注明依据（文件路径）。
- 建议最多 8 条，类型是 `fill-gap`（补知识）、`fix-checks`（修检查）、`fix-rule`（修审批规则）、`security`（安全重点）。
- `guide/` 只写仓库自带文档里没有、而且会影响 agent 判断的内容，措辞简短。
- 需要人回答才能定的问题写进 `open-questions.md`。流程不会问你这些问题。看完以后手改项目包，或带着 `focus` 再跑一次。

## 你介入的地方

| 时机 | 控制台 / Slack | 命令行 |
|---|---|---|
| `choose`：选采纳哪几条 | 在对话里回复编号，或点 `all`、`none` | `uv run myorch reply <run> choose "1,3"` |

- 先看 `docs/onboard/report.md` 和 `proposals.md`：控制台右栏「文档」，或 `uv run myorch ws <run> docs/onboard`。
- 回复写编号就行：`1,3`、`1、3`、`1 3` 都可以。`all` 或 `全部` 是全部采纳，`none`、`无`、`0` 是一条都不要。编号不存在或看不懂时会再问一次。
- 回复一旦被接收就开始执行，不能再改。24 小时不回复，这一步超时失败。

## 写回项目包

| 能改 | 不能改 |
|---|---|
| `guide/` 下的文件，可以新建 | `project.yaml`、`image/`、`policy.yaml`、`providers/`、`sources/` |
| `checks.sh`、`setup.sh` | `checks.env`（learn 能改） |
| `risk-rules.yaml`、`security.md` | 其他文件，包括 `prompts/` |

开发流程按项目的设置（`project.yaml` 的 `flows.development`、`prompts/`，见 [development 的「按项目的不同」](development.md#按项目的不同)）onboard 改不了，要改时由人手改。

修改有三种写法：精确替换（`edits`，`old` 必须在文件里恰好出现一次）、追加（`append`）、整个文件（`content`）。

- 只写项目自己的目录 `myorch-knowledge/<项目>/`，模板 `_templates/<模板>/` 不变。
- `guide/`、`checks.sh`、`setup.sh` 是覆盖式继承：项目只从模板继承的文件，第一次修改时先复制模板的内容再改，所以 `edits` 可以引用快照里看到的原文。
- `risk-rules.yaml` 和 `security.md` 是拼接式继承：快照里是模板的内容加项目的内容，项目自己的文件里只有项目特有的部分。所以 `security.md` 只用 `append`，`risk-rules.yaml` 只用 `content`，写完整的项目规则文件（`rules:` 列表，可选 `default:`）。同步快照时再和模板的规则拼起来。
- 其余规则和 learn 一样，见 [learn 的「写回项目包」](learn.md#写回项目包)：全部校验通过才写，只提交自己写的文件，写完重新同步快照，可以 `git revert`。提交信息是 `onboard(<项目>): #1 标题; …`，正文带 `myorch-run: <run_id>`。

## 产物

都在工作区的 `docs/onboard/` 下。

```
docs/onboard/
  checks/
    changed.txt        样本改动的文件
    report.md          checks.sh 的报告
    result.json        passed、failed、skipped
  report.md            项目概况和结论，每条带依据
  proposals.json, proposals.md   建议
  open-questions.md    要人回答的问题
  accepted.json        你采纳的编号
```
