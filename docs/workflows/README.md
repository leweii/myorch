# myorch 流程手册

这本手册讲每个流程：做什么、什么时候用、每一步做什么、什么时候需要你。平台本身（服务、架构、命令、项目包、流程语法）在[使用手册](../README.md)里。

每个流程一章。章里的输入表、步骤图和步骤明细都从 `flows/*.yaml` 生成，和 `myorch expand`、控制台「流程」看到的一样，所以不会和流程定义对不上。其余文字写在流程旁边的 `flows/<流程>.md` 里，英文版在 `docs/en/flows/<流程>.md`。改了流程或它的 `.md` 以后，重新生成（中英文手册一起生成）：

```sh
uv run --with markdown python docs/workflows/build.py
```

## 所有项目共用一套流程

每个角色只有一个流程（`flows/development.yaml`、`review.yaml` 等），所有项目共用。项目之间的差别写在项目包 `myorch-knowledge/<项目>/` 里，展开流程时生效，发起时不用选：

- `project.yaml` 的 `flows.development`：review 有多重（`reviewers`、`max_rounds`、`security`、`on_exhausted`），要不要提交 takeaway（`takeaway`），有没有 `incident` 输入（`incident`）。不写时用默认值：3、3、true、`ask`、false、false。
- `prompts/<槽位>.md`：项目自己的 prompt 条款，槽位有 `develop`、`review`、`verify`、`fix`、`risk`。展开时原文拼进对应步骤的 prompt，先放模板的，再放项目的。
- `jira`：有 Jira 时，开发流程有工单输入和 `fetch-ticket`，工单就是需求；没有时第一条消息就是需求。
- 流程里的 `if:` 按项目裁掉步骤和输入。裁掉的输入在表单里看不到。

| 项目 | `flows.development` | `prompts/` | Jira |
|---|---|---|---|
| mdm（Cookbook，master-data-management） | 默认值，加 `takeaway: true` | core-ng 模板的 `develop.md`、`review.md`（Gradle 依赖、`*-interface` 契约） | 有（`MD`） |
| blog（个人博客） | `reviewers: 1`、`max_rounds: 1`、`security: false`、`on_exhausted: accept` | `develop.md`、`review.md`（Jekyll、写作规范、误公开） | 没有 |
| myorch（平台自己） | 默认值，加 `incident: true` | `develop.md`（incident 原因分析、平台的设计检查、合并以后要做的事）、`risk.md` | 没有 |

