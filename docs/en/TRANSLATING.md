# Translating the docs into English

The Chinese docs are the originals. The English docs live in this directory (`docs/en/`) and mirror them
file by file. Change both when you change one.

| Chinese original | English |
|---|---|
| `docs/README.md` | `docs/en/README.md` |
| `docs/NN-*.md` | `docs/en/NN-*.md` (same file names) |
| `ARCHITECTURE.md` | `docs/en/ARCHITECTURE.md` |
| `docs/workflows/README.md` | `docs/en/workflows/README.md` |
| `flows/<flow>.md` | `docs/en/flows/<flow>.md` |
| `docs/diagrams/gen.py` labels | `docs/diagrams/en.py` (Chinese text → English text) |
| texts in `flows/*.yaml` (input labels and hints, fragment descriptions) and role names | `docs/workflows/en.py` |

Not translated: `docs/notes/` (dated design notes), `deploy/README.md`, `PLAN.md`, the flows' YAML and prompts.
English pages link to the Chinese originals of these.

Build: `python3 docs/diagrams/gen.py docs/diagrams` writes the diagrams (English ones in `docs/diagrams/en/`;
it stops and lists any Chinese text missing from `en.py`). `uv run --with markdown python docs/build-html.py`
writes `docs/index.html` (English, the home page) and `docs/zh/index.html` (Chinese).
`uv run --with markdown python docs/workflows/build.py` writes `docs/workflows/index.html` (English) and
`docs/zh/workflows/index.html` (Chinese), and lists flow texts missing from `docs/workflows/en.py`.

## Style

- Plain technical English, the same register as the Chinese: short sentences, one fact per sentence, a clear
  subject. No marketing words, no filler ("simply", "seamlessly", "powerful").
- Keep the structure: same headings (translated), same tables, same lists, same order. Do not add or drop
  content. Translate meaning, not word by word.
- Keep verbatim: code, commands, file paths, identifiers, YAML keys, input names, step ids, flow names, role
  names, profile names, env vars, URLs, and the markers `<!-- diagram: name.html -->` and `<!-- flow-graph -->`.
- Inside code blocks: translate Chinese comments; keep commands. Translate illustrative values (example
  labels, example messages). Keep values the code matches on (e.g. reply `1`, `all`, `none`, `yes`).
- Product UI text is Chinese (console buttons, Slack buttons, tabs, chips). Give the English meaning and the
  original label in parentheses the first time it appears in a section, e.g. **Approve** (批准),
  **Continue on this branch** (在这个分支上接着改), the **Waiting on you** list (等你处理). After that, English only.
- Text the product prints in Chinese (error messages, CLI output): keep it verbatim in backticks and add the
  English meaning in parentheses, so readers can search for the literal text.
- Circled numbers ①②③④ stay.
- Dates stay ISO (2026-11-10).

## Glossary

| 中文 | English |
|---|---|
| 流程 | flow |
| 流程手册 | Flow manual |
| 使用手册 | User manual |
| 步骤 | step |
| 运行 / run | run |
| 发起（运行） | start (a run) |
| 项目 | project |
| 项目包 | project pack |
| 片段 | fragment |
| 角色 | role |
| 循环 / 轮 | loop / round |
| 跑满 | (the loop) runs out of rounds / is exhausted |
| 反思 | reflect (the reflect step) |
| 提问 / 回答 | question / answer |
| 审批 / 批准 / 驳回 / 提修改意见 | approval / approve / reject / request changes |
| 控制台 | console |
| 宿主机 / 宿主机动作 | host / host action |
| 工作区 | workspace |
| 凭证 | credentials |
| 知识 | knowledge |
| 检查 | checks |
| 风险判定 | risk verdict |
| 交付 | delivery |
| 推分支 | push the branch |
| 在原分支上接着改 / 接着改 | continue on the same branch / continue |
| 重新执行 | re-run (a step); resume (a run) |
| 评测 / 评测案例 / 评测集 | eval / eval case / eval set |
| 复盘 | postmortem |
| 找 bug | sweep (find bugs) |
| 学习 | learn |
| 接入项目 | onboard (a project) |
| 建议 / 采纳 | proposal / adopt |
| 渠道 / 适配器 | channel / adapter |
| 事件 / 摘要 | event / digest |
| 镜像 | image |
| 冒烟测试 | smoke test |
| 标签 | tag (run tags); label (input label) |
| 输入 | input |
| 产物 | artifact |
| 名词表 | Glossary |
| 概览 | Overview |
| 服务和依赖 | Services and dependencies |
| 架构 | Architecture |
| 安装和启动 | Setup |
| 命令参考 | CLI reference |
| 流程清单 | Flows |
| 写流程 | Writing flows |
| 设计原则 | Design principles |
| 使用规范 | Conventions |
| 排错 | Troubleshooting |
| 现状 | Status |
| 首页 | Home |

Console labels already in use (keep them):

| 中文 | English |
|---|---|
| ＋ 新运行 | + New run |
| 等你处理 | Waiting on you |
| 流程 / 流程 → 评测集 | Flows / Flows → Eval set |
| 步骤 / 文档 / 改动 / 实时输出 | Step / Docs / Changes / Live output (right-pane tabs) |
| 批准 / 提修改意见 / 驳回 | Approve / Request changes / Reject (Slack: 要求修改, Request changes) |
| 取消 | Cancel |
| 重新执行失败的步骤 / 从这一步重新执行 | Re-run failed steps / Re-run from this step |
| 在这个分支上接着改 | Continue on this branch |
| 重新开始 / 跳过 / 终止 | Start over / Skip / Stop |
| 可能问你 / 有条件 | May ask you / conditional |
| 开发 / 评审 PR / 复盘 / 找 bug / 学习 / 评测 / 接入项目 | Development / Review PR / Postmortem / Find bugs / Learn / Eval / Onboard project |

## Links

Every relative link must resolve from the English file's own location.

- To a document that has an English version (table above): link the English version. Example: in
  `docs/en/05-cli.md`, `07-projects.md`, `workflows/README.md`, `flows/development.md`, `ARCHITECTURE.md`.
- To anything else (`docs/notes/…`, `deploy/README.md`, `PLAN.md`, `src/…`, `flows/*.yaml`, `flows/lib/…`):
  the original file, path recomputed from the new location. Example: from `docs/en/03-architecture.md`,
  `../notes/2026-11-02-channel-layer.md`, `../../deploy/README.md`, `../../src/myorch/evals.py`.
- A link to the generated manual `workflows/index.html` becomes a link to `workflows/README.md` (from
  `docs/en/`) so the build points it at the right page.
- Anchors use the English heading's slug: lowercase, punctuation removed (including dots), spaces become
  hyphens. `## 1.2 Directories and storage` → `#12-directories-and-storage`. Keep anchors in links to
  Chinese-only files as they are.
