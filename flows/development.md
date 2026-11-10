# development

开发流程：从一个需求做到推送分支。所有项目共用这一个流程。它在一个会话里写需求、技术设计、改代码，再经过 review、风险判定和人工审批，最后把批准的那个提交推到这次运行的分支。myorch 不开 PR，也不合并：PR 由人在 GitHub 上开，合并也由人决定。审批之前改动不离开沙箱，什么都不推送。

项目之间的差别都在项目包里，发起时不用选（见[按项目的不同](#按项目的不同)）：

- review 有多重：几个 reviewer、最多几轮、要不要单独的 security、跑满以后怎么办（`project.yaml` 的 `flows.development`）。
- 有没有 Jira：有时先由 `fetch-ticket` 读工单，工单就是需求；没有时第一条消息就是需求。
- 要不要把任务信息和经验（takeaway）提交到仓库的 `myorch/` 目录（`flows.development.takeaway`）。
- 有没有 `incident` 输入：平台出了问题时，把出问题的运行挂给 `develop` 看（`flows.development.incident`）。
- 项目自己的 prompt 条款（`prompts/develop.md`、`review.md`、`verify.md`、`fix.md`、`risk.md`），展开流程时原文拼进对应步骤的 prompt。

```sh
uv run myorch run flows/development.yaml --project mdm -i ticket=18700
uv run myorch run flows/development.yaml --project mdm -i ticket=18700 -i task="只改后台接口，前端另开工单"
uv run myorch run flows/development.yaml --project blog -i task="给文章页加字数统计"
uv run myorch run flows/development.yaml --project myorch -i task="..." -i incident=development-1a2b3c4d
uv run myorch run flows/development.yaml --project mdm -i from_run=<上一次的运行 ID> -i task="自测发现：批量导入时没有校验"
uv run myorch run flows/development.yaml --project mdm -i ticket=18700 -i at=<PR 的 merge-base> -i ticket_as_of=2026-10-01T08:00:00Z
```

控制台：先选项目，任务选「开发」。表单只显示这个项目用得上的字段。

- 有 Jira 的项目（mdm）：填工单号（`MD-18700` 或 `18700`）。第一条消息是可选的补充说明：写工单里没有的重点和限制。
- 没有 Jira 的项目（blog、myorch）：第一条消息就是需求，必填，可以写多行。
- myorch：平台在某次运行里出了问题时，在「出问题的运行」里填那次运行的 ID。本机 Claude Code 会话发现平台问题时，也用 channel 工具 `start` 发起，第一条消息写看到的现象和初步分析。

消息的第一行成为 `task`，全文写进 `docs/external/request.md`。

<!-- flow-graph -->

上面的图按 mdm 展开，步骤最全。blog 和 myorch 没有 `fetch-ticket` 和 takeaway 四步，blog 的 review 循环只有 1 个 reviewer、没有 `security`。

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `sync-master` | 克隆或更新仓库，强制对齐 `origin/<base>`，初始化子模块。第一次克隆走项目共享目录里的镜像仓库，之后只拉增量（[`lib/sync-base.yaml`](lib/sync-base.yaml)） | `outputs.base_sha` |
| `checkout-branch` | 建这次运行的分支，分支名来自项目包的 `branch`（mdm 是 `MD-<工单号>-myorch-<运行 ID>`，blog 和 myorch 是 `myorch/<运行 ID>`），执行项目的 `setup.sh`。接着改时切到上一次的分支，重放时切到 `at`，见后面两节 | `outputs.branch`、`outputs.since` |
| `fetch-ticket` | 只在项目有 Jira 时。用 Jira REST API 读工单（只读），写成 `docs/external/ticket.md`，下载截图。见「读工单」 | `docs/external/ticket.md`、`docs/external/attachments/` |
| `develop` | 一个会话里做完：写需求文档、写技术设计、需要人决定的问题一次问完、改代码、跑检查、写实现记录、提交一次。见「develop」 | `docs/requirements.md`、`docs/design.md`、`docs/implementation.md`、一个提交 |
| `review-fix` 循环 | `ci`（项目的 `checks.sh`）→ `review`（个数按项目）和 `security` 并行 → `verify` 核实、算出 `blocking` → `fix`（只在 `blocking > 0` 时）→ `pack-choose`、`pack-apply`（只在 `fix` 提了项目包修改时）。轮数和跑满以后怎么办按项目。见「review 循环」 | `docs/ci/round-N.md`、`docs/review/round-N/` |
| `takeaway-trail` | 只在 `flows.development.takeaway`（mdm）时。宿主机动作：把运行到现在的过程写进 `docs/takeaway/trail.json` | `trail.json` |
| `takeaway` | 同上。只读的 agent：写任务信息 `task.md` 和经验 `takeaway.md` | `docs/takeaway/task.md`、`takeaway.md` |
| `takeaway-redact` | 同上。宿主机动作：删掉两个文件里像密钥的行 | |
| `takeaway-commit` | 同上。把两个文件提交到仓库的 `myorch/<日期>-<运行 ID>/` | 一个提交 |
| `risk-rules` | 按规则算审批人数：改动路径和内容、review 没解决的问题、检查失败、没经过 review 的提交 | `outputs.approvers`、`head_sha` 等 |
| `classify-risk` | 读实际的 diff，独立判断要几个人审批，给审批人写风险说明 | `docs/risk.md` |
| `human-review` | 人工审批，人数见「风险和审批」。人数为 0 时自动通过 | |
| `deliver` | 核对 HEAD 就是批准的提交，推送分支，写交付说明 | `docs/delivery.md`、`outputs.delivery` |

- `develop` 判断不需要改代码（`proceed` 为 false）时，后面的步骤全部跳过，运行正常结束。
- 没有 takeaway 的项目，四步在展开时就去掉了，`risk-rules` 直接等 `review-fix`。
- 流程顶层是 `on_failed: ask`：步骤重试后仍失败时问你。

## 你介入的地方

| 时机 | 控制台 | 命令行 |
|---|---|---|
| `develop` 改代码前提问 | 在对话里回复，或点选项 | `myorch reply <run> develop "回答"` |
| 采纳项目包修改 | 回复编号，例如 `1,3`、`all`、`none` | `myorch reply <run> pack-choose.r<N> "1,3"`（N 是轮次） |
| review 跑满仍有阻塞问题（`on_exhausted: ask` 的项目） | 看反思建议，回复 1、2 或 3 | `myorch reply <run> review-fix 1`（1 再跑一轮，2 接受当前结果，3 终止） |
| 某一步重试后仍失败 | 回复 1、2、3，或者写一句提示 | `myorch reply <run> <步骤> 1`（1 新会话重来，2 跳过，3 终止。写别的文字是提示，在原会话里接着做） |
| 需要审批 | 右栏「改动」页看 diff、review 结论和风险判定，点「批准」「要求修改」或「驳回」 | `myorch review <run>` 打开汇总页；`myorch approve <run> human-review`、`myorch changes <run> human-review "意见"`、`myorch reject <run> human-review` |
| 推送以后 | — | 在 GitHub 上用推送的分支开 PR（交付说明可以直接当 PR 描述），决定是否合并。myorch 项目合并以后还要发布，见「myorch：合并以后」 |

takeaway 的四步失败时不问你（`on_failed: skip`），也不阻塞后面的步骤。宿主机动作失败时也不会问。

## 产物

同一个运行的所有步骤共享一个工作区。控制台右栏的「文档」能看所有文档和截图。命令行用 `myorch ws <run>` 列出文件，`myorch ws <run> docs/risk.md` 读一个文件。

```
observed/<运行 ID>/           接着改时上一次的运行、有 incident 时出问题的运行（只读）
docs/
├── external/
│   ├── request.md            第一条消息的原文（控制台和 Slack 发起时）
│   ├── ticket.md             工单（fetch-ticket，或 develop 用 MCP 读的）
│   └── attachments/          工单里的图片
├── previous/                 接着改时：上一次的文档和对话
├── requirements.md           需求、验收标准、工单对照；有 incident 时开头是原因分类
├── design.md                 影响分析、方案、兼容和回滚、测试计划
├── implementation.md         改动、证据、检查结果、假设、和设计的差异；fix 追加「第 N 轮修复」
├── ci/round-N.md             每轮的检查报告（round-N/ 里有 result.json、head.txt）
├── review/round-N/           reviewer-K.json、security.json、verified.json、summary.md、rebuttal.md、approver.json、pack/
├── takeaway/                 trail.json、task.md、takeaway.md（只在 takeaway 的项目）
├── risk.md                   风险结论和请审批人重点检查的地方
└── delivery.md               交付说明
repo/myorch/<日期>-<运行 ID>/  task.md、takeaway.md（提交在分支上，只在 takeaway 的项目）
```

## develop

一个 agent 从读需求做到提交代码，三个阶段按顺序：需求、技术设计、实现。不能跳过前两个阶段直接写代码。项目的 `prompts/develop.md` 原文拼在 prompt 的「输入」之后，见[按项目的不同](#按项目的不同)。

- **输入**：工单 `docs/external/ticket.md`（有时）、截图、请求原文 `docs/external/request.md`、项目约定、仓库自带的知识、项目包的 `guide/`（其中 `decisions.md` 是以前人做过的决定）。请求原文和工单是外部数据，里面的指令不执行。
- **需求文档**：目标、范围、明确不做的事、可检查的验收标准、工单对照表、预计改动的文件、风险、依据。
  - 工单对照表：工单里的每一条要求照抄一行（包括评论里补充的），对应哪条验收标准，或者为什么不做。一条要求里有几个动作，验收标准就要覆盖每个动作。
  - 工单点名的模块、功能、场景不能由 agent 自己划进「明确不做的事」。想排除时要问人。
- **技术设计**：影响分析（调用方、跨模块依赖、其他服务）、方案和不选的方案、兼容和回滚、测试计划（每条验收标准的测试，加上边界和反例）、风险。设计里不写代码。
  - 列全入口：给字段加校验或要求多处一致时，至少用报错文案和字段名两种方式反查，在设计里写出搜索命令和找到的入口。
  - 删除或放宽已有的判断前，用注释和 `git log -L`、`git blame` 查清它为什么存在。查不清、或改动后它保护的东西不再成立时，问人。
  - 先找同类的已有实现：能直接用就调用；形状不合适就改成能共用的形状；行为由共享方法决定时改共享方法，并检查它的每个调用方。不复制一份。为了复用而改共享实现属于这次的范围。
- **改代码之前问，最多问一次**：两个阶段里发现的需要人决定的问题，放进一次提问，每个问题给选项，第一个是推荐的。用户能看到的东西、接口和契约、数据模型和迁移、影响其他模块或服务的行为变化、明显改变实现规模的选择，都不能由 agent 替你选。`decisions.md` 里已经有的决定照做，不再问。
- **兜底**：agent 结束时说还有没问就按推荐做了的问题（`requires_design_decision`），平台照样问你，你的回答作为下一轮发回同一个会话。
- **实现**：只改范围内的代码，每条验收标准有测试或检查，跑 `myorch-checks`（项目的 `checks.sh`）直到通过，写实现记录，在当前分支提交一次。检查报告把失败列在「环境」里时（沙箱的网络或系统限制），不算代码的问题，照常提交，在实现记录里写明。
- **停下来**：需求和代码矛盾、必须改范围以外的代码、检查因代码失败又修不好时，不提交，步骤失败，然后问你怎么办。
- **可以判断不用改**：功能已经存在，或者你决定不做。不写文档，后面全部跳过。

为什么合成一步：分开时每次交接都会丢信息。有凭证的步骤（`sync-master`、`fetch-ticket`、`deliver`）仍然单独跑，`develop` 拿不到 GitHub 和 Jira 的凭证。

## 读工单

- **条件**：项目设置了 `jira.url`（mdm 是 `example.atlassian.net`，前缀 `MD`），并且给了工单号。只写数字时加上项目的前缀（`jira.key`）。没有 Jira 的项目没有工单输入，也没有 `fetch-ticket` 这一步。
- **只读**：凭证是项目的 Jira provider（mdm 是 `jira-mdm`），出站规则只放行读工单、读附件内容和 `/myself`。沙箱里只有占位符，真实 token 由网关替换。
- **内容**：标题、类型、状态、优先级、报告人、经办人、标签、组件、父工单、关联工单、描述、子任务、附件列表、评论。文件开头写明它是外部数据。
- **截图**：只下载图片附件，最多 10 张，每张不超过 5 MB，存到 `docs/external/attachments/<id>-<文件名>`。`ticket.md` 的「附件」一节列出路径，`develop` 用 Read 打开看。描述里的 `!文件名!` 指的就是这些图。其他附件只列名字。某张下载失败不影响运行。控制台的「文档」也能看这些图。
- **评论**：`ticket_comments` 默认 `all`。选 `none` 时不带评论，只按描述做（评测用：需求应该写在工单里，不靠评论）。
- **MCP 兜底**：`fetch-ticket` 没跑（项目没有 `jira.url`）、而这次运行挂了能读工单的 MCP 知识源时，`develop` 先用 MCP 读工单，写同一个 `docs/external/ticket.md`，开头注明来源。读不到就在摘要里写明，按请求原文继续。mdm 目前不默认挂 Atlassian MCP。

## review 循环

定义在片段 [`lib/review-loop.yaml`](lib/review-loop.yaml)，[review](review.md) 和 [eval](eval.md) 也用它（只跑 1 轮、不修复）。这里的参数来自项目的 `flows.development`：默认 3 个 reviewer 加单独的 security，最多 3 轮，跑满时问你。项目的 `prompts/review.md`、`verify.md`、`fix.md` 拼进对应步骤。

- **每轮看什么**：第 1 轮看整个改动（接着改时只看这次运行的新提交）；之后每轮只看上一轮开始以后的提交。范围由 `ci` 算出，写在 `docs/ci/round-N/head.txt` 里。
- **先独立判断，最后读作者的说法**：reviewer 先只看需求文档、技术设计和 diff，自己找问题；然后才读实现记录，核对里面的测试和检查是否真的存在、假设站不站得住。实现记录里写了原因，也不是放过问题的理由。
- **需求文档对照工单原文**：需求文档也是作者写的。漏掉、缩小或改写了工单的要求，工单对照表只覆盖了一部分动作，或者自己把工单点名的内容划出范围，都算 Important。
- **到仓库里查**：只看 diff 看不出来的三类问题，一律算 Important：
  - 没有复用已有的同类实现，或者复制了共享方法的分支而不是改它。
  - 漏掉的入口：同一个字段、同一句报错的其他读写位置没改，设计里也没说为什么。
  - 删掉或放宽了已有的判断，破坏了它保护的东西，而需求文档没写这是人决定的。
- **分工**：1 号多看正确性和边界，2 号多看验收标准和测试，3 号多看设计、范围和复用。漏掉的入口和放宽的判断每个人都查。只有 1 个 reviewer 的项目（blog）只有 1 号。
- **读前面步骤的过程**：`fix` 和 `reflect` 可以用 `/sandbox/bin/myorch-steps` 分段读这次运行前面步骤的 manifest 和日志（例如 develop 当时为什么这么做）。reviewer 和 verify 的 prompt 不提它：它们要先独立判断。前面步骤的记录（`.myorch/`、别的步骤的 `.claude/`）对每一步都是只读的。
- **规则标记**：上面这些「到仓库里查」「先独立判断」「Nit 最多 5 条」之类的条款，是因为模型以前犯过错才加的。它们在流程文件里用 `[[rule:<规则>/<位置>]]` 标出来，起因登记在 [`rules/registry.yaml`](rules/registry.yaml)。展开时标记去掉、正文照常保留；只有重放时 `--set flows.ablate=<规则>` 会去掉整条，用来评测这条规则还需不需要。
- **verify**：合并重复，逐条打开代码核实，只有一个人报的也核实，不成立的删掉。删掉纯风格意见，Nit 最多留 5 条。每个检查失败项算一条 Important，环境失败不算。上一轮的问题标成 fixed、open 或 rebutted。`blocking` 是还没解决的 Important 条数，为 0 时循环结束。
- **fix**：只修 Important，一个问题的所有位置一起改，不碰 Nit。认为发现不成立、或者修复要人决定时，不改代码，写进 `rebuttal.md`（要人决定的以「需要人决定：」开头），下一轮的 verify 会判断。原因在项目包里时，提项目包修改，见下一节。修完跑检查，在实现记录末尾加「第 N 轮修复」，提交一次。
- **跑满**：`on_exhausted: ask`（默认）时，`reflect` 读完所有轮次，判断是个别问题、方向错了、需要人决定，还是 reviewer 误判，给出建议，然后问你：再跑一轮、接受当前结果、终止。`accept`（blog）时直接往下走，最后一轮的修复没人 review 过，`risk-rules` 会把这些提交列出来。
- **审批人要求修改**：意见写进下一轮的 `approver.json`，每一条都算 Important，verify 不会把它删掉。

## 项目包的修改（pack-choose、pack-apply）

阻塞问题有时出在项目包里，例如 `checks.sh` 写错了。项目包在 sandbox 里是只读的（`project/`），`fix` 改不了它。这时：

1. `fix` 不改 `project/`，也不为它写 rebuttal。它把 `project/` 复制到 `/tmp`，在副本上改，用副本跑一次相关的检查，再把建议写进 `docs/review/round-N/pack/proposals.json`（机器读）和 `proposals.md`（给人看，含验证结果）。
2. `pack-choose` 列出建议，问你采纳哪几条。
3. `pack-apply` 在宿主机上把采纳的改动写回 `myorch-knowledge`，只提交这些文件，重新同步项目快照。每条改动的原文必须在文件里恰好出现一次，否则什么都不写，步骤失败。
4. 这次运行的下一轮挂新的快照，用新的项目包检查。别的运行不受影响。最后一轮刚写回时，反思步骤会建议再跑一轮。

只能改 `guide/` 下的文件、`checks.sh`、`checks.env`、`setup.sh`（`project.py` 的 `EDITABLE["fix"]`）。`risk-rules.yaml` 和 `security.md` 不能改：运行不能改变自己的改动怎么被审批。要改别的文件时，`fix` 写「需要人决定：」。

## 任务信息和 takeaway

只在项目设置了 `flows.development.takeaway: true` 时（现在只有 mdm）：仓库里放一个 `myorch/` 目录要项目同意。review 循环之后、风险判定之前，四步把这次运行的任务信息和经验提交到仓库。

- **提交在分支上**：`takeaway-commit` 把 `task.md` 和 `takeaway.md` 复制到 `repo/myorch/<日期>-<运行 ID>/`（日期是运行开始的那天），只提交这个目录，提交信息固定为 `Add myorch task and takeaway for <运行 ID>`。内容没变时不重复提交。审批时能看到，推送后在 PR 里，合并后进 base 分支，[learn](learn.md) 从那里读。
- **不阻塞**：四步都是 `on_failed: skip`。失败了记一条失败事件，当作跳过，后面照常。每一步只在上一步给出输出时执行，所以不会提交旧的或写了一半的文件（`takeaway-trail` 先清空两个文件）。
- **trail**：每一步的状态、摘要、失败原因、重试次数，问答，review 每轮的 blocking 和检查结果，分支、输入、myorch 提交、项目包版本。像密钥的行在写入前删掉。
- **takeaway 写什么**：`task.md` 只写事实（工单、运行、输入、需求摘要、问答、每轮 review 和检查、结果）。`takeaway.md` 写遇到的问题、根本原因、怎么解决的、缺了什么知识、对流程或平台的建议，每条标类别（项目知识、流程、平台、环境）并给出证据。「缺了什么知识」只写换一个需求也用得上的项目知识，不写这张工单的答案。两个文件合起来不超过 200 行。agent 不挂知识源，只读。
- **读过程**：除了 `trail.json`，`takeaway` 还可以用 `/sandbox/bin/myorch-steps` 分段读每一步的日志，找最早走偏的地方。
- **不写密钥**：两个文件会进公司仓库。`takeaway-redact` 按 `knowledge.SECRET_RULES`（和知识源扫描是同一份规则）删掉像密钥的行。两个文件都空时它失败，提交就不会执行。
- **risk-rules 怎么看这个提交**：它在最后一轮 review 之后，但不算「没有经过 review 的提交」：只有一个父提交、而且只改了 `myorch/` 下 `.md` 文件的提交不计入。合并提交一律计入。`risk-rules` 在这个提交之后取 `head_sha`，所以你批准的、推送的，都是带着这两个文件的提交。
- **不写的时候**：重放（`at`）不写，否则评测案例的答案会进知识。`develop` 判断不用改时也不写。运行在审批前失败时推不出去，它的经验不在仓库里。

## 风险和审批

风险由改动决定，不由作者决定。两个分类器互相兜底：规则可能漏掉语义上的风险，模型可能看漏路径上的风险，最后取较严的一个。

**risk-rules**（确定性脚本）：

- 规则来自项目包的 `risk-rules.yaml`（模板的规则加上项目自己的），命中的规则里取最严的。规则可以按路径、按新文件里的文字、按改动行的正则匹配。没有规则命中时用 `default`。
- 下面任意一种情况，至少 1 人：最后一轮 review 还有没解决的 Important；最后一轮检查失败；最后一轮 review 之后有新的提交（例如跑满后接受了，最后一轮的修复没人看过）。takeaway 的提交不算，见上一节。
- 输出 `approvers`（规则要求的人数）和 `available`（项目登记的审批人数）。

**classify-risk**（agent，只读）：读实际的 diff、需求文档、实现记录里的假设、最后一轮 review 和检查、没经过 review 的提交，判断 0、1 或 2 人。改接口或数据、改构建部署、和安全有关、难以撤回、影响下游，任意一条就是 2 人。写 `docs/risk.md`：结论、影响范围、没解决的问题、请审批人重点检查的 3 到 5 处。项目的 `prompts/risk.md` 拼在 prompt 末尾（myorch 用它规定「影响范围」按平台的边界、未结束的运行、部署来写）。

**human-review** 的人数：

```
min(项目登记的审批人数, max(risk-rules 的人数, min(classify-risk 的人数, 2)))
```

- 三个项目现在都只登记了一个审批人，所以人数最多是 1。
  - mdm：core-ng 模板默认 1 人，数据库迁移、接口模块、构建和依赖、CI 和部署、环境配置、实体字段注解、破坏性 SQL 是 2 人；mdm 的 protos 子模块 2 人、规格文档 1 人。所以每次都要恰好 1 人。
  - blog：`default: 1`，每次都要 1 人。
  - myorch：`default: 0`，平台的边界（sandbox、凭证、profile、部署、调度和审批的代码）要 2 人。两个判断都给 0（例如只改了文档）时自动通过，`deliver` 直接推送分支。推送的只是分支，合并和部署要人做。
- 发起人必须是审批人之一（`require_initiator`）。agent 的批准不算数。
- 审批绑定提交：打开审批时记下 `head_sha`，`deliver` 推送前核对。
- 要求修改：回到 `review-fix`，最多再跑 2 轮。之后 takeaway、风险判定、审批从头再走一遍。
- 24 小时没人处理，发一次提醒。7 天没人处理，这一步失败（「审批超时」），什么都不推送。
- 任何一人驳回，运行终止。

## 交付

定义在 [`lib/deliver.yaml`](lib/deliver.yaml)。

- 当前分支是 base 分支时拒绝推送。HEAD 不等于批准的提交时拒绝推送，要重新运行、重新审批。
- 推送分支（普通推送，不强推），不调用 GitHub API，不开 PR。
- 写 `docs/delivery.md`：标题和工单、改了什么（取自实现记录的「改动」）、怎么验证的（检查结果、验收标准表）、review 轮数和没解决的问题、没经过 review 的提交、风险结论和重点检查项、怎么撤回。
- `outputs.delivery` 带上分支、base、提交、标题、工单、交付说明和仓库的 PR 模板。`run_finished` 事件带着它，控制台和 Slack 据此告诉你推送了哪个分支。PR 由你开。

## 接着改（from_run）

通用的做法见 [在原分支上接着改](../docs/workflows/README.md#在原分支上接着改)。这个流程里：

- `checkout-branch` 只读挂载上一次运行的工作区，从它的最后一个提交开始。远端同名分支上有人追加了提交时，从远端的最新提交开始。两者分叉时停下来，要人先处理。
- 复制到 `docs/previous/` 的文件：`requirements.md`、`design.md`、`implementation.md`、`risk.md`、`delivery.md`、`external-request.md`（上一次的请求原文）、最后一轮 review 的 `review-summary.md`、记录上一次运行 ID 的 `RUN`，更早的几次在 `earlier/` 里。`conversation.md` 是宿主机写的：人对上一次运行说过的话。
- `develop` 的需求文档写完整的需求，在「目标」里单独写一段「这次的修改」。上一次的验收标准仍然有效，除非这次的请求改了它。设计在上一次的基础上改。代码在已有提交上改，不重做。`conversation.md` 里人的决定优先于上一次文档里的假设。
- `checkout-branch` 输出 `since`（这次开始时的提交）。review 循环的第 1 轮只看 `since` 之后的提交，检查仍然跑整个分支相对 base 改动的文件。风险判定看整个分支。
- 推送到同一个分支，已经开的 PR 会自动更新。没给工单号、`incident` 时沿用上一次的。
- 不能和 `at` 同时给。

## 重放（评测用）

用来在当时的代码上重做一张已经合并的工单，再和合并的 PR 对比。表单只对有 Jira 的项目显示 `at`、`ticket_as_of`、`ticket_comments`（命令行对任何项目都能传 `-i at=`）。平时不填。

- `at` 填 PR 的 merge-base。`checkout-branch` 从这个提交建分支，然后删掉 `origin` remote、其他所有引用、`FETCH_HEAD`、reflog 和借来的对象，做一次 gc，子模块也一样。之后的提交会泄露答案，所以仓库里只剩 `at` 能到达的历史。最后把 `origin/<base>` 指向 `at`，后面所有 diff 都以它为基准。
- `ticket_as_of` 填开始做这张工单的时刻（ISO 时间）。`fetch-ticket` 用工单的变更记录把标题、描述、状态、优先级、经办人回滚到那个时刻，只留之前的评论、附件和关联工单，不带子任务（子任务可能写着解法）。
- 重放永远不审批、不推送：`human-review` 和 `deliver` 的条件带 `not inputs.at`。删掉 `origin` 是第二道保护。takeaway 也不写。运行在 `classify-risk` 之后结束。
- 不能和 `from_run` 同时给，`checkout-branch` 会直接失败。
- 想知道只靠工单描述够不够时，加 `-i ticket_comments=none`。
- **对比**：重放时可以用 `--set` 改这次运行的流程设置，例如 `--set flows.development.reviewers=1` 或 `--set flows.ablate=nit-cap`（去掉一条标记过的规则，规则名见 [`rules/registry.yaml`](rules/registry.yaml)）。只能改 `flows.` 下已有的设置，只在重放时能用；改了什么记在 mandate 里，运行带 `set:…` 标签，方便按变体分组。`myorch expand --set …` 可以先看改完的 prompt。

## 按项目的不同

流程只有这一个。2026-11-10 之前每个项目复制了一份（`ck-development`、`blog-development`、`mo-development`），同一处改进要改三份，三份很快就不一样了，所以合回一个（[docs/notes/2026-11-10-one-set-of-flows.md](../docs/notes/2026-11-10-one-set-of-flows.md)）。项目之间的差别写在项目包 `myorch-knowledge/<项目>/` 里，展开流程时生效：

- **`project.yaml` 的 `flows.development`**：`reviewers`（默认 3）、`max_rounds`（3）、`security`（true）、`on_exhausted`（`ask`）、`takeaway`（false）、`incident`（false）。模板可以设置，项目覆盖模板。
- **`prompts/<槽位>.md`**：槽位有 `develop`、`review`、`verify`、`fix`、`risk`，先放模板的，再放项目的，原文拼进对应步骤的 prompt。mandate 的快照里能看到拼好的 prompt。文件名不是这几个槽位时直接报错。
- **流程里的 `if:`**：按项目裁掉步骤和输入。`fetch-ticket` 和工单、重放输入看 `project.jira.url`，takeaway 四步看 `flows.development.takeaway`，`incident` 输入看 `flows.development.incident`。裁掉的输入在表单里看不到，保留默认值。

| | mdm | blog | myorch |
|---|---|---|---|
| Jira | 有：工单必填，`fetch-ticket`，重放输入 | 没有：第一条消息是需求 | 没有：第一条消息是需求 |
| review | 默认：3 个 reviewer、security、3 轮、跑满问你 | 1 个 reviewer、没有 security、1 轮、跑满接受 | 默认 |
| takeaway | 有 | 没有 | 没有 |
| `incident` | 没有 | 没有 | 有 |
| prompts | core-ng 模板的 `develop.md`（Gradle 依赖、`*-interface` 契约注释）、`review.md`（`*-interface` 的检查） | `develop.md`（Jekyll：文档写短、写作规范、哪些要问人）、`review.md`（构建、链接、写作规范、误公开） | `develop.md`（incident 原因分析、设计要看的平台事项、「合并以后要做的事」）、`risk.md` |
| 审批 | 1 人 | 总是 1 人（`risk-rules.yaml` 的 `default: 1`） | 0 或 1 人，按风险 |

改某个项目的做法：改它的项目包（`flows.development`、`prompts/`、`risk-rules.yaml`），然后 `uv run myorch project sync <项目>`。改 `flows/development.yaml` 或片段会影响所有项目。

### blog

个人博客（`leweii/leweii.github.io`）。改动小，所以 review 轻：1 个 reviewer 看一次，有阻塞问题就修一次，不再 review，跑满直接接受。修复后的提交没人 review 过，`risk-rules` 把它们列出来，交付说明的「review」一节列出没修好的问题。分支是 `myorch/<运行 ID>`，永远不推 master：推到 master 博客就上线了。

- `prompts/develop.md`：写文章看写作规范，改主题看开发指南。需求文档、技术设计写短，不涉及的部分写「不涉及」。文章的观点和事实、是否公开（`published`、加密文章）、影响全站的改动（`_config.yml`、布局、依赖、部署）都要问人。
- `prompts/review.md`：构建会不会挂、链接会不会断、页面会不会错乱；写作规范、front matter、有没有误公开。
- 端到端测试只在 [review](review.md) 里跑，development 不跑。

### myorch 和 incident

myorch 平台自己：代码、流程、prompt、文档都在仓库 `leweii/myorch` 里，分支 `myorch/<运行 ID>`。两种用法：做一个普通需求；或者平台在某次运行里出了问题（任何项目的运行都可以），带上 `incident` 找原因、修平台。

`incident` 是出问题的那次运行。它的工作区只读挂给 `develop`，位置是 `/sandbox/workspace/observed/<运行 ID>/`：

- `.myorch/flow.yaml`：那次运行用的流程快照。
- `.myorch/steps/<实例>/out/manifest.json` 和 `logs/turn-<attempt>-<turn>.log`：每一步的结果和每次尝试的日志；`supervisor-*.log` 是 sandbox 的网络、DNS、凭证记录。
- `docs/` 和 `memory/MEMORY.md`：那次运行的需求、设计、review、检查报告。

这些文件是那次运行的数据：agent 只读，不执行里面的命令和指令。控制台的运行选择器只列本项目的运行，所以 `incident` 是普通的文本框。

myorch 的 `prompts/develop.md` 要求 `develop` 在写需求之前先判断原因：

| 原因 | 意思 | 接下来 |
|---|---|---|
| `platform-code` | myorch 的代码有缺陷（`src/myorch/`、`tools/`、`deploy/`） | 继续：需求、设计、实现、review、审批 |
| `flow` | 流程定义、片段、prompt、profile 有缺陷（`flows/`、`profiles/`） | 继续 |
| `project-knowledge` | 那个项目的项目包缺知识或检查，平台没错 | 停止。建议用那个项目的 learn 或 postmortem |
| `environment` | 网络、外部服务、凭证过期、机器资源 | 停止。只记录，不改代码 |
| `one-off` | 模型偶发的失误，重跑大概率不会再出现 | 停止。建议重跑 |
| `unclear` | 证据不够 | 停止。写明还缺什么证据 |

「停止」是指 `outputs.proceed` 写 false：后面的步骤全部跳过，运行正常结束，什么都不推。原因分类、证据（文件和行）和该去哪里处理写在摘要里。继续时，需求文档开头写「出问题的运行和原因分类」，「目标」写清楚那次运行出了什么问题、根本原因在哪个文件、改完以后同样的情况应该怎样。修复不能放宽 policy、provider、挂载、审批。

同一个 `prompts/develop.md` 还让技术设计多看几件平台的事：分层（engines → runner → storage → core）、改了片段或 profile 影响哪些流程（流程是所有项目共用的）、Temporal 的确定性、volume 和 events.db、sandbox 里的命令和镜像、要不要重新生成文档。

### myorch：合并以后

这个流程不部署。sandbox 只改仓库，拿不到 docker.sock、gateway 和数据目录，流程里也不能有自动合并、自动部署的步骤（项目包的 `security.md`）。`develop` 在实现记录最后写「合并以后要做的事」。你在 GitHub 上开 PR、合并到 main 以后：

1. 在 myorch 的 main 分支上运行 `deploy/update.sh`（先用 `--check` 看要发布哪些提交、会等什么）。它快进 main，等没有正在执行的步骤，用目标提交的干净检出重建镜像，重启 worker 和控制台，跑冒烟流程。冒烟失败时自动回滚到上一次发布的提交。改了 `src/myorch/engines/` 或 `core/run.py` 时，它等所有未结束的运行结束。见 [`deploy/README.md`](../deploy/README.md) 的「发布 origin/main」。
2. 改了 `images/claude-code/` 时，`update.sh` 一起重建 sandbox 的基础镜像 `openshell-claude-code`。各项目的开发镜像都基于它，要用上新的基础镜像，还要重建项目的镜像（项目包的 `image/build.sh`）。改了 `pyproject.toml` 或 `uv.lock` 时，重建 myorch 项目的开发镜像（在 myorch-knowledge 目录下运行 `myorch/image/build.sh ../myorch`），以后 myorch 项目的 development 运行才用上新依赖。
3. 改了 `tools/`（sandbox 里的辅助命令 `myorch-checks`、`myorch-ask` 等）时，它们由 `myorch init` 装进 volume（compose 里是 `init` 服务）。要确认重新运行过 `myorch init`，否则 sandbox 里还是旧版本。
