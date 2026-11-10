# 一套流程，项目包决定形状（2026-11-10）

状态：**代码、流程、项目包和单元测试完成。** 本仓库在分支 `converge-flows` 上，myorch-knowledge 也在同名分支上。两边要一起合并。

## 用户定的事

- 回到「一套通用流程 + 项目包参数」。2026-11-03 起按项目复制的 `ck-*`、`blog-*`、`mo-*` 合成一套。当时的提交说明写的就是 "Diverge first, converge later"。
- development 用一个 develop 步骤做骨架，也就是 ck 的做法。mo 的 analyze → design → implement 三步不再保留。
- 项目专属的 prompt 条款在展开流程时**原文**拼进步骤的 prompt，不交给 agent 自己去读 guide。
- 流程形状的差异写在 `project.yaml` 的 `flows:` 里，发起运行时不用选。

## 为什么

按项目复制以后，同一处改进要改三份，而三份很快就不一样了。改一个流程的时候，分不清哪些差异是项目真的需要，哪些只是复制后各自漂移。原则 14「流程与项目无关」说的是一回事，仓库里做的是另一回事。

逐份对比的结果：

| 片段或流程 | 三份之间的差异 | 去向 |
|---|---|---|
| `choose-proposals`、`e2e`、`review` | 只有名字不同 | 直接合并 |
| `sync-base` | ck 多一个裸镜像加速 | 所有项目都用 |
| `review-loop` | ck 多了复用、漏掉的入口、工单核对等条款，都是通用的工程要求，只有 `*-interface` 一条是 Core-NG 专属；mo 多了「项目包出错时提建议写回」 | ck 的条款通用；`*-interface` 放进 core-ng 模板的 `prompts/review.md`；项目包修复所有项目都开 |
| `deliver` | ck 是 mo 的超集；blog 多一段「修复后重新检查」 | 用 ck 的。blog 的 ci-final 不再有 |
| `development` | 形状不同（见下） | 一个流程，按项目设置裁剪 |
| `learn`、`postmortem`、`eval`、`sweep` | 只有 ck 版，内容本来就是通用的 | 去掉 `ck-` 和 `projects: [mdm]` |

## 机制（平台改动）

1. **项目设置** `flows.development`，在 `src/myorch/project.py` 的 `Development` 里：`reviewers`、`max_rounds`、`security`、`on_exhausted`、`takeaway`、`incident`。默认值就是 ck 原来的做法，`takeaway` 和 `incident` 默认关。模板可以设置，项目覆盖模板。
2. **prompt 槽位** `prompts/<slot>.md`。槽位有 develop、review、verify、fix、risk。先放模板的，再放项目的。文件名不是这几个槽位之一时直接报错，免得写错名字的文本被悄悄丢掉。流程里用 `{{project.prompts.<slot>}}` 引用，展开时原文拼进 prompt，mandate 的快照里能看到。
3. **流程层的 `if:`**，写在步骤、`use:` 条目和输入上，表达式针对 `project` 求值，展开时裁剪（`core/fragment.py` 的 `apply_project_if`）。
   - 步骤被裁掉时，后面依赖它的步骤**继承它的 `needs`**。例如 takeaway 四步裁掉以后，`risk-rules` 改为等 `review-fix`。如果只是从 `needs` 里删掉，`risk-rules` 就没有任何依赖，会在一开始就运行。
   - 输入被裁掉时不删除，而是变成 `hidden` 并保留默认值。表单里看不到它，但 prompt 里的 `{{inputs.ticket}}` 在运行时照样能渲染。
   - 输入的 `required` 也可以是表达式，例如 `required: "not project.jira.url"`：有 Jira 时工单就是需求，task 可以不填。
4. **`with:` 先填项目值**，所以项目值可以按类型传给片段的 int、bool 参数。原来 e2e 的 `acceptance: "true"` 字符串写法因此改回 bool。
5. **按项目看输入**：`flowfile.flow_inputs(path, project)`。CLI、relay、控制台（`inputs_for`）、Slack 表单和运行标签都按项目解析输入，工单前缀来自项目的 Jira key。旧的 `ck-development` 运行会读 `development.yaml`，所以照样能按工单号找到。
6. `flowfile` 给缺少 `flows`、`prompts` 的项目字典补默认值。这类字典来自旧 mandate，或者是手写的。

## 每个项目的变化

| | 原来 | 现在 |
|---|---|---|
| mdm | ck-development | 基本不变。`flows.development.takeaway: true`；`*-interface` 条款和 Gradle 说明放进 core-ng 模板的 prompts；review 循环里多了项目包修复 |
| blog | 一个 reviewer 看一次、修一次，修完再跑一次检查；审批固定 1 人；没有风险判定 | 1 个 reviewer、1 轮、不做安全 review、`on_exhausted: accept`。多了 verify、风险判定（risk-rules、classify-risk）和项目包修复。修完不再自动重跑检查，fix 自己会跑检查，没经过 review 的提交由 risk-rules 列出来。`risk-rules.yaml` 的 `default: 1`，所以照样每次都要人审批 |
| myorch | analyze → design → implement | 一个 develop 步骤。incident 分析、设计要看的平台事项、「合并以后要做的事」都放进 `myorch/prompts/develop.md`；风险说明的写法放进 `prompts/risk.md`；`flows.development.incident: true`。`outputs.cause` 不再输出，原因分类写在 summary 和需求文档里 |

## 合并时要注意

- 两个仓库要一起合并。旧代码会忽略新的 `flows:` 和 `prompts/`，所以先合并 myorch-knowledge 没有问题；反过来，只合并本仓库的话，mdm 会没有 takeaway。
- 运行 ID 的前缀从此是 `development-`、`review-` 这类，不再带项目；sandbox 名字也是 `dev-deve-xxxxx`。项目在运行列表的项目列里看，sandbox 的项目在 `myorch.project` 标签里。
- 还没结束的运行用的是提交时展开好的流程，不受影响。`from_run` 按 role 选运行，旧的 `ck-development` 运行照样能接着改。
- `flows/*.md` 流程手册和 docs/ 里写流程名的地方，这个分支**没有改**：主工作区里这些文件正在大改，还没提交。要改的地方：ARCHITECTURE「代码结构」、06-flows、07-projects（`flows:`、`prompts/`）、08-flow-syntax（流程层 `if:`、输入的 `if:` 和 `required` 表达式、`with:` 先填项目值、片段表）、09-principles §14、README 的命令示例，以及 `flows/ck-*.md`、`blog-*.md`、`mo-development.md` 改名或合并。
