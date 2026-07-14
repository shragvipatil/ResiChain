# ResiChain — Setup Guide

**ResiChain** is a multi-agent energy supply-chain resilience system. It monitors
global oil-shipping corridors (Hormuz, Red Sea, Suez, Cape) for disruption risk
and auto-generates procurement / strategic-reserve playbooks during a crisis.

This document reproduces the full running system from scratch. It assumes only
**Docker Desktop** (with Docker Compose v2) and **git** installed.

---

## 1. Clone

```bash
git clone <REPO_URL> ResiChain
cd ResiChain
```

## 2. Configure environment

Copy the example environment file and fill in the required values:

```bash
cp .env.example .env      # then edit .env
```

**Minimum required to run** (the system starts and the demo works with these):

| Variable | What it is | Example |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Postgres credentials | `resichain` / `resichain` / `resichain` |
| `DATABASE_URL` | Must match the three above | `postgresql://resichain:resichain@postgres:5432/resichain` |
| `NEO4J_AUTH` | Neo4j login, `user/password` | `neo4j/resichain123` |
| `REDIS_URL` | Redis connection | `redis://redis:6379/0` |
| `CHROMA_HOST` / `CHROMA_PORT` | ChromaDB address | `chromadb` / `8000` |
| `GEMINI_API_KEY` | Google Gemini key (Agent 2 intel extraction) | *(your key)* |

**Optional** (system runs without them — falls back to documented constants /
demo data): `EIA_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `AISHUB_USERNAME`,
`SMTP_*` (email alerts), `INDIA_DAILY_CONSUMPTION_MBD` (defaults to 5.1),
`SPR_TOTAL_MB` (defaults to 38).

> Hostnames in `DATABASE_URL`, `REDIS_URL`, etc. use the **service names**
> (`postgres`, `redis`, `neo4j`, `chromadb`) — not `localhost` — because the
> backend reaches them over Docker's internal network.

## 3. Build and start

```bash
docker-compose up -d --build
```

First build takes a few minutes (Python deps + spaCy model). Watch until all
five containers report healthy:

```bash
docker-compose ps
```

Expect `postgres`, `redis`, `neo4j`, `chromadb` = **healthy** and `fastapi` = **Up**.
Confirm the API is live:

```bash
curl http://localhost:8000/health          # -> {"status":"healthy",...}
```

## 4. Seed the databases

Run once after the first clean start (populates Neo4j graph + ChromaDB vectors):

```bash
docker-compose exec fastapi python scripts/seed_knowledge_graph.py
docker-compose exec fastapi python scripts/seed_chroma.py
```

## 5. Pre-cache external data (run ~30 min before a demo)

Caches vessel positions, Brent/WTI prices, and the OFAC sanctions list so the
demo is immune to venue Wi-Fi or external-API failures:

```bash
docker-compose exec fastapi python scripts/pre_cache_demo_data.py
```

Expect three `OK` lines (`vessels:demo_cache`, `prices:demo_cache`,
`OFAC snapshot + Postgres`).

## 6. Seed demo state (run ~2 min before a demo)

Sets the Section-12 pre-demo baseline (corridor risks, vessels, agent
heartbeats, one historical resolved alert) and freezes automatic risk
recomputation for 30 minutes so the demo starts from a known state:

```bash
docker-compose exec fastapi python scripts/seed_demo_state.py
```

Expect `DEMO STATE READY`.

---

## 7. Access the system

| Surface | URL |
|---|---|
| Backend API + docs | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| Ministry dashboard | http://localhost:3000/ministry |
| Procurement dashboard | http://localhost:3001/procurement |
| Refinery dashboard | http://localhost:3002/refinery |
| Viewer (read-only) | http://localhost:3003/viewer |
| Admin (system health) | http://localhost:3004/admin |

Only port **8000** (backend) and the frontend ports are published. The
databases (Postgres, Redis, Neo4j, ChromaDB) are reachable only from the host
loopback or internally between containers — Neo4j is not exposed at all.

## 8. Trigger a crisis (demo / evaluation)

With demo state seeded (step 6), inject a compound disruption and run the
crisis pipeline:

```bash
# Set compound risk (Hormuz + Red Sea both critical)
docker-compose exec redis redis-cli SET risk:state \
  '{"Hormuz":0.82,"Red_Sea":0.87,"Suez":0.18,"Cape":0.05}'

# Run the crisis graph
curl -X POST http://localhost:8000/api/crisis/trigger
```

The response includes `compound_risk` (~0.977), the blocked chokepoints, the
surviving Cape routes, and a generated playbook with a `signal_to_playbook_seconds`
value (target: under 180 s).

---

## Troubleshooting

- **A container isn't healthy** — `docker-compose logs <service>` (e.g.
  `docker-compose logs fastapi`).
- **`No module named 'db'` running a script** — rebuild so the Dockerfile's
  `PYTHONPATH=/app` is applied: `docker-compose up -d --build`.
- **Dashboards show stale numbers** — re-run step 6; the risk freeze expires
  after 30 minutes.
- **Full reset** — `docker-compose down -v` wipes all data; then repeat from
  step 3. (This deletes the seeded graph/vectors — re-run steps 4–6.) 