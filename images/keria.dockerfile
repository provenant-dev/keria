FROM python:3.12-alpine AS builder

RUN apk --no-cache add \
    curl \
    bash \
    alpine-sdk \
    libffi-dev \
    libsodium \
    libsodium-dev \
    python3-dev \
    linux-headers \
    lmdb-dev

RUN curl --proto '=https' --tlsv1.3 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /keria

COPY pyproject.toml uv.lock README.md ./

ENV LMDB_FORCE_SYSTEM=1
ENV UV_NO_BINARY_PACKAGE=lmdb

RUN uv sync --locked --no-dev --no-editable

COPY src/ src/
RUN uv sync --locked --no-dev

FROM python:3.12-alpine
WORKDIR /keria

RUN apk --no-cache add \
    bash \
    curl \
    libsodium \
    libsodium-dev \
    libffi \
    lmdb \
    gcc

COPY --from=builder /keria /keria

ENV PATH="/keria/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 3901
EXPOSE 3902
EXPOSE 3903
ENTRYPOINT ["keria"]
CMD ["start"]
