# syntax=docker/dockerfile:1
#
# ONE forecasting portal — single image serving BOTH the API and the built
# SPA on port 8600.
#
# No nginx container: api/main.py already mounts frontend/dist with a
# 404 -> index.html fallback for BrowserRouter deep links, outside the auth
# gate. TLS and (later) Entra ID sign-in are terminated by nginx on the DGX
# host — see deploy/nginx-onefcst.conf, which already exists.
#
# Build from the REPO ROOT:
#   docker build -t onefcst:latest --build-arg VITE_API_KEY=<key> .

# ---------------------------------------------------------------- frontend
FROM node:24-slim AS frontend
WORKDIR /build

# package files first so `npm ci` is cached until dependencies actually change
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci

COPY app/frontend/ ./

# VITE_* are compiled INTO the bundle — they are not runtime config. Rotating
# the API key therefore needs an image rebuild, not just a restart. Same
# caveat as deploy/frontend.env.production.example.
ARG VITE_API_BASE=/api
ARG VITE_API_KEY=
ENV VITE_API_BASE=${VITE_API_BASE} \
    VITE_API_KEY=${VITE_API_KEY}
RUN npm run build


# ----------------------------------------------------------------- runtime
FROM python:3.13-slim AS runtime

# postgresql-client-17 — rebuild_preserving.backup_db() shells out to
#                 pg_dump. NOT optional: a failed backup now ABORTS the
#                 rebuild by design, so an image without it cannot rebuild
#                 at all. Version matters — pg_dump refuses to dump a server
#                 NEWER than itself, and Debian trixie ships client 15/16
#                 while compose runs postgres:17, so pull the client from
#                 PGDG rather than taking the distro default.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    # mdbtools is deliberately NOT installed any more: since 2026-07-30 the
    # pipeline reads stock from the weekly ZM13 xlsx, not SAP在庫抽出.accdb.
    apt-get install -y --no-install-recommends postgresql-client-17; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

# The directory layout is load-bearing and must NOT be flattened:
#   config.py  resolves _ROOT via parents[3]  -> /opt/onefcst
#   main.py    resolves the SPA via parents[3] -> /opt/onefcst/app/frontend/dist
# Keeping app/backend + app/frontend/dist under /opt/onefcst means zero code
# changes; copying only the backend would break every default path.
WORKDIR /opt/onefcst

COPY app/backend/requirements.txt app/backend/requirements.txt
RUN pip install --no-cache-dir -r app/backend/requirements.txt

COPY app/backend/ app/backend/
COPY --from=frontend /build/dist app/frontend/dist

# config.py does mkdir() on DATA_DIR/EXPORT_DIR at IMPORT time, so these must
# be writable by the runtime user even before a volume is mounted over them.
RUN useradd --system --uid 10001 --create-home onefcst \
 && mkdir -p /opt/onefcst/app/data/exports /opt/onefcst/app/data/backups \
 && chown -R onefcst:onefcst /opt/onefcst/app/data
USER onefcst

WORKDIR /opt/onefcst/app/backend
EXPOSE 8600

# /health is exempt from the API-key gate (api/auth.py), so this works
# whether or not ONEFCST_API_KEY is set.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8600/health', timeout=4).status==200 else 1)"

# Deliberately ONE worker. The scheduler starts per-process from the FastAPI
# lifespan and rebuild_status/rate_limit hold state in memory, so a second
# worker would run a second scheduler and two rebuilds could drop the
# database concurrently. Matches deploy/onefcst-backend.service, which is
# also single-process. For 15-20 users this is correct sizing.
CMD ["python", "-m", "uvicorn", "onefcst.api.main:app", \
     "--host", "0.0.0.0", "--port", "8600", "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
