# 7. 项目包

流程里不写项目专有的东西。仓库、分支、检查命令、风险规则、项目知识都放在项目包 `../myorch-knowledge/<name>/` 里。`myorch-knowledge` 本身是一个 git 仓库：learn、onboard 写回的每条改动都是一个提交。

流程所有项目共用，一个角色一个，见 [6. 流程清单](06-flows.md)。项目之间流程形状的差异写在 `project.yaml` 的 `flows:` 里，项目专属的 prompt 条款写在 `prompts/` 里，见「共用流程的项目设置」和「prompt 槽位」。每个流程的步骤见 [流程手册](workflows/README.md)。

## 现有项目

| 项目 | 仓库 | base | 继承 | 身份 | 流程设置（`flows.development`）和 `prompts/` | 状态 |
|---|---|---|---|---|---|---|
| `blog` | `leweii/leweii.github.io` | master | — | 本人 `leweii`（2026-11-05 前是 bot `leweiibot`） | `reviewers: 1`、`max_rounds: 1`、`security: false`、`on_exhausted: accept`。`risk-rules.yaml` 的 `default: 1`。prompts：develop、review | 在用。有 e2e（Playwright）。onboard 跑过 |
| `mdm` | `example-org/master-data-management` | master | core-ng | 本人 `leweii` | 默认值，加 `takeaway: true`。有 Jira。prompts 来自模板 | 在用。provider 已建，onboard 完成过一次（2026-11-01）。读 Jira 工单 |
| `myorch` | `leweii/myorch` | main | — | 本人 `leweii` | 默认值，加 `incident: true`。prompts：develop、risk | 平台自己（2026-11-05 建）。检查离线运行，镜像 `myorch-dev-myorch`。项目包的 README 写着还没有真实运行过 |
| `_templates/core-ng` | — | develop | — | — | prompts：develop（Gradle 依赖、`*-interface` 模块）、review（`*-interface` 契约） | Core-NG 模板（Java/Gradle 多模块 + React）。在试验项目上试过检查、缓存、policy、镜像 |

三个项目用同一套流程（development、review、learn、postmortem、eval、sweep、onboard）。

## 目录结构

```
myorch-knowledge/
  _sources/                 可选：全局登记的知识源（见「知识源」）
  _templates/core-ng/       模板：同一技术栈共享
  <project>/
    project.yaml            必需：仓库、身份、provider、镜像、共用流程的设置（flows:）等（见下）
    prompts/                可选：拼进共用流程 prompt 的项目条款：develop.md、review.md、verify.md、fix.md、risk.md
    guide/                  myorch 维护的知识：开发方法、review 指南、风险说明。learn、onboard 会改
    checks.sh               项目的检查（CI）
    checks.env              可选：checks.sh 的设置（core-ng 的 checks.sh 会读它）
    setup.sh                可选：新 checkout 后的准备（链接预装依赖、配置缓存）
    risk-rules.yaml         改动路径 → 需要几人审批
    security.md             安全 review 的重点
    policy.yaml             可选：需要联网的步骤用的 OpenShell 网络 policy
    e2e/                    可选：端到端测试（serve.sh、run.sh、tests/、README.md），需要 project.yaml 的 e2e.image
    <其他目录>/             可选：给 agent 看的资料，例如 blog 的 profile/。用法写在它自己的 README 里
    image/                  开发镜像的 Dockerfile 和 build.sh（不进快照）
    providers/              可选：这个项目要用的 OpenShell provider profile（不进快照）
    sources/                可选：这个项目登记的知识源（不进快照）
    README.md               给人看的说明（状态、启用步骤）
```

## project.yaml 字段

字段的最终依据是 `src/myorch/project.py` 的 `Project` 模型。

