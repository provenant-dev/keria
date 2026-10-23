# py-spy profiler sidecar. Debian-based so py-spy's prebuilt wheel installs cleanly;
# it shares KERIA's PID namespace at runtime (see compose.profiling.yaml) and attaches
# to the KERIA process. Deliberately separate from the KERIA image so the thing under
# profile stays pristine.
#
# Base is python:3.14-slim-bookworm because it's already cached locally (avoids a
# Docker Hub pull). py-spy is a standalone Rust binary installed via pip, so the host
# Python version here is irrelevant to what it profiles.
FROM python:3.14-slim-bookworm

RUN pip install --no-cache-dir py-spy

# procps gives pgrep/ps for locating the KERIA PID inside the shared namespace.
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
