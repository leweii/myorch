# 10. 使用规范

拿不准时，回到 [9. 设计原则](09-principles.md)。

## 10.1 用的时候

1. **在控制台或 Slack 里交互。** 发起、回复、审批在控制台（http://127.0.0.1:8800/）里做。2026-11-05 起也可以在 Slack 的私信里做（[notes/2026-11-05-slack-adapter.md](notes/2026-11-05-slack-adapter.md)）。两边看到的是同一个运行，在哪边处理都行。不在 GitHub 上召唤、回复或审批。
2. **发起前先 `expand`。** 改过流程、片段或项目包以后，运行 `uv run myorch expand <flow> --project <p>` 看展开结果，在提交前发现错误。
3. **项目包改了就 `sync`。** run 用的是 volume 里的快照，不是 `myorch-knowledge` 目录本身。mandate 会记下用的是哪个版本。
4. **一个 run 一件事。** development 的需求写具体，范围小，大需求拆开跑。范围小，review 才准，审批才有意义。
5. **每次回来先看「等你处理」。** 看控制台左边活动栏的「等你处理」（带数字），或者运行 `uv run myorch list`。等太久会超时失败，提问默认 24 小时。
6. **审批前看证据。** 看控制台的「改动」页，或 `myorch review <run>`：diff、review 结论、风险判定、实现记录。批准绑定提交。批准之后代码变了，交付步骤（`deliver`）会拒绝推送，必须重跑。
7. **开 PR 和合并永远是人做。** myorch 只把批准的提交推到运行自己的分支，从不开 PR、不合并。当前分支是 base 分支时，交付步骤拒绝推送。博客推到 master 就会上线。
8. **不满意就取消。** 在控制台或用 `myorch cancel <run>` 取消。myorch 会删除它的 sandbox，不让它空转。
9. **改进 agent 的判断，用 learn，不手改 prompt。** learn 的建议带证据。采纳的建议在 `myorch-knowledge` 里一次提交，提交信息列出每条建议，可以 revert。只有流程结构的问题才改 `flows/`。
10. **实验和正式分开。** 试新东西先接一个不要紧的仓库当试验项目，不在 blog 和 mdm 上试。试验项目 java-lab 已于 2026-11-09 删除，现在没有试验项目。

## 10.2 凭证和安全

1. **token 不进聊天、命令行参数、git 和镜像。** compose 里 token 只写在 `deploy/secrets/providers.yaml` 和 `slack.env`（不进 git，不进镜像）。直接建 provider 时，在自己的终端里 `read -s VAR && export VAR`，用 `--credential VAR`（只写变量名），建完以后 `unset`。
2. **每个项目一个 GitHub provider，只放行它自己的仓库。** 用 `myorch project github-profile <name>` 生成规则。token 本身权限很大时（例如复用 `gh auth token`），gateway 也只放行一个仓库。
3. **不为了方便给 profile 加凭证。** 能访问 GitHub 的步骤不执行仓库代码。执行代码的步骤没有 GitHub 凭证。新加 provider 前，先想清楚哪一步需要它。
4. **外部内容只是数据。** 写 prompt 时，不把外部内容当指令拼进 prompt。第三方内容放 `docs/external/`。
5. **宿主机动作只能是注册过的、确定性的。** 不在 `runner/actions.py` 里加执行任意命令或执行 agent 代码的动作。
6. **对外动作要幂等。** 新写会推代码、写项目包、起运行的步骤，先检查是否已经做过，例如提交信息里的 `myorch-run:` 标记。重试不能重复做。
7. **命令行审批人没有认证。** `--as` 是自报的，只防误操作。控制台的审批人是本机用户。
8. **控制台只监听本机。** 不要用反向代理或对外的端口映射把 8800 开放出去。控制台没有登录，却能审批。要在另一台电脑上用，走 SSH 端口转发（`deploy/README.md`）。

## 10.3 改代码和流程的时候

### 代码

