"""English for the flows' own texts on the English Flow manual (docs/workflows/build.py).

The flows (flows/*.yaml) and the console are Chinese: input labels and hints, role names and hints
(myorch.core.inputs.ROLE_INFO), fragment descriptions. Keys are those texts exactly; values the English.
build.py lists the texts that have no entry yet (the English page then shows the Chinese).
"""

EN = {
    # roles (myorch.core.inputs.ROLE_INFO): the console's task names and hints
    '开发': 'Development',
    '从需求到 PR：需求分析、技术设计、实现、多人 review、风险审批。审批通过后才推送分支、创建 PR。': 'From requirement to PR: requirement analysis, technical design, implementation, review by several reviewers, risk approval. The branch is pushed and the PR opened only after approval.',
    '评审 PR': 'Review PR',
    '评审一个已有的 PR。结论只在控制台显示。': 'Review an existing PR. The verdict is shown only in the console.',
    '复盘': 'Postmortem',
    '复盘：问题已经进了代码。找出引入它的改动和本该拦住的层，提出知识库修改，并生成评测案例。不改项目代码。': 'Postmortem: a problem already got into the code. Find the change that introduced it and the layer that should have caught it, propose knowledge changes, and generate an eval case. Does not change project code.',
    '找 bug': 'Find bugs',
    '找现有代码里的 bug，每次看一批文件，只出日报。': 'Find bugs in existing code, a batch of files at a time; produces only a daily report.',
    '学习': 'Learn',
    '从历史运行里沉淀知识：改进 guide/、记录人做过的决定、加确定性检查。': 'Distil knowledge from past runs: improve guide/, record decisions people made, add deterministic checks.',
    '评测': 'Eval',
    '重放一个评测案例，给 reviewer 打分。': 'Replay an eval case and score the reviewers.',
    '接入项目': 'Onboard project',
    '接入新项目：分析仓库，提出项目包的修改。': 'Onboard a new project: analyse the repository and propose changes to the project pack.',

    # development
    'Jira 工单': 'Jira ticket',
    '例如 18700。启动后第一步读工单': 'For example 18700. The first step reads the ticket',
    '起点': 'Starting point',
    '需求': 'Requirement',
    '要改什么，越具体越好，可以写多行。有工单时工单就是需求，这里只写工单里没有的：重点、限制、要注意的地方': 'What to change, as specific as possible; several lines are fine. With a ticket, the ticket is the requirement; write here only what it does not say: focus, limits, things to watch',
    '出问题的运行': 'Run with the problem',
    '在哪次运行里出了问题，例如 development-1a2b3c4d。没有可以不填': 'The run where the problem happened, for example development-1a2b3c4d. Leave empty if there is none',
    '重放：基准提交（评测用）': 'Replay: base commit (for evals)',
    '平时留空。评测重放历史工单时填 PR 的 merge-base：从这个提交开始，删掉之后的历史，不审批、不交付': "Normally empty. When an eval replays a past ticket, the PR's merge-base: start from this commit, drop the later history, no approval, no delivery",
    '重放：工单截止时间（评测用）': 'Replay: ticket cut-off time (for evals)',
    '平时留空。ISO 时间，例如 2026-10-01T08:00:00Z：工单按这个时刻的样子读（描述回滚，只留之前的评论）': 'Normally empty. An ISO time, for example 2026-10-01T08:00:00Z: the ticket is read as it was at that moment (description rolled back, only earlier comments kept)',
    '工单评论': 'Ticket comments',

    # review
    'PR 编号': 'PR number',
    '例如 25': 'For example 25',
    '评审强度': 'Review depth',

    # postmortem
    '修复或 revert 提交': 'Fix or revert commit',
    '提交 SHA。和下一项二选一': 'A commit SHA. Give this or the next one',
    '引入问题的提交': 'Commit that introduced the problem',
    '知道时直接填 SHA': 'Its SHA, if you know it',

    # sweep
    '审查强度': 'Audit depth',

    # learn
    '要学习的运行': 'Runs to learn from',
    '可选。逗号分隔的运行 ID；留空时自动选上次 learn 之后的运行': 'Optional. Comma-separated run IDs; when empty, the runs since the last learn are picked',

    # eval
    '案例': 'Case',
    '案例 ID（流程 → 评测集）': 'Case ID (Flows → Eval set, 流程 → 评测集)',
    '测哪种强度': 'Depth to test',

    # onboard
    '要重点看的地方': 'Focus areas',
    '可选，例如「前端检查」「数据库迁移」': 'Optional, for example “frontend checks”, “database migrations”',

    # fragments (flows/lib/*.yaml description)
    '列出建议，让人挑选采纳哪几条': 'Lists proposals and lets a person pick which ones to adopt',
    '审批通过后推送分支，写交付说明（不调用平台 API）': 'After approval, pushes the branch and writes the delivery notes (calls no platform API)',
    '端到端测试：项目的固定冒烟用例 +（可选）按验收标准写的用例 →（可选）fix，按轮循环直到全部通过': "End-to-end tests: the project's fixed smoke cases + (optional) cases written from the acceptance criteria → (optional) fix, round after round until all pass",
    'CI → 并行 reviewer + security → verify 投票 →（可选）fix，按轮循环直到没有阻塞问题': 'CI → parallel reviewers + security → verify vote → (optional) fix, round after round until no blocking issue is left',
    '克隆或更新项目仓库，切到最新的 base 分支，初始化子模块': "Clones or updates the project's repository, checks out the latest base branch, initialises submodules",
}
