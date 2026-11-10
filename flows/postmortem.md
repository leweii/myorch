# postmortem

复盘一个已经进了项目代码的问题：找出引入它的提交，判断最早该在哪一层拦住、为什么没拦住，提出知识库修改，并把这次改动做成一个评测案例。用在问题已经修好（或已经 revert）以后，想知道当初为什么没拦住时。

它只分析，不改项目代码，修 bug 用 [development](development.md)。对外只改本地的 `myorch-knowledge`（你采纳的建议）和本机的评测集（`myorch-data/evals/cases/`）。和 [learn](learn.md) 的区别：learn 从一批运行里找规律，postmortem 从一个具体的问题倒查。postmortem 的结论以后也会被 learn 读到。

```sh
uv run myorch run flows/postmortem.yaml --project mdm -i commit=<修复或 revert 提交>
uv run myorch run flows/postmortem.yaml --project mdm -i bad=<引入问题的提交>
```

控制台：先选项目，任务选「复盘」，填「修复或 revert 提交」或「引入问题的提交」中的一个。第一条消息可以写问题的现象和影响，也可以不写；写了存成 `docs/external/request.md`，analyze 会读。

<!-- flow-graph -->

## 输入

- `commit` 和 `bad` 二选一，都不填时运行不会启动。两个都填时，以 `bad` 为准，`commit` 当作修复提交。
- 提交不在任何分支上（例如没合并的 PR）也可以：locate 会按 SHA 从 GitHub 单独拉取。
- `runs` 不用填。发起时客户端填入这个项目最新的 development 和 review 运行（最多 20 个，已结束、失败、取消的都算），locate 从里面挑出碰过这次改动的。

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `locate` | 找到引入问题的提交（见下）、它的 PR，以及哪些 myorch 运行碰过它 | `docs/postmortem/target.json`、`bad.diff`、`fix.diff`（有修复提交时） |
| `replay-before` | 宿主机动作：建评测案例（草稿），另起一个 eval 运行，用现在的知识库回放这次改动。和 trace、analyze 同时进行 | 评测案例，eval 运行 `eval-<id>-before` |
| `trace` | 从碰过这次改动的运行里，列出当时每一层看到了什么：需求文档提到了哪些相关文件、各轮 review 在改动附近（前后 10 行）的发现、CI 结果、风险判定 | `docs/postmortem/trace.json`、`trace.md` |
| `analyze` | 判断问题是什么、最早该在哪一层拦住、为什么没拦住；提出知识库修改；写出期望 reviewer 报出的发现。只读，只写 `docs/postmortem/` | `postmortem.md`、`proposals.json`、`proposals.md`、`follow-ups.md`、`case.json` |
| `choose` | 列出建议，等你回复编号。没有建议时跳过 | `docs/postmortem/accepted.json` |
| `apply` | 宿主机动作 `project-apply`：写回项目包、提交、重新同步快照。一条都没采纳时跳过 | `myorch-knowledge` 的一个提交 |
| `replay-after` | 宿主机动作：把期望的发现写进评测案例。采纳了建议时，用新的知识库再回放一次 | 评测案例更新，eval 运行 `eval-<id>-after` |

`<id>` 是这次 postmortem 运行 ID 的最后一段，例如 `postmortem-1a2b3c4d` 的回放是 `eval-1a2b3c4d-before`。

### locate：怎么找到引入问题的提交

| 给了什么 | 怎么找 |
|---|---|
| `bad` | 直接用 |
| `commit`，提交信息里有 `This reverts commit <sha>` | 被 revert 的提交 |
| `commit`，普通修复 | 对修复改动的行做 `git blame`（纯新增时看新增位置前后 2 行），取出现最多的提交 |

- 引入问题的提交是合并提交时，对比的基准是它的第一个父提交，所以看的是整个 PR。
- 完整历史克隆在工作区外面（`/tmp`），包含修复的历史不进运行的工作区。
- 碰过这次改动的运行：它的分支上有这个提交、而它开始时的 base 上没有；或者它关联的 PR 就是这个提交的 PR。
- 需求取自第一个碰过它的运行的 `requirements.md`（或 `pr.md`）。没有 myorch 运行碰过时，用提交信息和 PR 标题、描述。
- 找不到时这一步失败，并说明原因：提交不在仓库里；修复改的行找不到来源（这时用 `-i bad=` 直接指定）；引入问题的提交是仓库的第一个提交。

### analyze：该拦住的层和原因