| 字段 | 必需 | 说明 |
|---|---|---|
| `extends` | | 继承的模板名（`_templates/<name>/`） |
| `repo` | ✓ | `owner/name` |
| `base` | | base 分支，默认 `main` |
| `submodules` | | 需要一起 clone 的其他仓库（`owner/name`）。GitHub provider 对它们只读 |
| `identity.account` | ✓ | 在 GitHub 上以哪个账号行动（生成 GitHub provider profile 时用） |
| `identity.git_name` / `git_email` | ✓ | 提交的作者。sandbox 里是 `MYORCH_GIT_NAME`、`MYORCH_GIT_EMAIL` |
| `identity.self_is_operator`、`identity.trigger` | | 保留字段（GitHub 渠道时代的召唤方式）。现在没有代码用 |
| `providers` | | 角色 → gateway 里的 provider 名。角色有 `github`（dev-git 用）、`artifacts`（私有依赖源，dev-git 和 dev-write 用）、`jira`（读工单，ticket-read 用）。`null` 表示不用。`artifacts`、`jira` 不写等于 `null`；`github` 不写时，用名字就叫 `github` 的 provider |
| `image` | ✓ | 开发镜像，例如 `myorch-dev-blog:latest` |
| `e2e.image` | | 端到端测试工具所在的镜像，例如 `myorch-e2e-blog:latest`。空（默认）：不跑端到端测试。见「e2e/ 约定」 |
| `e2e.profile` | | 端到端测试步骤的权限，默认 `dev-read`（仓库只读，没有凭证，不能上网） |
| `e2e.acceptance` | | 默认 `true`：agent 按验收标准写用例并运行。`false`：只跑固定用例 |
| `jira.url` | | Jira 站点，例如 `https://example.atlassian.net`。空（默认）：不读工单。设了以后，development 显示工单相关的输入（`ticket`、重放用的 `at`、`ticket_as_of`、`ticket_comments`），`task` 不再必填，`fetch-ticket` 在 develop 之前读工单和截图，写进 `docs/external/ticket.md` 和 `docs/external/attachments/`。只读，不写回 |
| `jira.key` | | 工单号只给数字时加上的前缀，例如 `MD`：`18700` → `MD-18700` |
| `jira.auth` | | `basic`（默认，Atlassian Cloud：邮箱加 API token）或 `bearer`（Data Center 的个人访问令牌） |
| `jira.email` | | `basic` 时 token 所属账号的邮箱 |
| `branch` | | 分支名模板，默认 `myorch/{{run.id}}`，运行时渲染。可以用 `{{inputs.x}}`。渲染失败时（例如流程没有这个输入）退回 `myorch/<run_id>`。「接着改」（`from_run`）时用原运行的 ID，所以分支和原运行相同 |
| `conventions` | | 提交和 PR 的约定，写进每个 agent 步骤的 prompt |
| `pr_template` | | 仓库里 PR 模板的路径。deliver 把模板内容放进它的输出 `delivery.pr_template`，给开 PR 的人用 |
| `repo_docs` | | 仓库自带的知识（`CLAUDE.md`、`.claude/skills/` 等），prompt 会引导 agent 去读 |
| `required_inputs` | | 这个项目要求必填的输入，例如 `[ticket]`。只对声明了这个输入的流程生效 |
| `knowledge` | | 这个项目的运行默认挂载哪些知识源（名字列表）。发起时可以增减，见「知识源」 |
| `sweep` | | sweep 流程看哪些文件：`paths`（目录或文件，不写表示整个仓库）、`include`、`exclude`（glob，可以用 `**`）、`chunk`（每次几个文件，默认 20，1–200） |
| `policy` | | 项目包里的 policy 文件名。profile 写 `policy: project` 时使用 |
| `shared` | | 每个步骤都挂的跨 run 共享读写目录（缓存），例如 `[gradle]` → `/sandbox/workspace/shared/gradle`，数据在 volume 的 `shared/<project>/gradle` |
| `flows.development` | | 共用的 development 流程怎么跑：`reviewers`、`max_rounds`、`security`、`on_exhausted`、`takeaway`、`incident`。见「共用流程的项目设置」 |
| `approvers` | | 能审批这个项目改动的人。development 的风险判定要求的人数不超过这个名单的人数。空表示不设上限 |
| `review` | | 只有一个值 `local`（默认）：审批前改动不离开 sandbox。可以不写 |
| `allow` | | 保留字段（能指挥 myorch 的 GitHub 账号）。现在没有代码用 |

