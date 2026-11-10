# learn

从一个项目过往的运行里沉淀知识，让下一次运行一开始就知道。所有项目共用这一个流程。它把各种信号整理成对项目包的修改建议，你挑选采纳哪几条，然后写回 `myorch-knowledge/<项目>/`。知识库变准了，以后的每次 development 和 review 都受益。

它只改本地的 `myorch-knowledge`：`guide/`、`checks.sh`、`checks.env`。它不推送，不碰项目仓库，也不改流程（对流程的建议只写成文件）。和 [postmortem](postmortem.md) 的区别：postmortem 从一个漏过去的问题倒查，learn 从一批运行里找反复出现的规律。

```sh
uv run myorch run flows/learn.yaml --project mdm                                                 # 自动选运行
uv run myorch run flows/learn.yaml --project mdm -i runs=development-b947d694,review-5d5a21ec     # 手动选运行
uv run myorch schedule add weekly-learn-mdm --flow learn --project mdm --cron '0 9 * * 1'         # 每周一 9 点
```

控制台：先选项目，任务选「学习」。「要学习的运行」可以留空，第一条消息不用写。

<!-- flow-graph -->

## 学哪些运行

不给 `runs` 时，发起的客户端（命令行、控制台、Slack、定时任务都一样）自动选：

- 这个项目上次**完成**的 learn 之后开始的 development、review 和 postmortem 运行；
- 只要已经结束的（成功或失败），取消的不算；
- 最多 20 个，取最新的。`observe_runs` 最多挂载 20 个工作区。

命令行会在开始前列出选了哪些。一个新运行都没有时，运行不会启动，报「没有新的已结束的 development / review run」。`uv run myorch list --project <项目>` 能看到有哪些运行，手动选时用 `-i runs=<id>,<id>`。

失败或超时的 learn 不算完成。下一次 learn 还会选到同一批运行。

## 读哪些信号

| 信号 | 从哪里来 | 怎么进到运行里 |
|---|---|---|
| review 的发现和后来的处理结果 | 各运行的 `docs/review/round-*/`，PR 的状态 | collect 读只读挂载的 `observed/<run_id>/`，到 GitHub 查 PR |
| 人回答过的问题、修改意见、驳回、失败的步骤、重新执行、从哪个运行接着改 | 宿主机的事件日志 | 发起时由客户端写进 `docs/learn/trail.json`（[`trail.py`](../src/myorch/trail.py)）。sandbox 读不到事件日志 |
| CI 失败 | `docs/ci/round-*/result.json` | collect |
| 实现时的假设，和需求、设计的差异 | `docs/implementation.md` 的对应小节 | collect |
| 为什么在分支上接着改 | 接着改的运行的请求（`docs/external/request.md`） | collect |
| 复盘结论和跟进建议 | postmortem 运行的 `docs/postmortem/` | collect |
| 开发运行自己写的任务信息和经验 | 项目仓库 base 分支的 `myorch/<日期>-<run_id>/` | collect 从仓库读。学过哪些，发起时写进 `docs/learn/takeaways-learned.json` |

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `collect` | 汇总上表的信号，判断每条 review 发现后来怎么样了，读仓库里还没学过的 takeaway。脚本只读 GitHub，不执行仓库代码 | `docs/learn/findings.json`、`outcomes.md`、`signals.json`、`signals.md`、`takeaways.json`、`takeaways/` |
| `analyze` | 找规律，提出修改建议，每条都要有证据。只写建议文件，不改任何东西 | `docs/learn/proposals.json`、`proposals.md`、`follow-ups.md`、`flow-proposals.md` |
| `choose` | 把建议列给你，等你回复编号。没有建议时跳过 | `docs/learn/accepted.json` |
| `apply` | 宿主机动作 `project-apply`：校验、写回项目包、在 `myorch-knowledge` 里提交、重新同步快照。一条都没采纳时跳过 | `myorch-knowledge` 的一个提交 |
| `takeaways-learned` | 宿主机动作：把这次读过的 takeaway 记为学过。不论采纳了几条都执行；这次没读到 takeaway 时跳过 | `myorch-data/learn/<项目>/takeaways.json` |

