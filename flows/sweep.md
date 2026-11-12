# sweep

找项目现有代码里的 bug。所有项目共用这一个流程。每次按固定顺序看一批文件，几个审查员并行看，核实后写一份日报。适合每晚定时跑，一轮一轮把整个仓库扫完。

它是观察模式：只写日报 `docs/sweep/report.md`，不改代码，不推送，不启动 development，也不改知识库。发现的问题要修，由人另起 [development](development.md)。和 [review](review.md) 的区别：review 看一个 PR 的改动，sweep 看已经在主分支上的代码。

```sh
uv run myorch run flows/sweep.yaml --project mdm                     # 跑一次，审查强度默认 standard
uv run myorch run flows/sweep.yaml --project mdm -i depth=deep
uv run myorch schedule add nightly-sweep-mdm --flow sweep --project mdm --cron '0 2 * * *'   # 每晚 2 点
```

控制台：先选项目，任务选「找 bug」，选审查强度。第一条消息不用写。

<!-- flow-graph -->

## 看哪些文件

由项目包 `project.yaml` 的 `sweep:` 决定（见 [7. 项目包](../docs/07-projects.md)）：

| 字段 | 含义 | 不写时 |
|---|---|---|
| `paths` | 只看这些目录或文件 | 整个仓库 |
| `include` | 只看匹配这些 glob 的文件（可以用 `**`） | 不筛选 |
| `exclude` | 不看匹配这些 glob 的文件 | 不排除 |
| `chunk` | 每次看几个文件（1 到 200） | 20 |

- 文件列表来自 `git ls-files`，按路径排序。
- mdm 和 blog 现在没有写 `sweep:`，所以看整个仓库，每次 20 个文件。myorch 只看 `src/myorch`、`flows`、`tools`、`deploy`（不看 `src/myorch/ui/static/vendor/**`），每次 15 个。
- sweep 读的是项目包快照里的配置。改了 `project.yaml` 以后要 `uv run myorch project sync <项目>`。
- 配置一个文件都没选中时，`pick` 失败。

## 每一步做什么

| 步骤 | 做什么 | 产物 |
|---|---|---|
| `sync` | 克隆或更新仓库，切到最新的 base 分支（[`lib/sync-base.yaml`](lib/sync-base.yaml)，用项目共享的 bare mirror 加速） | `repo/` |
| `pick` | 按游标取下一批文件，把已知问题复制一份进工作区。游标在这里不动 | `docs/sweep/chunk.txt`、`docs/sweep/known.json` |
| `audit` | 几个审查员各自读这批文件，找现在就存在的缺陷。只读，不改代码 | `docs/sweep/audit-<编号>.json` |
| `verify` | 逐条打开代码核实，去掉误报和已知问题，合并重复的，写日报 | `docs/sweep/verified.json`、`docs/sweep/report.md` |
| `record` | 把新问题记进 `known.json`，游标前移 | 共享目录里的 `cursor.json`、`known.json` |

### audit：找什么

- 正确性（逻辑错误、漏掉的边界情况、没处理的错误、和文档或注释不一致）、安全（注入、泄露的密钥、不安全的默认值、缺少的权限检查）、用户会看到的错误（坏链接、错误的数据或显示）。
- 每个审查员有自己的重点：1 号多看正确性和边界情况，2 号多看安全和错误处理，3 号多看文档和数据是否一致。
- 不报：代码风格、命名、能写得更好但没错的地方、缺少测试；已知问题；这批文件以外的代码（只用来理解上下文）。
- 严重程度只有两级：`Important`（错误行为、数据错误、安全问题，值得安排人修）和 `Nit`（确实是问题，但影响小）。没把握的不报。
- 有 `guide/review-guide.md` 时按它的标准看。

### verify：核实

- 每条都打开代码亲自核实，不成立的去掉。
- 几个审查员报的同一个问题合并成一条，`votes` 是报告它的人数。只有一个人报、核实成立的也保留。
- 和 `known.json` 里是同一个问题的去掉。
- 日报：没有新问题时只有一句「这批文件没有发现新问题。」，后面附看了哪些目录。有新问题时，第一句写 Important 和 Nit 各几条，再按严重程度列出每条的位置、问题、后果、建议怎么修。不写过程，不写客套话。

## 跨运行的状态

跨运行的状态放在项目共享目录 `shared/<项目>/sweep/`（myorch 存储的 volume 上）。只有 `pick` 和 `record` 挂载它。

| 文件 | 内容 | 谁写 |
|---|---|---|
| `cursor.json` | `{"next": 下一批从第几个文件开始, "total": 文件总数, "run": 写它的运行}` | `record` |
| `known.json` | 报告过的问题列表：`fingerprint`、`file`、`line`、`title`、`severity`、`run`、`at`（日期） | `record` 追加 |

- **整个运行成功后游标才前移。** `record` 是最后一步，前面任何一步失败，它都不执行，下次重扫同一批。
- 扫到最后一个文件后从头开始，所以能一直定时跑下去。
- 游标是排序后列表里的位置，不是文件名。文件增删后，下一批的起点会跟着错开几个文件：可能漏看或重看几个，下一轮会补上。
- 已知问题去两次重：verify 按意思判断是不是同一个问题，`record` 再按 `文件|标题` 的指纹去掉完全相同的。
- `known.json` 只增不减。想从头扫，删掉 `cursor.json`；想让某个问题再被报告，从 `known.json` 里删掉那一项。每个运行的工作区里也有一份当时的副本 `docs/sweep/known.json`。

## 你介入的地方

运行中不需要你。看日报：控制台打开运行，「文档」里的 `docs/sweep/report.md`；或者 `uv run myorch ws <run> docs/sweep/report.md`。值得修的问题，另起 development。

## 定时跑

- `--flow` 要写流程名 `sweep`。需要别的审查强度时加 `-i depth=quick` 或 `-i depth=deep`。
- 上一次定时发起的运行还在跑时，这一次跳过，所以两次运行不会同时动游标。不要加 `--allow-overlap`：两个运行会扫同一批文件，`known.json` 也可能被后结束的那个覆盖。
- 运行的发起人是建定时任务的人。`uv run myorch schedule list` 看下次什么时候跑，`pause`、`unpause`、`delete` 管理，`trigger` 立即跑一次。

## 产物

```
docs/sweep/
  chunk.txt          这次看的文件，每行一个
  known.json         开始时的已知问题副本
  audit-<编号>.json   每个审查员的发现
  verified.json      核实后的新问题
  report.md          日报
```