流程里用 `{{project.x}}` 读这些值，例如 `{{project.base}}`、`{{project.e2e.image}}`。另外有两个算出来的值：`{{project.repo_docs_list}}`（每行一个 `- repo/<路径>`）和 `{{project.submodules_list}}`（空格分隔）。`{{project.prompts.<slot>}}` 来自 `prompts/`，不来自 `project.yaml`（`project.yaml` 里写的 `prompts` 会被忽略）。`myorch project show <name>` 显示合并后的全部值。

例子（mdm，节选）：

```yaml
extends: core-ng
repo: example-org/master-data-management
base: master
submodules: [example-org/protos]
identity:
  account: leweii
  git_name: Jakob He
  git_email: <提交用的邮箱>
  self_is_operator: true
providers:
  github: github-mdm
  artifacts: azure-artifacts
  jira: jira-mdm
jira: {url: "https://example.atlassian.net", key: MD, auth: basic, email: <token 所属账号的邮箱>}
image: myorch-dev-mdm:latest
policy: policy.yaml
branch: "MD-{{inputs.ticket}}-myorch-{{run.id}}"
required_inputs: [ticket]
flows:
  development: {takeaway: true}
approvers: [jakobhe]
repo_docs: [CLAUDE.md, openspec/specs/]
```

### 接 Jira

1. 把 `openshell/providers/jira-read.yaml` 里的 host 换成你的 Jira 站点，导入 profile，用你的 token 建 provider（文件开头有命令）。token 只存在 OpenShell gateway 里，sandbox 只看到一个占位符。provider 只允许 GET。要下载工单的截图，还要放行 `GET /rest/api/2/attachment/content/**`，见已经填好站点的 `jira-read-example.yaml`。
2. 在项目的 `project.yaml` 里写：

```yaml
providers:
  jira: jira-<站点>
jira: {url: "https://<站点>", key: MD, auth: basic, email: <token 所属账号的邮箱>}
```

3. 之后 development 的表单里有工单输入，带 `ticket` 的运行会先读工单。读工单的步骤用 `ticket-read` profile（只有 `jira` 凭证，仓库只读）。

## 共用流程的项目设置（flows:）

流程所有项目共用，形状由项目决定。`project.yaml` 的 `flows.development` 决定 `flows/development.yaml` 怎么跑（`src/myorch/project.py` 的 `Development`）。发起运行时不用选，按项目自动用。

| 字段 | 默认 | 范围 | 作用 |
|---|---|---|---|
| `reviewers` | 3 | 1–8 | 每轮并行几个 reviewer（review-loop 的 `reviewers`） |
| `max_rounds` | 3 | 1–10 | review 最多几轮 |
| `security` | `true` | | 每轮另跑一个安全 reviewer |
| `on_exhausted` | `ask` | `ask`、`fail`、`accept` | 最后一轮之后还有阻塞问题时：问你、失败、接受当前结果继续（没解决的问题由 `risk-rules` 要求人审批） |
| `takeaway` | `false` | | 把任务信息和 takeaway 提交到项目仓库的 `myorch/<日期>-<run_id>/`，learn 从 base 分支读。只在欢迎这种目录的仓库里开 |
| `incident` | `false` | | 显示 `incident` 输入：一个出问题的运行（任何项目的），只读挂给 develop。怎么用写在项目的 `prompts/develop.md` 里 |

