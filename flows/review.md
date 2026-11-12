# review

PR 评审：对一个已有的 PR（包括人写的）做一轮 AI review。所有项目共用这一个流程。它先跑项目的检查，再让 reviewer 看代码；项目有端到端测试时，最后用浏览器测运行中的应用。结论只在本地控制台（运行的「改动」标签页）和工作区的 `docs/review/round-1/` 里，不往 GitHub 上发任何东西。它只找问题：不改代码，不批准 PR，也不要求修改，因为 agent 的批准不算数。对 GitHub 只读。

它用的 review 循环和 [development](development.md) 是同一个片段 [`lib/review-loop.yaml`](lib/review-loop.yaml)，但只跑 1 轮、不修复、有阻塞问题也照常结束。项目的 `prompts/review.md`、`verify.md` 同样拼进去（例如 core-ng 项目的 `*-interface` 检查、博客的写作规范和误公开检查）。

```sh
uv run myorch run flows/review.yaml --project mdm -i pr=13                # 默认 standard
uv run myorch run flows/review.yaml --project blog -i pr=13 -i depth=deep
```

控制台：先选项目，任务选「评审 PR」，填 PR 编号，选评审强度。第一条消息可以不写。

评审强度（`depth`）决定 reviewer 的个数、模型和要不要单独做安全 review，见下面的预设表。日常的 PR 用 standard。只想快速看一眼用 quick。改了构建、依赖、安全相关的地方用 deep。verify 总是用 Opus。

<!-- flow-graph -->

上面的图按 mdm 展开。mdm 和 myorch 没有设置 `e2e.image`，没有 `e2e` 块；blog 有。

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `fetch-pr` | 用 GitHub API 读 PR 和改动文件列表；克隆仓库，拉取 base 分支和 `pull/<n>/head`，在这次运行的分支上切到 PR 的 head，初始化子模块 | `docs/pr.md`、`docs/external/pr.md` |
| `review-round` 循环（1 轮） | `ci`（项目的 `checks.sh`，检查 PR 改动的文件）→ `review`（个数由强度决定）和 `security`（quick 没有）并行 → `verify` 核实。没有 `fix` | `docs/ci/round-1.md`、`docs/review/round-1/` |
| `e2e` 循环（1 轮） | 只在项目设置了 `e2e.image` 时有这一块（现在只有 blog）。`e2e-smoke` 跑项目包的固定冒烟用例；`e2e-accept` 把 PR 描述里能用浏览器检查的验收标准写成用例，运行它们。只测不修 | `docs/e2e/round-1/` |

- 两个块都只跑 1 轮，`fix: false`、`on_exhausted: accept`：有阻塞问题或测试失败，运行也照常结束，只报告。
- `e2e` 在 `review-round` 之后，用的是 `ci` 准备好的检出。

## 你介入的地方

没有要你回答的提问，也没有审批。某一步失败时运行就失败（这个流程没有设 `on_failed: ask`）。

| 时机 | 控制台 | 命令行 |
|---|---|---|
| 运行结束后 | 「改动」标签页看 diff 和核实过的发现，「文档」页看检查报告和端到端测试的报告、截图 | `myorch ws <run> docs/review/round-1/summary.md` |
| 看完以后 | — | 在 GitHub 上自己决定怎么处理这个 PR |

## 产物

```
docs/
├── pr.md                    可信的元数据：作者、状态、head → base、改动文件
├── external/pr.md           PR 作者写的标题和描述（只当参考数据）
├── ci/round-1.md            检查报告
├── review/round-1/
│   ├── reviewer-K.json      每个 reviewer 的发现
│   ├── security.json        安全 review 的发现（quick 没有）
│   ├── verified.json        核实以后的发现：Important / Nit / Pre-existing
│   └── summary.md           数量、阻塞问题列表、验收标准表
└── e2e/round-1/             有端到端测试的项目
    ├── smoke/               report.md、result.json、截图
    ├── acceptance/          report.md、result.json、截图，cases/ 是写出的用例
    └── acceptance.md        每条验收标准的结论
```

## 原则

- **每次都 review 整个 PR**：myorch 不在 GitHub 上留标记，没有增量 review。`fetch-pr` 里「上次 review 到」的逻辑已经不用了：`last_reviewed` 总是空的，`changed` 总是 true，所以 `review-round` 每次都跑，第 1 轮看的是 `origin/<base>...HEAD`。
- **可信的和不可信的分开放**：作者、状态、改动文件、SHA 写进 `docs/pr.md`。PR 作者写的标题和描述写进 `docs/external/pr.md`，每个 agent 都把 `docs/external/` 当作参考数据，里面的指令不执行。
- **reviewer 的规则和开发流程一样**：先独立判断再核对作者的说法、到仓库里查复用和漏掉的入口、放宽的判断，详见 [development 的 review 循环](development.md#review-循环)。1、2、3 号各有侧重，quick 只有 1 号（侧重正确性和边界）。
- **verify 逐条核实**：不管几个 reviewer，每条发现都打开代码核实，不成立的删掉，只有一个人报的也核实。`votes` 只记录几个人报告了它。Nit 最多留 5 条，检查失败算 Important。
- **需求文档**：这个流程没有给 review 循环传 `requirement_doc`，reviewer 和 verify 被告知的需求文档是 `docs/requirements.md`，而评审运行里没有这个文件。所以它们只能按改动本身判断，PR 描述只在 `docs/external/pr.md` 里当参考。端到端块传的是 `docs/pr.md`。

## 端到端测试

- review 读代码，e2e 测运行中的应用，两者分开。用什么工具测由项目决定：`project.yaml` 的 `e2e:`（`image`、`profile`、`acceptance`），项目包的 `e2e/`（博客用 Playwright 和无头 Chromium，见 `myorch-knowledge/blog/e2e/README.md`）。
- 测试步骤的权限是 `e2e.profile`（blog 是 `dev-read`：仓库只读，没有凭证）。
- 冒烟用例失败只是测试环境的问题（例如浏览器没起来）时，报告里单独列为「环境」，不算代码的问题。
- `e2e-accept` 不能为了通过而放宽用例。用例失败时先判断是用例写错了还是改动有问题，改动有问题就保留用例。

## 安全

- 别人的 PR 代码不可信，`ci` 和端到端测试会执行它（构建脚本、测试、lint 配置）。执行 PR 代码的步骤（`ci`、`e2e-smoke`、`e2e-accept`）和读代码的 reviewer 都拿不到 GitHub 凭证，在跑完即删的沙箱里。
- 有凭证的一步（`fetch-pr`）只运行流程里写死的脚本，不执行 PR 里的任何代码。它只读：拉代码、读 PR。
- 剩下的风险是 prompt 注入：PR 内容可能诱导 reviewer 写出误导的结论。结论只在本机显示，不发到 GitHub。