### collect：发现的处理结果

已经标为 fixed 的发现不算。其余每条按下面的顺序判断，取第一个成立的：

| 处理结果 | 含义 |
|---|---|
| `fixed` | 后面某一轮里，同一个文件有一条发现标为 fixed |
| `rebutted` | 标题的前 12 个字出现在某一轮的 `rebuttal.md` 里 |
| `merged_unchanged` / `closed` / `open` | 只看 review 运行：被评审的 PR 已合并、已关闭、还开着 |
| `not_required` | Nit 或 Pre-existing，本来就不阻塞 |
| `unknown` | 以上都不是 |

myorch 不再往 PR 上发评论，所以没有「有人回复了评论」这一类，结果只来自运行自己的记录和 PR 状态。

### collect：仓库里的 takeaway

- 项目设置了 `flows.development.takeaway`（现在只有 mdm）时，[development](development.md) 每次把 `task.md` 和 `takeaway.md` 随改动提交到 `myorch/<日期>-<run_id>/`。PR 合并后它们才在 base 分支上，所以只读得到合并了的。别的项目仓库里没有这个目录，这一类信号就是空的。
- 工作区没有仓库时，collect 做一个不检出文件的浅克隆，用 `git show` 逐个读。
- 跳过学过的，从最早的开始读。每次最多 30 条、300 KB（第一条总会读），其余留给下一次。摘要写明还剩几条。
- 读不到 `takeaway.md`（网络问题、空文件）的不读，也不记为学过，下次再试。连续 3 条读不到就停下。目录名不是 `<日期>-<run_id>` 的跳过。
- 读不到仓库时只记一句固定的话和 git 退出码，不带命令行和 git 的输出。learn 照常进行。
- 学过哪些只记在本机 `myorch-data/learn/<项目>/takeaways.json`（[`takeaways.py`](../src/myorch/takeaways.py)），不写公司仓库，也不写 `myorch-knowledge`。

### analyze：找哪几类知识

最多 8 条，证据不够就少提或不提。不重复知识库和仓库自带文档里已有的内容，措辞简短，沿用现有文件的风格。

| 类型 | 找什么 | 写到哪里 |
|---|---|---|
| `record-decision` | 人在需求分析或设计时回答过的、以后同类问题都适用的决定 | `guide/decisions.md`（没有时新建）：什么情况、怎么做、为什么、出处。以后遇到同类问题照做，不再问人 |
| `fill-gap` | 返工的原因：接着改、复盘结论、实现偏离需求或设计、不得不做的假设 | 开发、设计相关的指南。问的是「知识库早写了什么，第一次就能做对」 |
| `add-rule` | 反复出现的同类问题（至少 2 次运行，或 1 次但影响大） | 实现时要避开的规则，或 `review-guide.md` |
| `relax-rule` | 反复被反驳、或没改就合并的意见 | 放宽 `review-guide.md` |
| `add-check` | 能用脚本判断的反复出现的问题，或反复失败的 CI 检查 | `checks.sh` 或 `checks.env`。只加确定性的检查，会误报的只报警告。没把握写对时写进 `follow-ups.md` |

另外两个文件不会被应用：`follow-ups.md` 是要改项目仓库本身的建议（lint、CI、测试），要另起 development；`flow-proposals.md` 是对 myorch 流程的建议（步骤顺序、提问时机、总失败或多余的步骤）。

takeaway 当作信号，和其他证据一样核实：只采纳能推广到以后的项目知识，只对某个工单成立的不要。证据写成 `<run_id>: takeaway`。流程、平台、环境类的建议进 `flow-proposals.md`。

每条建议的修改写成精确替换（`edits`：`old` 从当前文件逐字复制，在文件里只出现一次）或追加（`append`）。不用 diff，因为 agent 写的 diff 常常行号对不上。

