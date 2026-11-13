# Running the OpenShell gateway in Docker (investigation, 2026-11-04)

Status: **investigation complete; it is feasible**. The compose draft is in `deploy/` (see `deploy/README.md`). On 2026-11-04 it ran `deploy/smoke.yaml` successfully.

Background: we want to package myorch as docker compose so it can be brought up quickly on another computer. myorch is the control plane: the worker calls the `openshell` CLI and the `docker` CLI, and the gateway creates sandboxes on the host's Docker. So myorch itself only needs docker.sock mounted to run in a container. The problem is the gateway: it currently runs as a host process under `brew services`. This note records what we measured when running the gateway in a container.

## Test method

- Image: `ghcr.io/nvidia/openshell/gateway:0.1.6`.
- A second gateway on port 17671, with its state in a separate volume and the host's docker.sock mounted.
- The local brew gateway (17670) and its providers were left alone.
- The host CLI connected to the probe gateway with `OPENSHELL_GATEWAY_ENDPOINT=http://127.0.0.1:17671`, without switching the default gateway.
- After the tests we deleted the probe's sandboxes, containers, volume, network and temporary files.

## Results

| Test | Result |
|---|---|
| Official image | Available for `linux/amd64` and `linux/arm64`. The image is distroless (no shell) and includes the docker driver |
| Create a sandbox; supervisor connects back to the gateway | ✅ |
| Mount a volume with the admission label (`myorch-artifacts`, `subpath`, read-only) | ✅ Writes to the read-only mount are rejected |
| Mount a volume without the admission label | ✅ Rejected (not admitted by required labels) |
| Provider credentials | ✅ The sandbox only sees the placeholder `openshell:resolve:env:...` |
| Network policy | ✅ Allowed paths reach GitHub. Paths not allowed return 403. Programs not in `binaries` (curl) cannot connect out. Same results as the brew version |
| Restart the gateway | ✅ Providers and sandbox records are kept |
| Callback address | ✅ The docker driver's `grpc_endpoint` works |

Once a git request hung for 75 seconds. The supervisor log showed a timeout connecting to github.com (`NET:FAIL`). Two more runs both returned within 1 second. We judge it a one-off network problem, unrelated to containerisation.

The sandbox's two containers (supervisor and workload) use only named volumes (`openshell-channel-*`, `openshell-control-*`, `openshell-supervisor-*`), with no bind mounts of host paths. So with the gateway in a container, container paths and host paths cannot diverge.

## Differences from the brew version

### 1. Sandbox signing key

The docker driver requires the gateway to be able to sign sandbox launch credentials. Without configuration, creating a sandbox fails with:

```
docker sandboxes require launch-scoped gateway authentication
```

The brew start script (`/opt/homebrew/opt/openshell/libexec/openshell-gateway-homebrew-service`) prepares the key automatically. The container version has to prepare it itself: put `signing.pem`, `public.pem` and `kid` under `OPENSHELL_LOCAL_TLS_DIR/jwt/`. The algorithm is Ed25519. The alternative is to configure `[openshell.gateway.gateway_jwt]`, which we did not test.

The LibreSSL bundled with macOS does not support Ed25519 (`Algorithm ed25519 not found`). Generate the key with openssl inside a container:

```sh
openssl genpkey -algorithm ed25519 -out signing.pem
openssl pkey -in signing.pem -pubout -out public.pem
echo <any id> > kid
```

### 2. User authentication

Without authentication configured, the gateway refuses to start (`gateway has no user authentication configured`). The options:

- `[openshell.gateway.auth] allow_unauthenticated_users = true`: only for personal local use. Anyone who can reach the port can operate the gateway, so the port must be published on `127.0.0.1` only. The probe used this, with `--disable-tls`.
- TLS + mTLS, as in the brew version. **Not tested.**
- OIDC. Not tested.

### 3. How the supervisor connects back to the gateway

- `supervisor_network_mode` accepts only `auto`, `host` and `bridge`. A custom network is not allowed.
- The docker driver has no `network_name` setting (the gateway reports `unknown field`).
- So the supervisor cannot join the compose network. It can only connect back through the host: the gateway publishes a port, and `grpc_endpoint` points to `host.docker.internal:<port>`. The gateway automatically adds two hosts entries to the supervisor: `host.docker.internal` and `host.openshell.internal`.
- Docker Desktop (macOS): a port published on `127.0.0.1` is reachable. Tested.
- Linux: `host.docker.internal` points to the docker0 bridge. A port published on `127.0.0.1` is not reachable from the bridge. Either publish on the bridge address or use `host` mode. **Not tested.**

### 4. Other

- The default arguments are `--bind-address 0.0.0.0 --port 8080`, which look meant for Kubernetes (Helm). The docker driver requires a fixed non-zero port.
- The state directory is set by `XDG_STATE_HOME`. The SQLite database is under `<state>/openshell/...`. Put it in a volume to keep it across restarts.
- The image's default user is 1000. On Docker Desktop, access to docker.sock needs root, so the probe used `--user 0`.
- When a host configuration file is bind-mounted into the container, `sed -i` replaces the file's inode, and the container keeps seeing the stale file. After changing the configuration, recreate the container or overwrite in place with `cat >`.
- Credentials are stored in the gateway's own volume. A new computer still needs one run of `openshell provider create`, as today.

## Not yet verified

- The container gateway in TLS and mTLS mode.
- Connecting back on a Linux host.
- Running a flow with agent steps on the container gateway. `deploy/smoke.yaml`, which has only script steps, runs successfully (Temporal → worker → gateway → sandbox → volume).
- How `allowed_ips` behaves on machines with a fake-IP proxy. In theory this does not depend on where the gateway runs.

## Other OpenShell 0.1.6 release artifacts

The GitHub release has a Linux CLI (`openshell-{aarch64,x86_64}-unknown-linux-musl.tar.gz`, statically linked) and gateway (`openshell-gateway-*-unknown-linux-gnu.tar.gz`, plus rpm and deb). The myorch worker image uses the musl build of the CLI.