- 每个项目怎么走开发流程，见 [development 的「按项目的不同」](../../flows/development.md#按项目的不同)。
- 改某个项目的做法，改它的项目包，然后 `uv run myorch project sync <项目>`。改 `flows/` 下的流程或片段，所有项目都受影响。
- 流程文件开头写 `role:`（角色）。myorch 按角色认流程，不按名字。例如「在这个分支上接着改」对 development 角色的运行都有效，eval 回放用 eval 角色的流程。
- 2026-11-03 到 2026-11-10 之间，流程按项目复制过（`ck-`、`blog-`、`mo-` 前缀）。现在合成了一套，原因见 [docs/notes/2026-11-10-one-set-of-flows.md](../notes/2026-11-10-one-set-of-flows.md)。旧运行（例如 `ck-development-…`）照样能看、能接着改。

## 角色

| 角色 | 控制台里的任务 | 用途 | 对外写什么 | 流程 |
|---|---|---|---|---|
| `development` | 开发 | 从需求到推送分支：需求、实现、review、审批、推分支 | 审批通过后推送分支。不开 PR，不推 base 分支 | [development](../../flows/development.md) |
| `review` | 评审 PR | 评审一个已有的 PR，结论显示在控制台 | 不写。只读 PR | [review](../../flows/review.md) |
| `postmortem` | 复盘 | 问题已经进了代码：找出引入它的改动和本该拦住的层，改进知识，生成评测案例 | 只改本地 `myorch-knowledge` | [postmortem](../../flows/postmortem.md) |
| `sweep` | 找 bug | 找现有代码里的 bug，每次看一批文件，只出日报 | 不写 | [sweep](../../flows/sweep.md) |
| `learn` | 学习 | 从历史运行里沉淀知识：改进 `guide/`、记录人做过的决定、加确定性检查 | 只改本地 `myorch-knowledge` | [learn](../../flows/learn.md) |
| `eval` | 评测 | 重放一个评测案例，给 reviewer 打分 | 不写 | [eval](../../flows/eval.md) |
| `onboard` | 接入项目 | 分析仓库、试跑检查，提出项目包的修改 | 只改本地 `myorch-knowledge` | [onboard](../../flows/onboard.md) |

## 什么时候用哪个

| 情况 | 用 |
|---|---|
| 有一个需求要做 | development |
| 改动推上去了，自测发现问题，或者要在这次改动上再改 | development，[在原分支上接着改](#在原分支上接着改) |
| 有一个 PR 要看一眼 | review |
| 跑了几次以后，review 总报同类问题或总漏同类问题 | learn |
| 新接一个仓库，或者仓库结构变了 | onboard |
| 想定期找老代码里的 bug | sweep，用 `myorch schedule` 每晚跑 |
| 改了 reviewer 的 prompt 或模型，想知道变好还是变差 | eval |
| 一个问题已经修好了，想知道当初为什么没拦住 | postmortem（修 bug 本身用 development） |
| 平台自己出了问题（任何项目的运行） | myorch 项目的 development，带上 `incident` |

## 发起一个运行

| 在哪里 | 怎么做 |
|---|---|
| 控制台 http://127.0.0.1:8800/ | 顶栏「＋ 新运行」：先选项目，再选任务（这个项目的每个流程一个按钮），填字段，第一条消息写请求 |
| Slack | `/myorch` 打开发起弹窗：先选项目，再列出这个项目的流程，字段和控制台的一样。运行开始以后，bot 在你的私信里给它开一个线程 |
| 本机 Claude Code 会话 | 用 channel 工具 `start` 发起，例如发现平台问题后发起 myorch 项目的 development。它发起的运行会推送到这个会话 |
| 命令行 | `uv run myorch run flows/<流程>.yaml --project <项目> -i 名字=值`。命令参考见[使用手册 · 5. 命令参考](../05-cli.md) |
| 定时 | `uv run myorch schedule add <名字> --flow <流程名，例如 sweep> --project <项目> --cron '…'`，例如每晚的 sweep、每周的 learn |

- 字段由流程文件的 `inputs` 定义：标签、提示、是否必填、类型。类型有文本、消息、下拉选项、工单号（`MD-18700` 和 `18700` 都可以）、运行（从这个项目已结束的运行里选）。每章的「输入」表列出了它们。
- 消息框写什么也由流程定义，可以按项目不同。development 在有 Jira 的项目（mdm）里，需求在工单里，消息是可选的补充说明；blog 和 myorch 没有工单，消息就是需求，必填。
- 工单输入和输入里的工单号会自动成为运行的标签，运行列表可以按标签搜。
- 发起前，客户端读流程、按 presets 选参数、展开片段、填入项目值、检查。写错了直接报错，不会启动。想先看展开结果：`uv run myorch expand flows/<流程>.yaml --project <项目>`。

## 你介入的地方

流程在下面几种地方停下来等你。控制台顶栏的「等你处理」和 `uv run myorch list` 都会列出来。Slack 线程里有同样的按钮。步骤图里琥珀色的卡片和「可能问你」标记就是这些地方。

| 什么时候 | 控制台 / Slack | 命令行 |
|---|---|---|
| agent 提问 | 在对话里回复。Slack 里点选项按钮，或在线程里直接回复 | `myorch reply <run> <步骤> "回答"` |
| 循环跑满了，问题还没解决 | 先看反思步骤的建议，再回复 1 再跑一轮、2 接受、3 终止 | `myorch reply <run> <循环> 1` |
| 挑选建议（learn、onboard、postmortem 等） | 回复编号，例如 `1,3`、`all`、`none` | `myorch reply <run> choose "1,3"` |
| 审批 | 先看右栏「改动」页（diff、review 结论、风险判定），再点「批准」「提修改意见」（先在输入框写意见）或「驳回」。Slack 里是「批准」「要求修改」「驳回」三个按钮 | `myorch review <run>` 看材料，然后 `myorch approve`、`myorch changes` 或 `myorch reject` |
| 不想再跑了 | 取消 | `myorch cancel <run>` |
| 失败了，原因已经排除 | 「重新执行失败的步骤」，或在一个步骤上点「从这一步重新执行」。同一个运行接着跑，成功的步骤保留结果 | `myorch resume <run>` |

- 提问等太久会超时失败，默认 24 小时。
- 批准绑定提交。批准之后代码变了，交付会拒绝推送，必须重跑。
- 提修改意见把运行送回审批前的循环（例如 `review-fix`）再跑一轮，你的意见会给到修复的 agent。
- 本机 Claude Code 会话能回答提问、从失败的步骤继续，不能审批。

## 在原分支上接着改

用于 development 运行结束以后：自测发现了问题，或者要在这次改动的基础上再改。在控制台里打开那个运行，点「在这个分支上接着改」，第一条消息写这次要改什么。命令行用 `-i from_run=<运行 ID>`。

- 选的运行必须已经结束，属于同一个项目，角色也是 development。没给工单号时，沿用上一次的。
- 新运行用同一个分支名，推送后更新的是同一个分支，所以人开的 PR 也跟着更新。
- 从那个分支的最后一个提交开始。远端分支上有人追加了提交时，从远端的最新提交开始。两者分叉时停下来，要人先处理。
- 上一次的需求、设计、实现记录、风险判定、交付说明和最后一轮 review 结论放在 `docs/previous/`。agent 在它们的基础上写，并单独写明这次改了什么。更早的几次在 `docs/previous/earlier/` 里，一层套一层。
- 人对上一次运行说的话放在 `docs/previous/conversation.md`：agent 问过的问题和人的回答、修改意见、批准或驳回时的意见，以及失败和重新执行。事件日志在宿主机上，sandbox 读不到，所以发起时（命令行、控制台、Slack 都一样）由宿主机写进新运行。agent 把它当作人的决定，和上一次文档里的假设冲突时以它为准。
- 后面的实现、review、审批照常走。第 1 轮 review 只看这次新加的提交，检查和风险判定看整个分支相对 base 的改动。细节见 [development 的「接着改」](../../flows/development.md#接着改from_run)。

## 看懂步骤图

- 从上到下是执行顺序。同一行的步骤并行。一张卡片等的不是上一行的全部步骤时，卡片下面写「等 …」。
- 卡片第二行是执行者：agent（模型 · profile）、脚本（profile）、宿主机动作（注册过的确定性动作，不进 sandbox）、审批。profile 决定 sandbox 的权限，见[使用手册 · 7. 项目包](../07-projects.md)。
- 左边有蓝条的是 agent。斜线底的是宿主机动作。琥珀色是需要你的地方：审批卡片，和标着「可能问你」的步骤。
- 虚线外框是有条件的步骤或循环。鼠标停在「有条件」上能看到表达式。
- 方框是循环：标题写最多几轮、什么时候停、跑满了怎么办。底部的反思步骤只在跑满且要问你时执行，给出建议。
- 「×3 并行」表示同一个步骤起 3 个实例（例如 3 个 reviewer）。
- 图按 mdm 和默认输入展开（mdm 的开发流程步骤最全）。换一个项目，项目包可能裁掉一些步骤、换掉参数（例如 blog 的 review 只有 1 个 reviewer），条件里的项目值也可能不同。用 `myorch expand flows/<流程>.yaml --project <项目>` 或控制台「流程」看某个项目的展开结果。点开图下面的「每一步的执行者、条件、输出」看完整的表达式。

## 改流程

1. 改 `flows/<流程>.yaml` 或片段。写法见[使用手册 · 8. 写流程](../08-flow-syntax.md)。
2. `uv run myorch expand flows/<流程>.yaml --project <项目>` 看展开结果。
3. 同步改 `flows/<流程>.md` 和 `docs/en/flows/<流程>.md`，重新生成这本手册。
4. 跑一次 `uv run pytest`。worker 和控制台在 compose 里时，`docker compose -f deploy/compose.yaml up -d --build` 让它们读到新的流程。