| 输出 | 取值 |
|---|---|
| `layer`：最早该拦住的一层 | `requirement` 需求分析该问到；`implementation` 实现指南该避开；`review`；`security`；`risk` 该判成高风险要人看；`ci` 确定性检查；`approval` 审批人该看到；`none` |
| `category`：为什么没拦住 | `no-rule` 知识库和检查都没覆盖；`rule-not-applied` 有规则但没用上；`flagged-not-handled` reviewer 或 CI 指出过，但被反驳、降级或忽略；`human-let-through` 人看到了风险仍然放行；`not-detectable` 事先确实发现不了 |

- 没有 myorch 运行碰过这次改动时，判断的是：如果当时经过 myorch，哪一层该拦住。
- 建议最多 5 条，格式和 learn 一样（精确替换或追加），只改 `guide/`。规则要写成能用到同类问题上的样子，不只写这一个 bug。
- 适合做成 lint、CI、测试的建议写进 `follow-ups.md`，要另起 development 去做。
- `case.json` 只写 reviewer 看改动本身就应该发现的问题。`category` 是 `not-detectable`，或者只有 CI 才能发现时，写 `[]`。

## 回放和评测案例

回放是**单独的 eval 运行**（[eval](eval.md) 流程，[`evals.py`](../src/myorch/evals.py)）。那里的 reviewer 只看到改动本身和需求，看不到这次运行的工作区：事故描述、修复、分析和期望的发现都看不到。eval 运行从 GitHub 克隆仓库，检出引入问题的提交，之后的提交（包括修复）、分支和 reflog 都删掉。

案例 ID 就是这次 postmortem 的运行 ID，存在本机 `myorch-data/evals/cases/<运行 ID>.json`。它这样走到评测集里：

| 时机 | 案例的状态 | 发生了什么 |
|---|---|---|
| `replay-before` | `draft` | 只有改动，还没有期望的发现，评测集里看不到。用现在的知识库回放一次 |
| `replay-after`，`case.json` 有内容 | `pending` | 写入期望的发现，在控制台「流程 → 评测集」里等你确认。采纳过建议时，用新的知识库再回放一次 |
| `replay-after`，`case.json` 是空的 | `rejected` | 没有能事先发现的问题，案例不计分，也不再回放 |
| 你在「流程 → 评测集」里勾选成立的发现 | `confirmed` | 案例进入评测集，以后的 `myorch eval run` 也会用它。一条都不勾时是 `rejected` |

- 案例页显示两次回放各抓到了几条。确认之前按 `case.json` 的全部发现算，确认之后按你勾选的算。
- 命令行对比这次的两次回放：`uv run myorch eval report --batch <postmortem 运行 ID>`。它只按你勾选过的发现打分，所以先确认案例。
- 两个回放步骤都幂等：已经起过的回放不会再起第二次。
- 回放是另一个运行，它失败不影响 postmortem 本身。去 eval 运行里看原因。

## 你介入的地方

| 时机 | 控制台 / Slack | 命令行 |
|---|---|---|
| `choose`：选采纳哪几条 | 在对话里回复编号，或点 `all`、`none` | `uv run myorch reply <run> choose "1,3"` |
| 运行结束后：确认评测案例 | 「流程 → 评测集」里打开这个案例，勾选成立的发现 | 没有命令 |

- 回复的写法和 learn 一样：`1,3`、`1、3`、`all`、`none`；看不懂会再问一次。详细内容在 `docs/postmortem/proposals.md`。
- 一条都不采纳时，不改知识库，也不做第二次回放。

## 写回项目包

和 learn 用同一个宿主机动作 `project-apply`，同样是 `scope: learn`，规则见 [learn 的「写回项目包」](learn.md#写回项目包)：全部校验通过才写、只提交自己写的文件、提交信息是 `learn(<项目>): …` 并带 `myorch-run: <run_id>`、写完重新同步快照、可以 `git revert`。

区别只在读的文件：建议在 `docs/postmortem/proposals.json`，采纳的编号在 `docs/postmortem/accepted.json`。prompt 只让 analyze 改 `guide/`；动作本身按 learn 的范围校验，也接受 `checks.sh`、`checks.env`。

## 产物

都在工作区的 `docs/postmortem/` 下，控制台右栏「文档」能看，也可以 `uv run myorch ws <run> docs/postmortem`。

```
docs/postmortem/
  target.json            引入问题的提交、基准、修复、PR、碰过它的运行、需求
  bad.diff, fix.diff     引入问题的改动；修复（有时）
  trace.json, trace.md   当时每一层看到了什么
  postmortem.md          结论：问题、该拦住的层和原因、证据、建议
  proposals.json, proposals.md, accepted.json   知识库建议，你采纳的编号
  follow-ups.md          要另起 development 的确定性检查建议
  case.json              评测案例里期望的发现
```
