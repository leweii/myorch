# eval

给 reviewer 打分：把一次历史 review 看过的改动原样重放，让 reviewer 再看一遍，和人确认过的真问题对比，算抓到几条、漏掉几条、多报几条。改了 reviewer 的 prompt、模型或项目包的 `guide/` 以后，用它判断变好还是变差。它对外什么都不写：不推送，不改代码，不改项目包。

一次 eval 运行只重放一个案例、一种评审强度。案例的收集、确认、批量发起和打分都在本机由 `myorch eval` 做（[`../src/myorch/evals.py`](../src/myorch/evals.py)），流程本身只负责准备代码和跑一轮 review。

```sh
uv run myorch eval collect                              # 从历史运行收集候选案例
uv run myorch eval list                                 # 案例和状态
# 在控制台「流程 → 评测集」里，逐个案例勾选哪些发现是真问题
uv run myorch eval run --depth quick --depth standard   # 每个已确认的案例，每种强度各起一次 eval
uv run myorch eval run --case <案例 ID>                  # 只跑某个案例（默认强度 standard）
uv run myorch eval report [--batch <批次>]               # 对比表
```

不要在控制台的「＋ 新运行」里或用 `myorch run` 直接发起它：只有 `myorch eval run`（和 postmortem 的回放）会把案例写进工作区，直接发起时 `prepare` 读不到案例，会失败。Slack 的发起弹窗不列这个流程。

<!-- flow-graph -->

## 案例从哪里来

案例是一次历史 review 第 1 轮看过的改动，加上人确认过的真问题。存在本机的 `myorch-data/evals/cases/<案例 ID>.json`。

| 阶段 | 谁做 | 做什么 |
|---|---|---|
| 收集 | `myorch eval collect` | 扫描所有运行，第 1 轮的 `verified.json` 里有带文件位置的 Important 或 Nit 的 development、review 运行成为候选，案例 ID 就是那次运行的 ID。Pre-existing 不算这次改动的问题。记下比较范围（base 和 head 提交）、分支上的提交、当时的需求文档（`docs/requirements.md`，没有时用 `docs/pr.md`）和这些发现。已经有的案例不重复收集 |
| 确认 | 你，在控制台「流程 → 评测集」 | 逐条勾选确实是问题的发现，点「收入评测集」，状态变成已确认。一条都不是时点「都不是真问题」，案例不要了。没有记录 head 的老运行，head 是推断的（分支上的第一个提交），页面上会提示你核对 |
| 重放 | `myorch eval run` | 每个已确认的案例、每种强度起一次 eval，一批运行共用一个批次号。用项目包最新的版本，不挂知识源 |
| 打分 | `myorch eval report` | 读每次重放的 `docs/review/round-1/verified.json`，和勾选的发现对比，见「怎么打分」 |

[postmortem](postmortem.md) 也会生成案例（`origin: git`）：改动是项目仓库里引入问题的那个提交，不来自 myorch 运行，案例 ID 是复盘运行的 ID。复盘自己发起这些回放：用当时的知识库一次，你采纳了它提出的知识库修改时，用新的知识库再一次，`myorch eval report --batch <复盘运行 ID>` 能看。

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `prepare` | 读 `docs/eval/case.json`。从源运行自己的工作区克隆仓库（只读挂载，所以从没推到 GitHub 的提交也能重放）；复盘案例从 GitHub 克隆。切到案例的 head，然后删掉 `origin` remote、其他所有引用和 reflog，做一次 gc，再把 `origin/eval-base` 指向案例的 base。把当时的需求文档写进 `docs/eval/requirement.md` | `repo/`、`docs/eval/requirement.md` |
| `review-round` 循环（1 轮） | 和 [review](review.md) 一样的一轮 review：`ci` → `review`（个数由强度决定）和 `security` → `verify`。比较基准是 `eval-base`，需求文档是 `docs/eval/requirement.md`。没有 `fix`，跑满自动接受 | `docs/ci/round-1.md`、`docs/review/round-1/` |

强度和 review 的三种一样，见上面的预设表。

## 不泄露答案

- **标准答案不进工作区**：`myorch eval run` 只把案例里能看的字段写进 `docs/eval/case.json`：来源、源运行、流程、仓库、base 和 head 提交、需求文档。人确认的发现留在本机的 `myorch-data/evals/cases/`。
- **后来的提交不留**：源运行后来的修复提交会告诉 reviewer 问题在哪里，所以仓库里只留 head 能到达的历史，分支、远端引用、reflog 都删掉。
- **对外不可写**：删掉 `origin` 以后，重放里的任何一步都推不出去。
- reviewer 也看不到源运行的技术设计、实现记录和工单原文：工作区里只有需求文档和改动。所以评测比真实开发流程里的 review 少了这些输入。

## 怎么打分

在本机算，流程里不算：

- 只比较 Important 和 Nit，两边都一样。
- **抓到**：重放的某条发现和一条勾选的发现在同一个文件，行号相差不超过 10（任意一个位置对上就算）。
- **漏掉**：勾选的发现里没被抓到的。
- **额外**：重放报了、但没对上任何勾选发现的。它们可能是误报，也可能是当时没人标出的真问题。单独列出其中的 Important 条数。
- `report` 先列每次运行（强度、案例、抓到/期望、额外），再按强度汇总：案例数、期望、抓到、漏掉、召回率（抓到 ÷ 期望）、额外、额外的 Important。复盘的回放按「强度·before」「强度·after」分开。还没跑完的运行不计入，最后提示还有几个在跑。

## 你介入的地方

| 时机 | 控制台 | 命令行 |
|---|---|---|
| 收集以后 | 「流程 → 评测集」里逐个案例勾选真问题 | 只能在控制台确认；`myorch eval list` 看状态 |
| 跑完以后 | 打开每次运行看 `docs/review/round-1/` | `myorch eval report --batch <批次>` |

运行本身没有提问和审批。
