# Multi-stage: node builds the frontend, python runs the single process that
# serves the API, the SSE streams and that built bundle. Nothing here assumes a
# particular host — the deploy platform is still open.

# --- stage 1: frontend ------------------------------------------------------
FROM node:22-slim AS web

WORKDIR /build
# Both files, and no glob: `npm ci` needs the lockfile, and falling through to
# `npm install` when it is missing resolves fresh versions and silently builds
# something other than what was tested. A build that cannot be reproduced should
# fail rather than improvise.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build


# --- stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

# Pinned. With `:latest`, the image built today and the image built next month
# could resolve dependencies differently with no change anywhere in this repo.
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    BD_MODE=mock \
    SVERSE_RUNS_DIR=/data/runs \
    # Which proxy the app may believe about a client's IP. The per-client
    # cooldown and rate limit are keyed on it, so behind an ingress that is not
    # named here every visitor shares one TCP peer address and the throttle
    # becomes global. Set it to the platform's proxy on deploy.
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

# Dependencies first so a source-only change does not re-resolve them.
# `--frozen` refuses to update the lockfile: the image gets the versions this
# repo was tested against, or the build fails.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server/ ./server/
COPY tests/fixtures/ ./tests/fixtures/
COPY --from=web /build/dist ./web/dist

RUN uv sync --frozen --no-dev && mkdir -p /data/runs
VOLUME ["/data/runs"]

EXPOSE 8000

# urllib rather than curl: python is already in this image, and adding an apt
# layer for one small job is against the grain of the rest of the build.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

CMD ["sh", "-c", "exec uv run --no-dev uvicorn server.app:app \
     --host 0.0.0.0 --port 8000 --workers 1 \
     --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
