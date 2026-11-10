# 怎么在 Mac 上看 run 的文件：验证记录（2026-11-02）

状态：**已放弃（2026-11-03）**。用户决定 A 和 B 都不做，另有方案。下文只作为测试记录保留。原状态：A、B 都已实测。**补充（同日）：myorch 之后要给团队用，A 和 B 都只解决"在我这台 Mac 上看"，方向改为 C（服务端事件存储 + 网页），见文末。**

## 问题

每一步的产物（代码、`docs/`、manifest、日志、agent 对话）都写在 Docker volume `myorch-artifacts` 的 `runs/<run_id>/` 下。sandbox 删除后数据仍在，但 volume 位于 Docker Desktop 的虚拟机里，Finder 和 VS Code 看不到。现在只能用 `myorch ws`（一次读一个文件，每次约 1 秒）或 `myorch export`（手动复制整个 run，得到的是快照）。

## 方案 A：volume 改为由 Mac 目录支撑（bind 型 volume）

```sh
docker volume create --driver local --opt type=none --opt o=bind --opt device=<Mac 目录> \
  --label openshell.ai/sandbox-attachable=true --label openshell.ai/sandbox-attachable-workspace=default <名字>
```

实测结果：

- Docker 层面可用。辅助容器里 `chown 1000:1000` 后，容器内显示 1000，Mac 上显示为当前用户（uid 502），两边都能读写。
- **OpenShell 拒绝用它创建 sandbox**：
  `docker volume '…' is backed by a host bind mount and requires enable_bind_mounts = true in [openshell.drivers.docker]`
- 要用 A，需要在 `/opt/homebrew/var/openshell/gateway.toml` 的 `[openshell.drivers.docker]` 里设 `enable_bind_mounts = true`。从 gateway 程序里的错误信息看（`docker bind mounts require enable_bind_mounts = true`），这个开关同时放开了普通 bind mount，也就是任何能给 gateway 传 `--driver-config-json` 的调用，都可以把任意 Mac 目录（如 `~/.ssh`）挂进 sandbox。现在只有 myorch 调用 gateway，但这会少一道防线。
- 因为没有打开这个开关，**A 的读写性能没有测**（共享目录预计比虚拟机内部慢，Gradle 缓存受影响最大）。

A 的好处：agent 写的内容在 Mac 上实时可见，不需要同步。

## 方案 B：把 volume 同步到 Mac 目录

一个只读挂载 volume、读写挂载 Mac 目录的辅助容器（alpine + rsync）。实测：

| 操作 | 耗时 |
|---|---|
| 全量同步 `runs/`（29 万个文件，Mac 上 5.7 GB） | 19 分钟 |
| 全量再同步一次（没有变化，只扫描） | 116 秒 |
| 只同步一个 run（增量） | 1 秒 |

同步出来的文件在 Mac 上属主是当前用户，Finder 和 VS Code 可以直接打开。

体积几乎全在 `repo/` 的构建产物里。最大的 run 是 mdm 的 `onboard-17e1299e`：

| 目录 | 文件数 | 大小 |
|---|---|---|
| `repo/` | 133,737 | 3.4 GB |
| `.claude/`（agent 对话） | 13 | 1.9 MB |
| `docs/` | 7 | 100 KB |
| `.myorch/`（manifest、提问、日志） | 14 | 96 KB |
| `memory/` | 1 | 4 KB |

## 建议（倾向 B）

1. worker 在每一步结束后，只把这个 run 的 `docs/`、`.myorch/`、`.claude/`、`memory/` 同步到 `myorch-data/runs/<run_id>/`，约 1 秒；不改 gateway，sandbox 权限不变。
2. 代码不复制 `repo/`，只导出改动：`changes.diff`（相对 base）和提交列表。生成 diff 的 git 命令要带 `--no-ext-diff`、`-c core.fsmonitor=false`、`-c core.hooksPath=/dev/null`，避免执行仓库配置里的程序（控制台的 diff 已经这样做）。需要完整仓库时仍用 `myorch export`。
3. 加 `myorch sync <run>`，用来补同步历史 run。
4. 本地网页（ARCHITECTURE.md §12 第二步）读这个目录和 Temporal 状态。

## 顺带发现

- run 数据从不清理。`onboard-17e1299e` 一个 run 在 volume 里占 4.1 GB（mdm 的构建产物）。需要一个清理命令或保留策略。
- 验证用的 volume `myorch-bindtest`、目录 `myorch-data/bindtest`、`myorch-data/mirrortest` 都已删除；真实的 `myorch-artifacts` 没有改动。

## 补充：要给团队用之后（2026-11-02）

A 和 B 的前提是"一个人在自己的 Mac 上看文件"。团队使用时，数据不能只在某个人的电脑上，查看也不能靠打开文件夹，所以两者都只适合作为过渡。改为参考 pi-cloud-agent（github.com/zen8labs/pi-cloud-agent）的做法：

- **方案 C：事件存储。** 每一步的结果（manifest、docs、会话记录里的工具调用和用量、sandbox 内生成的截断 diff）由 worker 转成事件，写进服务端的数据库，大文件放文件存储。网页只读这里，不读 volume，sandbox 删掉也不受影响。sandbox 不需要任何网络回调，仍然只写自己的 `/sandbox/out`、`/sandbox/logs`。
- **展示**：网页服务，带登录。首页是"等你处理"（提问、审批），run 页是步骤时间线；点开一步看活动（工具调用）、产物、diff；review 轮次单独做卡片。审批和回答在网页上做，身份来自登录，不再是命令行自报。

团队化还会带来这些要求（不只影响查看）：

| 方面 | 现在（单人） | 团队需要 |
|---|---|---|
| 部署 | 一台 Mac，Docker Desktop，Temporal dev server | 一台 Linux 服务器（或集群），正式的 Temporal 和数据库，有资源和并发上限 |
| 身份和权限 | 无登录；审批人是 `--as` 自报的 | 登录（GitHub 或公司 SSO）；按项目控制谁能看、谁能发起、谁能审批 |
| 代码可见性 | 只有自己 | 会话记录和 diff 里有公司代码，按项目授权；写出前脱敏；有保留期限 |
| 模型凭证 | 个人订阅 token | 团队或组织的 API key；按项目和人统计费用（⑪ 的数据） |
| GitHub 身份 | mdm、java-lab 用个人账号 `leweii` | 不能以某个人的身份行动：bot 账号或 GitHub App |
| 安全 | 仓库配置会在本机执行程序、不可信内容进入网页等问题 | 别人的 PR、更多发起人，⑬-1～⑬-4 必须先修 |