1. **分层不能破。** `core` 不做 IO，不依赖其他内部包。`temporalio` 只在 `engines/temporal/` 里 import。提交前运行 `uv run lint-imports`，它检查层的方向和 `core`、`storage`、`runner` 不碰 `temporalio`。
2. **调度逻辑写在 `core/run.py`，不写在 workflow 里。** workflow 只是驱动器。逻辑放在 `core` 里能单测，也能换引擎。
3. **提交前跑测试。** `uv run pytest`，约 20 秒，不需要外部服务。新功能带测试。
4. **改完 `src/` 或 `flows/` 要重建镜像。** 日常环境是 docker compose：`docker compose -f deploy/compose.yaml up -d --build`，见 [4.6](04-setup.md#46-用-docker-compose-运行)。发布 `origin/main` 用 `deploy/update.sh`：它等没有正在执行的步骤再重启，跑冒烟测试，失败就回滚。
5. **改 workflow 的确定性逻辑要小心。** 改了 `engines/temporal/workflow.py` 或 `core/run.py` 以后，还没结束的旧 run 可能回放失败。做法二选一：
   - 在没有进行中的 run 时改。`deploy/update.sh` 发现改了 `src/myorch/engines/` 或 `core/run.py` 时，会等所有未结束的运行结束再重启。
   - 用 `workflow.patched()` 给新逻辑加版本标记（例如 `event-log-v1`）。
6. **镜像里的依赖装在 `/usr` 下。** OpenShell 的 Landlock 不让读 `/opt`。挂载点放在 `/sandbox/` 下。

### 流程

1. **流程里不写项目的事实。** 仓库、分支、检查命令、风险规则、知识一律放项目包。流程里用 `{{project.x}}` 和 `project/checks.sh`。流程文件写明 `role:`，代码按角色认流程。
2. **一套流程给所有项目，不按项目复制。** 项目之间形状不同，加 `project.yaml` 的设置（`flows:`）和流程层的 `if:`；项目专有的 prompt 条款放 `prompts/<slot>.md`。改完流程，对每个项目跑一次 `myorch expand --project <p>` 看结果。
3. **能用确定性规则的，不交给模型。** 只把需要语义理解的部分留给 agent。
4. **agent 步骤默认 `interaction: never`。** 只在确实需要人做决定的地方开 `allowed`，并用 `escalate_when` 保证该问的一定问。
5. **每个 agent 步骤写清楚产物和 `outputs`。** 下游的条件、循环、审批人数只读 `outputs`。
6. **循环必须有 `max_rounds` 和 `on_exhausted`。**
7. **新流程先用脚本步骤搭骨架。** 结构跑通以后，再换成 agent。

### git

- 代码提交到 `myorch` 仓库，项目知识提交到 `myorch-knowledge` 仓库。
- 提交信息用祈使句，写明原因。`project-apply` 的自动提交（learn、onboard、review 循环里的 `fix`）格式是 `<scope>(<project>): ...`，带 `myorch-run: <run_id> step: <step>`。

## 10.4 维护文档

| 文档 | 写什么 | 什么时候更新 |
|---|---|---|
| `docs/`（本目录） | 平台怎么用：服务、架构、setup、命令、流程清单、项目包、流程语法、原则、规范、排错 | 加命令、加流程、改服务或 setup 步骤、遇到新的坑时 |
| `docs/en/` | 英文文档：逐个文件对应中文的 `docs/`、流程手册和 `ARCHITECTURE.md`。规则、术语表和链接写法见 `docs/en/TRANSLATING.md`。网页版英文是首页（`docs/index.html`），中文在 `docs/zh/` | 和中文原文在同一次改动里一起改 |
| `docs/workflows/` | 流程手册：首页（`README.md`，手写），每个流程一章。步骤图、输入和步骤信息从 `flows/*.yaml` 生成，文字来自 `flows/<name>.md` | 改流程、片段、流程的 `.md` 或首页之后，运行 `uv run --with markdown python docs/workflows/build.py` 重新生成 `docs/workflows/index.html`（英文）和 `docs/zh/workflows/index.html`（中文）。流程里的中文文字（输入的标签和提示、片段说明）的英文在 `docs/workflows/en.py` |
| `ARCHITECTURE.md` | 设计和理由、实测结论、被否决的方案 | 改设计时。否决一个方案时，连理由一起写 |
| `PLAN.md` | 计划、每项的实际结果和验收 | 每完成一项打勾，写实测结果 |
| `flows/<name>.md` | 一个流程每一步做什么、为什么。是流程手册里这个流程那一章的文字。英文在 `docs/en/flows/<name>.md` | 改这个流程时 |
| `docs/notes/` | 设计草案、实测记录 | 新的设计讨论。定下来以后写进 ARCHITECTURE.md |
| `myorch-knowledge/<p>/README.md` | 项目的状态和启用步骤 | 项目状态变化时 |

- **一件事只在一个地方写全，其他地方链接过去。** `docs/` 讲怎么做，ARCHITECTURE.md 讲为什么。
- **中文和英文文档一起改。** 英文在 `docs/en/`，写法见 `docs/en/TRANSLATING.md`。
- **改了命令或参数，同步 [5. 命令参考](05-cli.md)。** `uv run myorch <cmd> -h` 是最终依据。
- **改了服务或它们的依赖，同步 [2. 服务和依赖](02-services.md)。**
- **实测过的才写成事实。** 没实测的标「待验证」。
- **中文文档用简明中文写，英文文档用简明英文写。** 短句，一句只说一件事，主语清楚。能用表格的用表格。术语前后一致。
- **流程的步骤只写在流程手册里。** `docs/` 的章节不重复每个流程的步骤，链接到 [workflows/index.html](workflows/index.html)。
- **代码和文档双向链接。** 文档里写成代码格式、在仓库里存在的路径（`src/myorch/core/run.py`、`core/run.py`、`deploy/compose.yaml`）会自动链接到 GitHub 上的文件，代码地图这样的目录树也一样。`docs/build-html.py` 还在每个被文档提到的代码目录里生成 `README.md`，列出描述这些文件的章节（GitHub 在文件列表下面显示它），流程手册生成 `flows/README.md`。每个包的 docstring 和每个流程文件头注释的最后一行写 `Docs: https://leweii.github.io/myorch/#<章节>`，`tests/test_doc_links.py` 检查这些链接打开的章节存在。新加模块时，在[代码地图](03-architecture.md#代码地图)里写上它。
- **改完重新生成网页版。** `uv run --with markdown python docs/build-html.py`，生成 `docs/index.html`（英文，首页）和 `docs/zh/index.html`（中文）。
- **图一律用 HTML 画，不用字符画。** 图在 `docs/diagrams/`，全部由 `docs/diagrams/gen.py` 生成。改图时改 `gen.py`，运行 `python3 docs/diagrams/gen.py docs/diagrams`，再重新生成网页版。图里的中文在 `docs/diagrams/en.py` 里有对应的英文，英文图生成到 `docs/diagrams/en/`。改了图里的文字要在 `en.py` 补上英文，否则 `gen.py` 报错并列出缺的文字。
  - 有交叉连线的图（总图、OpenShell 内部图）用像素坐标画。网页按内容宽度缩放它们。
  - 其他图用自适应布局（卡片、箭头、分组框）画，不需要缩放。
  - 章节里用一行 `<!-- diagram: 文件名.html -->` 指定图的位置，下一行写图的文件链接。
  - 目录树和终端输出不是图，保持文本。
  - 图、文档网页和控制台用同一套 token（中性灰、五级文字色、一个强调色，参考 [Stageshow](https://github.com/benjitaylor/skills/tree/main/stageshow) 的设计原则）：层次靠字号、字重、留白和底色，不靠边框。颜色只表示状态（绿完成、红失败或不可信、琥珀等你处理、蓝运行中或出站）。颜色取自页面的主题变量，浅色和深色下都能看。