- 模板可以设置，项目覆盖模板（`project.yaml` 深度合并）。
- 流程里用 `{{project.flows.development.x}}` 传给片段参数（按类型传，见 [8. 流程语法 · 片段](08-flow-syntax.md#片段use)），用 `if: "project.flows.development.takeaway"` 这样的表达式决定要不要某些步骤和输入（见 [8. 流程语法 · 按项目裁剪](08-flow-syntax.md#按项目裁剪if)）。
- 不是所有差异都在 `flows:` 里。有没有 Jira（`jira.url`）决定工单输入和 `fetch-ticket`，有没有 e2e 镜像（`e2e.image`）决定 review 流程跑不跑端到端测试，`risk-rules.yaml` 决定审批人数。
- 旧 mandate 里、或者手写的项目字典没有 `flows`、`prompts` 时，`project.with_defaults` 补默认值。
- `flows.ablate`（默认空）：展开流程时去掉的 prompt 规则，见 [8. 流程语法 · prompt 里的规则](08-flow-syntax.md#prompt-里的规则rule)。给评测用，一般由 `myorch run --set flows.ablate=...` 在重放时临时设，项目包里不写。

## prompt 槽位（prompts/）

项目专属的 prompt 条款放在项目包的 `prompts/<slot>.md`。流程展开时把它原文拼进对应步骤的 prompt（`{{project.prompts.<slot>}}`），不交给 agent 自己去读。展开后的流程（工作区的 `.myorch/flow.yaml`）里能看到拼进去的文字。

| 槽位 | 拼进哪里 |
|---|---|
| `develop` | development 的 `develop` |
| `review` | review-loop 的 `review`（development、review、eval） |
| `verify` | review-loop 的 `verify` |
| `fix` | review-loop 的 `fix` |
| `risk` | development 的 `classify-risk` |

- 有模板时，先放模板的，再放项目的，中间空一行。都没有时是空字符串。
- 文件名不是这五个槽位之一（或者不是 `.md`）时，读项目包直接报错，免得写错名字的文本被悄悄丢掉。以 `.` 开头的文件和子目录不管。
- 只放这个项目或这个技术栈专有的要求。通用的工程要求写进流程，所有项目都受益。
- 例子：core-ng 模板的 `review.md` 要求接口行为变了时核对 `*-interface` 模块；myorch 的 `develop.md` 写怎么分析 `incident`、设计时要看的平台事项和「合并以后要做的事」；blog 的 `develop.md` 让文档写短。
- 和 `project.yaml` 一样，发起运行时直接读 myorch-knowledge 里的文件，改了马上生效。流程不改 `prompts/`（`project-apply` 的 scope 都不含它），手改后在 myorch-knowledge 里提交。

## 模板继承规则

项目写了 `extends: core-ng` 时：

- `project.yaml`：深度合并，项目的值优先。
- 其他文件：逐个覆盖。项目有这个文件就用项目的，没有就用模板的。`guide/` 是两边的文件合在一起。
- `risk-rules.yaml` 的规则和 `security.md`：拼接，模板在前。`risk-rules` 的 `default` 取最后一个设置了它的值。
- `prompts/<slot>.md`：同一个槽位拼接，模板在前。
- learn 或 onboard 第一次改一个只从模板继承的文件时，先把它复制到项目里再改（copy-on-write）。模板不变。
- 模板的 `sources/` 里登记的知识源，继承它的项目都能用。

`myorch project show <name>` 显示合并后的 `project.yaml`。

## checks.sh 约定

约定对所有项目相同。review 循环的 `ci` 步骤、agent 调用的 `/sandbox/bin/myorch-checks`、onboard 的 `try-checks` 都调用它：

- 输入：环境变量 `BASE_REF`（对比的起点），文件 `$CHECKS_DIR/changed.txt`（改动的文件列表）。当前目录是仓库根。
- 输出：`$CHECKS_DIR/report.md`（给人和 agent 看的报告），`$CHECKS_DIR/result.json`：`{"passed": true/false, "failed": [...], "skipped": [...]}`。可以多一个 `environment` 列表，写测试环境造成的失败（不是代码的问题），例如 core-ng 的 `ENV_FAILURE_PATTERNS` 匹配到的。
- 写完报告就 `exit 0`。检查失败写在 `result.json` 里，不靠退出码。
- **什么都没检查时不能报通过**，例如工具起不来。这时要算失败，并附上日志。
- 项目专有的设置放 `checks.env`，checks.sh 自己读（例如 mdm 的 `FRONTEND_SCRIPTS`、`GRADLE_JVMARGS`）。

`myorch-checks` 先跑 `setup.sh`（有的话），再跑 `checks.sh`，`passed` 为真时退出码是 0。它算出的改动文件包括：base 之后的提交、已暂存的改动、未暂存的改动、新的未跟踪文件。agent 在提交前跑检查，也能查到新文件。

## e2e/ 约定（可选）

project.yaml 的 `e2e.image` 不为空时，review 流程在 review 之后跑端到端测试片段 `flows/lib/e2e.yaml`（`use:` 条目上写了 `if: "project.e2e.image"`）。review 流程只测不修：`fix: false`，跑 1 轮。development 流程现在不跑端到端测试。

片段只规定步骤：固定用例 + 验收用例 →（可选）修复 → 下一轮。用什么工具测，由项目决定：

- `project.yaml` 的 `e2e:`：镜像、权限、要不要写验收用例。
- 项目包的 `e2e/` 目录：怎么启动应用、怎么跑用例、怎么写用例。

接一个技术栈不同的系统（例如手机 APP）时，只换这两处，不改流程。

`e2e/` 目录提供：

| 文件 | 约定 |
|---|---|
| `serve.sh` | 构建并启动应用，输出访问方式（例如 URL）。可以重复调用：同一个提交只构建一次，应用已经在运行时直接用 |
| `run.sh` | 输入：环境变量 `E2E_DIR`，可选参数 `--cases <目录>`。不带参数时跑 `tests/`。输出：`$E2E_DIR/report.md`、`$E2E_DIR/result.json`（格式和 checks.sh 相同，可以多一个 `environment` 列表），可选的 `$E2E_DIR/screenshots/`。写完报告就 `exit 0` |
| `tests/` | 固定的冒烟用例 |
| `README.md` | 给写验收用例的 agent 看：用什么工具、用例怎么写、文件怎么命名、测试环境有什么限制 |

运行环境：`e2e.image` 镜像，权限是 `e2e.profile`。当前目录是仓库根，仓库可能只读。`e2e.acceptance` 为 `true` 时，镜像里还要有 Claude Code，因为验收用例由 agent 写。修复步骤（开了 `fix` 时）在项目的开发镜像里跑，里面没有测试工具。结果在 `docs/e2e/round-<N>/`。

`result.json` 的 `environment` 列出测试环境的问题，例如浏览器没有启动、沙箱拒绝了某个系统调用。这些用例没有测成，不是代码失败。`run.sh` 能分辨时，把它们写进 `environment`，不写进 `failed`。`failed` 为空、只有 `environment` 时，循环结束：不跑修复步骤，也不再跑一轮。

有的系统不能在沙箱里跑。沙箱是 Linux 容器。iOS 模拟器只能在 macOS 上跑。Android 模拟器需要硬件加速，沙箱很可能不允许。这类项目的 `run.sh` 把用例交给外部的设备测试服务，再取回结果。`e2e.profile` 换成一个能联网的 profile，并带上这个服务的凭证（OpenShell provider）。流程不变。

示例见 `blog/e2e/`，用 Playwright 和无头 Chromium。Chromium 在 OpenShell 沙箱里要加 `--no-zygote` 和 `--disable-dev-shm-usage`：沙箱不允许写 `/dev/shm`，也不允许 zygote 创建 user namespace。

## risk-rules.yaml

```yaml
rules:
- paths: [.github/*, Gemfile*, ...]     # fnmatch：* 也匹配 /
  approvers: 2
  reason: 高风险路径（部署、依赖……）
- added_with_text:                       # 新增的文件，内容含某段文字
    paths: [_posts/*]
    text: 'published: true'
  approvers: 1
- changed_with_text:                     # 改动的文件里，增加或删除的某一行匹配正则
    paths: ["*.sql"]
    pattern: "(?i)\\b(DROP|ALTER|TRUNCATE)\\b"
  approvers: 2
default: 0
```

同步时，`risk-rules.yaml` 另存一份 `risk-rules.json`，脚本不用解析 YAML。

development 用它：`risk-rules` 步骤取 `default` 和所有命中规则中的最大值。以下情况至少要 1 人：review 还有没解决的 Important 问题、检查失败、有提交没经过 review。`classify-risk` agent 独立再判断一次（最多算 2 人）。审批人数取两者中较大的值，再用 `approvers` 名单的人数封顶。blog 的 `default: 1`，所以每次都要人审批。各流程的具体规则见 [流程手册](workflows/README.md)。

## 知识源

知识源是挂给运行的只读知识。它不放在项目包快照里，单独同步、单独记版本。字段的最终依据是 `src/myorch/knowledge.py` 的 `Source` 模型。

登记：在控制台「流程 → 知识库 → ＋ 登记知识源」里填表，也可以手写 YAML。
- 表单先「检查」：报出缺的字段。文件夹类型还会列出将收进哪些文件，以及扫描到的疑似密钥。
- 「登记」写进 `myorch-knowledge`，并提交一次 git。已登记的知识源可以在它的页面上「编辑」或「删除登记」。删除登记不删已有的快照。
- 模板里的知识源控制台只读。
- 不能直接登记 Obsidian vault（文件夹里有 `.obsidian/`）。vault 里有密钥，只能登记你整理过的文件夹。

登记位置（同名时项目优先，其次是模板，最后是全局）：

| 范围 | 文件 |
|---|---|
| 全局 | `myorch-knowledge/_sources/<名字>.yaml`。写了 `projects` 时只有这些项目能用 |
| 模板 | `_templates/<模板>/sources/<名字>.yaml` |
| 项目 | `<项目>/sources/<名字>.yaml` |

三种类型：

| 类型 | 必填字段 | 可选字段 | 怎么提供给 agent |
|---|---|---|---|
| `dir` | `path`（可以用 `${ENV}` 和 `~`） | `include`、`exclude`、`index`、`projects`、`max_size`、`allow_findings` | 快照只读挂在 `/sandbox/workspace/knowledge/<名字>/` |
| `git` | `repo`（`owner/name`）、`provider`（能 clone 这个仓库的 OpenShell provider） | `ref`（默认 `main`）、`image`（带 git 和 python3 的镜像，默认用项目的镜像），以及 `dir` 的可选字段 | 同上 |
| `mcp` | `url`（`https://`）、`provider`、`token_env`、`tools`。`auth: basic` 时必填 `email` | `auth`（`bearer` 默认，或 `basic`）、`index`、`projects`、`token_read_only` | 不挂目录。见下面的「MCP 只允许登记的工具」 |

可选字段的意思：

- `include`、`exclude`：glob，`**` 跨目录。`include` 为空时收所有文件。`.git`、`.obsidian`、`.DS_Store`、`.trash` 和符号链接一律不收。文件夹里的 `.myorchignore`（每行一个 glob）也算 `exclude`。
- `index`：agent 先读的文件；MCP 是工具的用法。写进 prompt。
- `max_size`：过滤后的总大小上限，默认 50MB，可以写 `50MB` 这样的形式。超过时同步失败。
- `allow_findings`：确认是误报的密钥扫描结果，写成 `文件:行号`。

宿主机的文件夹（`dir`）放在哪里：

- 快照在发起运行的进程里做。宿主机上的命令行能读任何目录；控制台、Slack 和定时任务的运行在容器里发起，只能读**宿主机知识根目录**下的文件夹。
- 知识根目录默认是 `~/orca/kb`，用 `deploy/.env` 的 `MYORCH_KB_ROOT` 改（写绝对路径）。compose 把它只读挂进控制台和 worker，容器里的路径和宿主机一样；登记的 `~` 在容器里也按宿主机的 home 展开。
- 所以要从控制台挂的文件夹，放在 `~/orca/kb/<名字>/` 下，登记成 `path: ~/orca/kb/<名字>`。放在别处时，控制台的「检查」会直接说明原因。
- 不挂整个 home：里面有 `~/.ssh` 这类密钥。

挂给谁：

- 项目的 `knowledge` 写默认挂哪些。发起时可以增减：命令行用 `myorch run … --kb a,-b`（`a` 加上，`-b` 去掉），控制台在「新运行」里勾选。
- 默认只挂给 agent 步骤。agent 步骤写 `knowledge: false` 就不挂。脚本步骤写 `knowledge: true` 才挂。MCP 知识源只给 agent 步骤。
- 运行开始时，mandate 记下每个知识源用的快照版本。之后知识源再变，这次运行看到的内容也不变。

### MCP 只允许登记的工具

agent 步骤启动时，`/sandbox/bin/myorch-mcp` 先于 claude 运行：

1. 用 provider 的占位变量拼 `Authorization` 头。`bearer` 是 `Bearer <token>`。`basic` 是 `email:<token>` 的 base64，例如 Atlassian 的个人 API token。网关在出口把占位符换成真的 token。
2. 向每个 MCP 服务器要工具列表。不在 `tools` 里的工具，全部用 `--disallowedTools` 禁用。跳过权限确认时，claude 只强制执行这一种规则。
3. 读不到工具列表时，禁用整个服务器，不放行任何工具。
4. 生成配置，加上 `--mcp-config` 和 `--strict-mcp-config` 启动 claude。仓库自带的 `.mcp.json` 不会加载。

所以 `tools` 是白名单，不只是提示。只登记读的工具。token 本身也应该只能读。`token_read_only: true` 可以记下登记人已确认这一点，不写也能挂载，挡住写操作的是 `tools` 白名单。

登记后用 `myorch knowledge check <名字> --project <项目>` 试一次：在和步骤一样的 sandbox 里连接服务器，对照白名单检查工具，可以再调用一次某个工具。

Jira 的例子（Atlassian Rovo MCP，组织管理员要先打开 API token 访问）：

```yaml
kind: mcp
url: https://mcp.atlassian.com/v1/mcp
provider: atlassian-mcp            # OpenShell provider，endpoint 只放行 mcp.atlassian.com
token_env: ATLASSIAN_TOKEN
auth: basic                        # 个人 API token；服务账号的 API key 用 bearer，不写 email
email: me@example.com
tools: [getJiraIssue, searchJiraIssuesUsingJql, getConfluencePage, searchConfluenceUsingCql]
token_read_only: true
index: 读工单用 getJiraIssue；查关联工单用 searchJiraIssuesUsingJql。
```

工具名以服务器返回的为准。启动日志里会列出「登记了但服务器没有」的工具。

### 快照

发起运行时自动同步，没有同步按钮。
- 文件夹每次都同步。内容没变时哈希相同，什么都不复制。路径不存在、扫描到疑似密钥、过滤后没有文件、超过 `max_size` 时，运行不会启动，错误直接显示出来。
  疑似密钥的规则在 `knowledge.SECRET_RULES`，development 的 takeaway 也用它。2026-11-09 加了三条：带字面凭证的 `Authorization: Basic/Bearer` 头、`https://用户:密码@` 地址、Azure DevOps PAT（52 个小写 base32 字符，纯十六进制的哈希不算）。已登记的文件夹里有这类内容时，下次同步会报出来；确实不是密钥的用 `allow_findings` 放行。
- git 仓库在从没同步过、或者最新快照超过 1 小时时同步。clone 在一个一次性的 sandbox 里做，token 留在 gateway。clone 失败但已有快照时，用上一次的快照，并在日志里记一条。
- MCP 没有快照：内容会变，查询记在 agent 的会话记录里。

也可以手动运行 `myorch knowledge sync <名字>`。快照存在 volume 的 `knowledge/<名字>/<哈希>/`，文件只读，每个知识源保留最近 5 个版本。设计和实现记录见 [notes/2026-11-03-knowledge-mounts.md](notes/2026-11-03-knowledge-mounts.md)。

## 接入一个新项目

1. **建项目包**：写 `myorch-knowledge/<name>/project.yaml`。同类技术栈写 `extends: <模板>`。最少填 `repo`、`base`、`identity`、`providers.github`、`image`。提交到 myorch-knowledge。
2. **同步**：`uv run myorch project sync <name>`。
3. **GitHub provider**：见 [4. 安装和启动 · 项目的 GitHub 凭证](04-setup.md#项目的-github-凭证)。`myorch project github-profile <name>` 生成只放行这个仓库的 profile。其他凭证（私有依赖源）用同样的方法建，映射到 `providers.artifacts`。
4. **开发镜像**：用模板的 `image/`，或者自己写，然后构建。
5. **网络 policy**（构建需要联网时）：在 `policy.yaml` 里放行需要的地址。`project.yaml` 写 `policy: policy.yaml`。
6. **onboard**：`uv run myorch run flows/onboard.yaml --project <name>`。看报告，挑建议。
7. **流程**：不用复制。流程所有项目共用，在 `project.yaml` 的 `flows.development` 里调 review 强度和要不要 takeaway，项目专有的要求写进 `prompts/`。见「共用流程的项目设置」「prompt 槽位」。
8. 用小需求试一次 development，确认分支、检查、推送都对。

## 改项目包

| 改什么 | 怎么改 |
|---|---|
| `guide/` 里的知识 | 优先跑 learn（有证据、可追溯）。也可以手改 |
| `checks.sh`、`checks.env` | learn、onboard（只有 `checks.sh`）都会提建议。development 的 review 循环发现它们有问题时也会提。也可以手改 |
| `setup.sh` | onboard，或 development 的 review 循环。也可以手改 |
| `risk-rules.yaml`、`security.md` | 只有 onboard 会提建议。也可以手改。开发运行不能改它们：运行不能改变自己的审批规则 |
| `project.yaml`、`prompts/`、`image/`、`providers/`、`sources/` | 流程永远不改它们。`sources/` 可以在控制台登记 |

流程能改哪些文件，由 `project-apply` 的 `scope` 决定（`src/myorch/project.py` 的 `EDITABLE`）：

| scope | 能改的文件 | 用在 |
|---|---|---|
| `learn` | `guide/`、`checks.sh`、`checks.env` | learn、postmortem |
| `onboard` | `guide/`、`checks.sh`、`setup.sh`、`risk-rules.yaml`、`security.md` | onboard |
| `fix` | `guide/`、`checks.sh`、`checks.env`、`setup.sh` | development 的 review 循环（`pack-apply`） |

建议都要你在运行里挑编号采纳后才写回。写回时 myorch 在 myorch-knowledge 里提交（`<scope>(<project>): ...`，带 `myorch-run: <run_id>`），然后重新同步快照。

手改以后，在 myorch-knowledge 里提交，然后运行 `uv run myorch project sync <name>`。不 sync，新的运行仍然挂旧快照。注意 `project.yaml` 有两个读的地方：
- `{{project.x}}` 在发起时直接读 myorch-knowledge 里的 `project.yaml` 和 `prompts/`，改了马上生效。
- 快照里的 `project.json`（脚本读它，例如 development 读 `approvers`）要 sync 以后才更新。

run 里 agent 写的 `docs/`、`memory/` 不会自动回到项目包。`flows.development.takeaway: true` 的项目（mdm），development 把每次运行的任务信息和 takeaway 提交到项目仓库的 `myorch/<日期>-<run_id>/`，随 PR 合进 base 分支。learn 从 base 分支读它们，读过的记在 `myorch-data/learn/<项目>/takeaways.json`，下次跳过。

### 快照

<!-- diagram: pack-lifecycle.html -->
图的文件：[diagrams/pack-lifecycle.html](diagrams/pack-lifecycle.html)。

sandbox 看到的是快照，挂在 `/sandbox/workspace/project/`，只读。快照和项目包有这些不同：

- 没有 `project.yaml`。合并后的配置（`{{project.x}}` 的值）在 `project.json` 里。脚本要读项目配置时读 `project.json`，或者读环境变量 `MYORCH_PROJECT_*`。
- 有模板时，先放模板的文件，再用项目的文件覆盖。`risk-rules.yaml` 和 `security.md` 是模板和项目拼接后的结果。另有一份 `risk-rules.json`。
- 没有 `image/`、`providers/`、`sources/`、`.git`。

每次同步按文件内容算一个版本号（`sha256:` 加 16 位哈希），写在 volume 的 `projects/<name>/<哈希>/`，文件只读。内容没变时版本号相同，不重写。`projects/<name>/.version` 记最新的版本。

- 新的运行用最新的版本，mandate 记下版本号。运行中的每一步都挂这个版本，所以同步不会改变正在跑的运行。
- 例外：运行自己用 `project-apply` 写回项目包后，它后面的步骤挂新版本。其他运行不受影响。
- 保留最近 3 个版本，以及 30 天内写的版本。最新的版本一直保留。
