# Profiling build of KERIA — functionally the same as keria/images/keria.dockerfile,
# but adapted to build with ZERO registry pulls on this host:
#   * base = the locally-cached ECR mirror of python-alpine (3.14, not Docker Hub 3.12.8)
#   * uv is pip-installed instead of COPYd from ghcr.io/astral-sh/uv
#   * `uv sync` runs WITHOUT --locked so deps re-resolve for 3.14 (building any sdist
#     that lacks a 3.14/musl wheel; the toolchain below can compile them)
#
# NOTE: the local keria+keripy SOURCE is shadowed at runtime via PYTHONPATH (see
# compose.profiling.yaml), so this image only needs the dependency tree to install and
# import — which exact keri/keria ends up in site-packages is irrelevant at runtime.
# PyPI/GitHub fetches during the build are not Docker Hub pulls.
FROM public.ecr.aws/docker/library/python:3.14.7-alpine3.24 AS builder

RUN apk --no-cache add \
    curl bash alpine-sdk libffi-dev libsodium libsodium-dev rust cargo

RUN pip install --no-cache-dir uv

WORKDIR /keria
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --no-editable
COPY src/ src/
RUN uv sync --no-dev

FROM public.ecr.aws/docker/library/python:3.14.7-alpine3.24
WORKDIR /keria

RUN apk --no-cache add bash curl libsodium-dev gcc

COPY --from=builder /keria /keria

ENV PATH="/keria/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 3901
EXPOSE 3902
EXPOSE 3903
ENTRYPOINT ["keria"]
CMD ["start"]
