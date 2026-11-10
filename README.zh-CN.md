# myorch

English: [README.md](README.md)

流程引擎驱动的 agent 平台。Temporal 编排流程，每个步骤在一个 OpenShell sandbox 里运行，步骤之间通过共享的 volume 交接产物。人在本地控制台（`myorch ui`）或 Slack 私信里发起运行、回复提问、审批。本机的 Claude Code 会话可以通过 channel 收到运行结果，读日志、发起后续流程（不能审批）。整套服务用 docker compose 运行，见 [deploy/README.md](deploy/README.md)。

**文档在 [docs/](docs/README.md)**（网页版 https://leweii.github.io/myorch/zh/ ，英文 https://leweii.github.io/myorch/ ）：服务和依赖、架构、安装和启动、命令参考、流程清单、项目包、写流程、设计原则、使用规范、排错、现状。每个流程做什么、每一步做什么，见[流程手册](docs/workflows/README.md)。设计理由见 [ARCHITECTURE.md](ARCHITECTURE.md)。

**文档和代码双向链接。** 文档里的路径链接到这里的文件；每个代码目录有一个生成的 `README.md`，列出描述其中文件的文档章节（从 [src/myorch/](src/myorch/) 看起）；每个包和流程文件都有一行 `Docs:` 链接。

## 目录

myorch 的东西放在四个地方：

```
<父目录>/
├── myorch/              ① 代码仓库（本仓库）：src/、flows/、profiles/、images/、tools/、deploy/、docs/。和项目无关
├── myorch-knowledge/    ② 项目包仓库：每个项目一个目录，模板在 _templates/。learn 等流程的改进写回这里（私有仓库 leweii/myorch-knowledge）
└── myorch-data/         ③ 本机运行数据：temporal.db、events.db、评测集（不进 git）

Docker volume myorch-artifacts   ④ 运行时的文件：工作区、② 的快照、共享缓存。sandbox 只挂这里
```

作用、关系和数据流见 [docs/01-overview.md · 目录和存储](docs/01-overview.md#目录和存储)。

## 快速开始

前置条件：macOS、Docker Desktop、gh（已登录）、uv。Temporal 和 OpenShell gateway 在 compose 里，不用另装。

新的 Mac 上，克隆本仓库，把凭证文件放进 `deploy/secrets/`，再运行安装脚本：

```sh
bash deploy/bootstrap-mac.sh       # 克隆 myorch-knowledge、构建镜像、起服务、同步项目包、冒烟测试
```

已经装好时：

```sh
docker compose -f deploy/compose.yaml up -d --build   # 起服务；改了 src/ 或 flows/ 以后也用它
uv run myorch list                                    # 宿主机上的 CLI 连 compose 里的 Temporal
```

控制台：http://127.0.0.1:8800/ 。完整步骤见 [docs/04-setup.md](docs/04-setup.md) 和 [deploy/README.md](deploy/README.md)。

## 开发

```sh
uv sync
uv run pytest          # 单元测试，不依赖 Temporal 和 OpenShell
uv run lint-imports    # 检查分层依赖
```

## 许可证

[MIT](LICENSE)
