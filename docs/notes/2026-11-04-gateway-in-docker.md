# OpenShell gateway 放进 Docker（调研，2026-11-04）

状态：**调研完成，结论是可行**。compose 草稿在 `deploy/`（见 `deploy/README.md`），2026-11-04 用它跑通了 `deploy/smoke.yaml`。

背景：要把 myorch 打包成 docker compose，在另一台电脑上快速跑起来。myorch 是控制面：worker 调 `openshell` CLI 和 `docker` CLI，sandbox 由 gateway 在宿主机的 Docker 上创建。所以 myorch 自己进容器只要挂 docker.sock。问题在 gateway：它现在是 `brew services` 跑的宿主机进程。这份笔记记录 gateway 放进容器的实测结果。

## 测试方法

- 镜像：`ghcr.io/nvidia/openshell/gateway:0.1.6`。
- 在 17671 端口另起一个 gateway，状态放在单独的 volume 里，挂宿主机的 docker.sock。
- 本机的 brew 版 gateway（17670）和它的 provider 不动。
- 宿主机的 CLI 用 `OPENSHELL_GATEWAY_ENDPOINT=http://127.0.0.1:17671` 连探针 gateway，不切换默认 gateway。
- 测完删掉了探针的 sandbox、容器、volume、网络和临时文件。

## 结果

| 测试项 | 结果 |
|---|---|
| 官方镜像 | 有 `linux/amd64` 和 `linux/arm64`。镜像是 distroless（没有 shell），带 docker driver |
| 创建 sandbox，supervisor 回连 gateway | ✅ |
| 挂载带审批标签的 volume（`myorch-artifacts`，`subpath`，只读） | ✅ 在只读挂载上写入被拒绝 |
| 挂载没打审批标签的 volume | ✅ 被拒绝（not admitted by required labels） |
| provider 凭证 | ✅ sandbox 里只有占位符 `openshell:resolve:env:...` |
| 网络 policy | ✅ 放行的路径能连到 GitHub。没放行的路径返回 403。不在 `binaries` 里的程序（curl）连不出去。和 brew 版对比，结果一致 |
| 重启 gateway | ✅ provider 和 sandbox 记录都保留 |
| 回连地址 | ✅ docker driver 的 `grpc_endpoint` 有效 |

有一次 git 请求卡了 75 秒，supervisor 日志是连 github.com 超时（`NET:FAIL`）。再跑两次都在 1 秒内返回。判断是偶发的网络问题，和容器化无关。

sandbox 的两个容器（supervisor 和 workload）只用命名 volume（`openshell-channel-*`、`openshell-control-*`、`openshell-supervisor-*`），没有宿主机路径的 bind mount。所以 gateway 在容器里时，不会出现容器内路径和宿主机路径不一致的问题。

## 和 brew 版的不同

### 1. sandbox 签名密钥

docker driver 要求 gateway 能签发 sandbox 的启动凭证。不配置时，创建 sandbox 报错：

```
docker sandboxes require launch-scoped gateway authentication
```

brew 的启动脚本（`/opt/homebrew/opt/openshell/libexec/openshell-gateway-homebrew-service`）会自动准备密钥。容器版要自己准备：在 `OPENSHELL_LOCAL_TLS_DIR/jwt/` 下放 `signing.pem`、`public.pem`、`kid`，算法是 Ed25519。另一种方法是配置 `[openshell.gateway.gateway_jwt]`，没有测。

macOS 自带的 LibreSSL 不支持 Ed25519（`Algorithm ed25519 not found`）。要在容器里用 openssl 生成：

```sh
openssl genpkey -algorithm ed25519 -out signing.pem
openssl pkey -in signing.pem -pubout -out public.pem
echo <任意 id> > kid
```

### 2. 用户认证

不配置认证时，gateway 拒绝启动（`gateway has no user authentication configured`）。可选的方式：

- `[openshell.gateway.auth] allow_unauthenticated_users = true`：只适合本机自用。能连到端口的人都能操作 gateway，所以端口只能发布在 `127.0.0.1`。探针用的是这种方式，加 `--disable-tls`。
- TLS + mTLS，和 brew 版一样。**没有测。**
- OIDC。没有测。

### 3. supervisor 回连 gateway 的路线

- `supervisor_network_mode` 只接受 `auto`、`host`、`bridge`。不能填自定义网络。
- docker driver 没有 `network_name` 这个配置项（gateway 报 `unknown field`）。
- 所以 supervisor 不能加入 compose 的网络，只能经宿主机回连：gateway 发布端口，`grpc_endpoint` 指向 `host.docker.internal:<端口>`。gateway 自动给 supervisor 加 `host.docker.internal` 和 `host.openshell.internal` 两条 hosts 记录。
- Docker Desktop（macOS）：端口发布在 `127.0.0.1` 也能连通。已测。
- Linux：`host.docker.internal` 指向 docker0 网桥。发布在 `127.0.0.1` 的端口从网桥连不到。要么发布到网桥地址，要么用 `host` 模式。**没有测。**

### 4. 其他

- 默认参数是 `--bind-address 0.0.0.0 --port 8080`，看起来是给 Kubernetes（Helm）用的。docker driver 要求固定的非零端口。
- 状态目录由 `XDG_STATE_HOME` 决定，SQLite 数据库在 `<state>/openshell/...` 下。放在 volume 里就能跨重启保留。
- 镜像默认用户是 1000。在 Docker Desktop 上访问 docker.sock 需要 root，探针用了 `--user 0`。
- 宿主机的配置文件用 bind mount 挂进容器时，`sed -i` 会换掉文件的 inode，容器里看到的文件就失效了。改配置后要重建容器，或者用 `cat >` 原地覆盖。
- 凭证存在 gateway 自己的 volume 里。新电脑上仍然要执行一次 `openshell provider create`，和现在一样。

## 还没验证

- TLS 和 mTLS 模式下的容器版 gateway。
- Linux 主机上的回连。
- 在容器版 gateway 上跑带 agent 步骤的 flow。只有 script 步骤的 `deploy/smoke.yaml` 已经跑通（Temporal → worker → gateway → sandbox → volume）。
- 带 fake-IP 代理的机器上 `allowed_ips` 的行为。理论上和 gateway 放在哪里无关。

## OpenShell 0.1.6 的其他发布物

GitHub release 里有 Linux 版的 CLI（`openshell-{aarch64,x86_64}-unknown-linux-musl.tar.gz`，静态链接）和 gateway（`openshell-gateway-*-unknown-linux-gnu.tar.gz`，以及 rpm、deb）。myorch 的 worker 镜像用 CLI 的 musl 版。
