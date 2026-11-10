# 6. 流程清单

每个流程的说明在单独的**流程手册**里：[workflows/index.html](workflows/index.html)。那里每个流程一章：做什么、输入、步骤图、每一步做什么、什么时候需要你。步骤图和输入表从 `flows/*.yaml` 生成，文字在 `flows/<流程>.md`。首页讲角色、什么时候用哪个、怎么发起和介入、在原分支上接着改。

本章只列出有哪些流程。

## 有哪些流程

| 角色 | 控制台里的任务 | 用途 | 流程文件 |
|---|---|---|---|
| `development` | 开发 | 从需求到推送分支：需求、实现、review、审批、推分支。不开 PR | `development.yaml` |
| `review` | 评审 PR | 评审一个已有的 PR，结论显示在控制台 | `review.yaml` |
| `postmortem` | 复盘 | 找出漏过去的问题是哪次改动引入的、本该在哪一层拦住，改进知识，生成评测案例 | `postmortem.yaml` |
| `sweep` | 找 bug | 找现有代码里的 bug，每次看一批文件，只出日报 | `sweep.yaml` |
| `learn` | 学习 | 从历史运行里沉淀知识，写回项目包 | `learn.yaml` |
| `eval` | 评测 | 重放评测案例，给 reviewer 打分 | `eval.yaml` |
| `onboard` | 接入项目 | 分析仓库，提出项目包的修改 | `onboard.yaml` |

- 一个角色一个流程，所有项目共用（mdm、blog、myorch 都一样）。片段在 `flows/lib/`：`sync-base`、`review-loop`、`e2e`、`deliver`、`choose-proposals`。
- 项目之间的差异在项目包里，发起时不用选：`project.yaml` 的 `flows.development` 决定 review 强度、跑满时怎么办、要不要 takeaway 和 `incident` 输入；`prompts/<slot>.md` 是拼进 prompt 的项目条款；有没有 Jira、e2e 镜像决定工单输入、`fetch-ticket` 和端到端测试。流程里用 `if:` 按项目裁剪。见 [7. 项目包 · 共用流程的项目设置](07-projects.md#共用流程的项目设置flows)。
- 2026-11-03 到 11-10 之间，软件开发的流程按项目复制过一套（`ck-`、`blog-`、`mo-`），之后合回一套。旧运行的 ID 还带着旧名字（如 `ck-development-1f29e33a`），按角色照样能找到和接着改。
- 流程文件开头的 `role:` 决定它是什么角色，myorch 按角色认流程。`projects:` 可以限定只给某些项目用，现有的流程都不写。
- 控制台顶栏的「流程」页列出所有流程定义和片段，按 `projects:` 分组（现在都在「通用」组），能选项目和输入预览展开后的步骤。
- 写流程的语法见 [8. 流程语法](08-flow-syntax.md)。改完流程后重新生成流程手册：`uv run --with markdown python docs/workflows/build.py`。