## 你介入的地方

| 时机 | 控制台 / Slack | 命令行 |
|---|---|---|
| `choose`：选采纳哪几条 | 在对话里回复编号，或点 `all`、`none` | `uv run myorch reply <run> choose "1,3"` |

- 问题里每条建议一行，带类型和证据条数。详细内容在 `docs/learn/proposals.md`：控制台右栏「文档」，或 `uv run myorch ws <run> docs/learn/proposals.md`。
- 回复写编号就行：`1,3`、`1、3`、`1 3` 都可以。`all` 或 `全部` 是全部采纳，`none`、`无`、`0` 是一条都不要。编号不存在或看不懂时会再问一次。
- 回复一旦被接收就开始执行，不能再改。想补采纳的，另跑一次 learn，或者手改项目包。
- 24 小时不回复，这一步超时失败。定时发起的 learn 也停在这里等你。

## 写回项目包

`apply` 是宿主机动作 `project-apply`，`scope: learn`（[`actions.py`](../src/myorch/runner/actions.py)、[`project.py`](../src/myorch/project.py)）。sandbox 访问不到 `myorch-knowledge`，所以在宿主机上做。

| 能改 | 不能改 |
|---|---|
| `guide/` 下的文件，可以新建 | `risk-rules.yaml`、`security.md`、`setup.sh` |
| `checks.sh`、`checks.env` | `project.yaml`、`image/`、`policy.yaml`、`providers/`、`sources/` 等其他文件 |

- 只写项目自己的目录 `myorch-knowledge/<项目>/`，模板（例如 mdm 的 `_templates/core-ng/`）不变。项目只从模板继承的文件，第一次修改时先复制模板的内容再改。
- 先在内存里校验所有采纳的修改：路径在允许的范围内、不跳出项目包、每个 `old` 恰好出现一次。**任何一处不通过，就一处都不写**，这一步失败并列出原因。
- 写入后在 `myorch-knowledge` 里提交，只提交这次写的文件（别人暂存的改动不会带进去）。提交信息是 `learn(<项目>): #1 标题; #3 标题`，正文带 `myorch-run: <run_id> step: apply` 和采纳的编号。
- 然后重新同步项目包快照。之后开始的运行用新知识，Mandate 里记录的项目包版本也跟着变。正在跑的运行继续用它们自己的快照。
- 幂等：发现这一步已经提交过，就直接算成功，不写第二遍。
- 某条建议效果不好：在 `myorch-knowledge` 里 `git revert <提交>`，再 `uv run myorch project sync <项目>`。

## 产物

都在工作区的 `docs/learn/` 下，控制台右栏「文档」能看，也可以 `uv run myorch ws <run> docs/learn`。

```
docs/learn/
  trail.json                 发起时写入：事件日志里的问答、意见、驳回、失败、重新执行
  takeaways-learned.json     发起时写入：已经学过的 takeaway 的 run_id
  findings.json, outcomes.md 发现和处理结果，统计表
  signals.json, signals.md   review 以外的信号
  takeaways.json, takeaways/<日期>-<run_id>/   这次读到的 takeaway
  proposals.json, proposals.md                 建议
  follow-ups.md, flow-proposals.md             不会自动应用的建议
  accepted.json              你采纳的编号
```

## 定时跑

`myorch schedule add` 的 `--flow` 要写流程名 `learn`。定时任务和控制台用同一条路径发起，所以自动选运行、写 `trail.json` 和 `takeaways-learned.json` 都一样。

- 上一次定时发起的运行还在跑（例如还停在 `choose`）时，这一次跳过。要同时跑，加 `--allow-overlap`。
- 没有新运行时，这一次不会启动运行。即使仓库里有新的 takeaway 也一样，因为 takeaway 只在 learn 运行里读。
- 运行的发起人是建定时任务的人。`uv run myorch schedule list` 看下次什么时候跑，`pause`、`delete` 停用。
