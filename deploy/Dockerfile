# myorch control plane: worker and console. Sandboxes are not started in here: the worker calls the
# openshell CLI (gateway service) and the docker CLI (helper containers on the host's daemon, through
# the mounted /var/run/docker.sock). Build context is the repository root:
#
#   docker compose -f deploy/compose.yaml build
FROM python:3.12-slim

ARG OPENSHELL_VERSION=0.1.6
ARG TARGETARCH

RUN apt-get update \
 # openssh-client: `openshell sandbox create -- <command>` (attached, e.g. myorch knowledge check)
 # runs the command over SSH; the worker's own steps use --detach and do not need it.
 && apt-get install -y --no-install-recommends ca-certificates curl git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 # Knowledge packs and local clones are bind mounts owned by the host user, not by root in here.
 && git config --system safe.directory '*'

# openshell CLI: the static (musl) build from the GitHub release.
RUN case "$TARGETARCH" in \
      amd64) arch=x86_64 ;; arm64) arch=aarch64 ;; *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && mkdir /tmp/os \
 && curl -fsSL "https://github.com/NVIDIA/OpenShell/releases/download/v${OPENSHELL_VERSION}/openshell-${arch}-unknown-linux-musl.tar.gz" \
    | tar -xz -C /tmp/os \
 && install -m 0755 "$(find /tmp/os -type f -name openshell | head -1)" /usr/local/bin/openshell \
 && rm -rf /tmp/os \
 && openshell --version

# docker CLI only (the daemon is the host's).
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

# The commit this image was built from (deploy/update.sh passes it): takeaways record it.
ARG MYORCH_COMMIT=""
ENV PATH=/opt/venv/bin:$PATH \
    MYORCH_COMMIT=$MYORCH_COMMIT \
    MYORCH_CONFIG=/app \
    MYORCH_KNOWLEDGE=/knowledge \
    MYORCH_DATA=/data
ENTRYPOINT ["myorch"]
