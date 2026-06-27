# LXD Management API

A production-ready CRUD REST API for managing an [LXD](https://linuxcontainers.org/lxd/)
server — instances, storage, networks, projects, and images — built with
**FastAPI + Pydantic v2** and talking **directly to the LXD REST API** over raw
`httpx` (no `pylxd` or any LXD SDK).

It supports **both** ways LXD's own clients connect:

| Mode   | Transport                         | Auth                          | Use when                                |
|--------|-----------------------------------|-------------------------------|-----------------------------------------|
| local  | Unix socket (`unix.socket`)       | implicit (socket access)      | API runs on the LXD host / same pod     |
| remote | HTTPS with **mutual TLS**         | client cert + key (trust add) | API runs elsewhere; `lxc remote add`    |

> This API's own client auth (JWT) is **separate** from LXD's mTLS auth, which
> is used only for the API-to-LXD connection.

---

## Features

- **Versioned API** — everything under `/api/v1`; `/health` stays unversioned.
- **Instances** — CRUD, start/stop/restart/freeze/unfreeze, exec & console over
  WebSocket, state (CPU/mem/IPs), logs, snapshots, backups (with tarball export).
- **Storage** — pools + volumes CRUD, resize, attach/detach volumes to instances.
- **Networks** — CRUD, state/leases, NIC attach/detach.
- **Projects** — CRUD + per-request project scoping (`?project=`).
- **Images** — list/get/delete, copy from remote image servers (`ubuntu:`, `images:`).
- **Async operations** — list/get/wait/cancel + a WebSocket relay of LXD's event stream.
- **JWT auth + RBAC** — `admin` > `operator` > `viewer` role hierarchy.
- **Resilience** — structured JSON logging, request IDs, global error handler,
  CORS, rate-limited auth endpoints, deep `/health` that checks LXD connectivity.

---

## Status

[![CI](https://github.com/alilotfi23/lxd-api/actions/workflows/ci.yml/badge.svg)](https://github.com/alilotfi23/lxd-api/actions/workflows/ci.yml)

> **Badge placement:** replace `alilotfi23` with your GitHub owner/repo and
> paste the line above at the top of this README. The badge reflects the
> `.github/workflows/ci.yml` pipeline added in the CI step.

---

## Quick start (Docker)

```bash
# 1. Configure
cp .env.example .env
#   -> set LXD_CONNECTION_MODE and edit connection details

# 2. Build & run
make up          # docker compose up --build -d

# 3. Check it
curl http://localhost:8000/health
# -> {"status":"ok","version":"1.0.0"}

# 4. Open the docs
open http://localhost:8000/docs
```

The default admin account is seeded from `SEED_ADMIN_USERNAME` /
`SEED_ADMIN_PASSWORD` on first startup (set both in `.env`, or leave blank and
create users via `/auth/register` after seeding one manually).

---

## Connection modes (concrete config)

### A) Local — Unix socket

The API container mounts the host's LXD socket. **Two socket paths exist**,
depending on how LXD was installed:

| Install | Socket path                                   |
|---------|-----------------------------------------------|
| snap    | `/var/snap/lxd/common/lxd/unix.socket`        |
| native  | `/var/lib/lxd/unix.socket`                    |

`.env`:
```ini
LXD_CONNECTION_MODE=local
LXD_SOCKET_PATH=/var/snap/lxd/common/lxd/unix.socket
```

`docker-compose.yml` mounts it read-only:
```yaml
volumes:
  - "${LXD_SOCKET_PATH}:/var/snap/lxd/common/lxd/unix.socket:ro"
```

### B) Remote — mutual TLS

LXD's network API authenticates with **client certificates** (this is what
`lxc remote add <name> <url>` does — it exchanges certs and trusts them).
Generate a client cert, add it to the LXD server's trust store, then point the
API at the cert/key files.

Generate a client cert/key pair:
```bash
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout client.key -out client.crt -subj "/CN=lxd-api"
```

Trust it on the LXD server (one of):
```bash
# From a trusted machine:
lxc config trust add client.crt
# Or via the LXD REST API on the server:
lxc remote add lxd-api https://lxd-host:8443   # then confirm fingerprint
```

`.env`:
```ini
LXD_CONNECTION_MODE=remote
LXD_REMOTE_URL=https://lxd-host:8443
LXD_CLIENT_CERT_PATH=/run/secrets/lxd/client.crt
LXD_CLIENT_KEY_PATH=/run/secrets/lxd/client.key
LXD_TRUSTED_CA_PATH=/run/secrets/lxd/server-ca.crt   # "" to skip verification (dev)
```

Mount the cert directory in compose (uncomment the `LXD_TLS_DIR` volume):
```yaml
volumes:
  - "${LXD_TLS_DIR:-./tls}:/run/secrets/lxd:ro"
```

---

## Environment variables

All settings live in `.env` (see `.env.example`). Key ones:

| Variable                          | Default                                           | Description                              |
|-----------------------------------|---------------------------------------------------|------------------------------------------|
| `APP_PORT`                        | `8000`                                            | HTTP port                                |
| `CORS_ORIGINS`                    | `*`                                               | Comma-separated allowed origins          |
| `LXD_CONNECTION_MODE`             | `local`                                           | `local` or `remote`                      |
| `LXD_SOCKET_PATH`                 | `/var/snap/lxd/common/lxd/unix.socket`            | Local-mode socket                        |
| `LXD_REMOTE_URL`                  | `https://lxd-host:8443`                           | Remote-mode LXD URL                      |
| `LXD_CLIENT_CERT_PATH`            |                                                   | Client cert (remote mTLS)                |
| `LXD_CLIENT_KEY_PATH`             |                                                   | Client key (remote mTLS)                 |
| `LXD_TRUSTED_CA_PATH`             |                                                   | Server CA (remote); `""` = skip verify   |
| `LXD_TIMEOUT`                     | `30`                                              | Per-request LXD timeout (s)              |
| `DATABASE_URL`                    | `sqlite+aiosqlite:///./data/lxd_api.db`           | User store (JWT subjects)                |
| `JWT_SECRET`                      | *(change me)*                                     | HS256 signing secret                     |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30`                                              | Access token lifetime                    |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS`   | `7`                                               | Refresh token lifetime                   |
| `SEED_ADMIN_USERNAME`             |                                                   | First-run admin username                 |
| `SEED_ADMIN_PASSWORD`             |                                                   | First-run admin password                 |

---

## Roles & permissions (RBAC)

Roles are hierarchical — `require_role("operator")` also admits `admin`.

| Role      | Level | Can do                                                                 |
|-----------|-------|------------------------------------------------------------------------|
| `viewer`  | 1     | All `GET` endpoints (read-only)                                        |
| `operator`| 2     | Instance lifecycle, exec/console, snapshots/backups, attach/detach     |
| `admin`   | 3     | Everything: CRUD on pools/networks/projects/images, user management    |

Examples: `POST /instances/{name}/start` → operator+; `DELETE /storage/pools/{name}` → admin only.

---

## Authentication (JWT)

```bash
# Login (rate-limited) -> access + refresh tokens
TOKEN=$(curl -s localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"changeme123"}' | jq -r .access_token)

# Use it
curl localhost:8000/api/v1/instances -H "Authorization: Bearer $TOKEN"

# Refresh
curl localhost:8000/api/v1/auth/refresh \
  -H 'Content-Type: application/json' \
  -d "{\"refresh_token\":\"$REFRESH\"}"
```

**WebSocket auth:** browsers can't set headers on a WS upgrade, so pass the
JWT as a query param: `ws://host/api/v1/instances/{name}/exec/ws?token=<jwt>`.

---

## Example curl requests

See `api.http` for a complete, runnable collection (works in VS Code REST
Client / JetBrains). Highlights per resource:

```bash
# Instances
curl localhost:8000/api/v1/instances -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/instances -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"web1","source":{"type":"image","alias":"ubuntu/22.04"}}'
curl -X PUT localhost:8000/api/v1/instances/web1/state -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"action":"start"}'

# Project-scoped request
curl "localhost:8000/api/v1/instances?project=staging" -H "Authorization: Bearer $TOKEN"

# Storage
curl localhost:8000/api/v1/storage/pools -H "Authorization: Bearer $TOKEN"

# Networks
curl localhost:8000/api/v1/networks -H "Authorization: Bearer $TOKEN"

# Projects
curl -X POST localhost:8000/api/v1/projects -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"staging"}'

# Images (async — returns an operation ref)
curl -X POST localhost:8000/api/v1/images -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"source":{"type":"image","alias":"ubuntu/22.04","server":"https://images.linuxcontainers.org","protocol":"simplestreams"}}'

# Operations (poll the async op from above)
curl localhost:8000/api/v1/operations/<id>/wait -H "Authorization: Bearer $TOKEN"

# System
curl localhost:8000/api/v1/system/info -H "Authorization: Bearer $TOKEN"
curl localhost:8000/api/v1/system/health
```

---

## Why async operations need polling

Many LXD actions (create instance, copy image, snapshot, migrate) are
**long-running**. LXD returns `202 Accepted` + an operation URL immediately and
runs the work in the background. Blocking the HTTP request until completion
would tie up a worker and risk client timeouts.

So this API **returns an operation reference** right away:

```json
{
  "operation_id": "abc-123",
  "operation_url": "/1.0/operations/abc-123",
  "poll_url": "/api/v1/operations/abc-123",
  "wait_url": "/api/v1/operations/abc-123/wait"
}
```

The client then does one of:
- **Poll** — `GET /api/v1/operations/{id}`
- **Long-poll** — `GET /api/v1/operations/{id}/wait?timeout=30`
- **Subscribe** — open `WS /api/v1/operations/ws` for real-time events

---

## Filtering, recursion & pagination

- `?expand=true` (default) → LXD `recursion=1` (full objects, not URLs).
- `?filter=status eq Running` → passed through to LXD's OData filter.
- `?limit=20&offset=40` → our own pagination applied on top of the LXD result.
- `?instance-type=container` (or `virtual-machine`) on instance list.
- `?project=staging` → scopes the request to a LXD project.

---

## Development

```bash
make install      # pip install -r requirements.txt + requirements-dev.txt
make migrate      # alembic upgrade head
make test         # pytest with coverage
make lint         # ruff check + black --check
make format       # black + ruff --fix
make typecheck    # mypy (non-blocking)
```

Tests use an in-memory SQLite DB and a mocked LXD client — **no real LXD
daemon is required**:

```bash
make test
```

---

## Project structure

```
app/
  main.py                      # FastAPI app, middleware, lifespan, error handler
  api/
    deps.py                    # JWT bearer, RBAC require_role, project param
    v1/
      api.py                   # v1_router aggregator
      routes/                  # auth, instances, snapshots, backups, storage,
                               # networks, projects, images, operations, system
  core/
    config.py                  # pydantic-settings
    security.py                # bcrypt + JWT + Role hierarchy
    limiter.py                 # slowapi instance
  schemas/                     # pydantic v2 request/response models
  services/
    lxd_client.py              # raw httpx wrapper of the LXD REST API
    lxd_operations.py          # async-op ref builder + wait helper
    exceptions.py              # LXDError hierarchy -> HTTP status
  db/                          # SQLAlchemy async models, session, crud, seed
  utils/                       # JSON logging, pagination
alembic/                       # migrations
tests/                         # pytest (mocked LXD)
Dockerfile, docker-compose.yml, Makefile, api.http
```

---

## API reference

Interactive docs are available at `/docs` (Swagger) and `/redoc` once running.
See `api.http` for a full request collection and `lxd-api.postman_collection.json`
for a Postman import.

---

## License

MIT
