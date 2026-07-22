# ResiChain

**AI-Driven Energy Supply Chain Resilience for Import-Dependent Economies**
*ET AI Hackathon 2.0 — Problem Statement 2*

A multi-agent AI system that monitors global oil-shipping chokepoints (Strait of Hormuz, Bab-el-Mandeb/Red Sea, Suez Canal, Cape of Good Hope) in real time, detects compound geopolitical disruptions, and autonomously generates procurement and strategic-reserve response playbooks — from signal to actionable playbook in under one second.

---

## Quick Start

```bash
git clone https://github.com/shragvipatil/ResiChain.git
cd ResiChain
cp .env.example .env
# edit .env with real values (see SETUP.md for details)
docker-compose up -d --build
```

Wait for all containers healthy, then seed and pre-cache:

```bash
docker-compose exec fastapi python scripts/seed_knowledge_graph.py
docker-compose exec fastapi python scripts/seed_chroma.py
docker-compose exec fastapi python scripts/pre_cache_demo_data.py
docker-compose exec fastapi python scripts/seed_demo_state.py
```

Full instructions: see [`SETUP.md`](./SETUP.md).

---

## Architecture

Eight specialized agents orchestrated as a LangGraph state machine:

| # | Agent | Role |
|---|-------|------|
| 1 | Ingestion & Verification | Polls GDELT/UKMTO/Alpha Vantage every 5 min; two-stage WATCH (0.45) → CONFIRMED (0.65) state machine |
| 2 | RAG Event Classifier | Gemini-powered extraction, ChromaDB grounding, spaCy fallback on rate-limit |
| 3 | Corridor Risk Engine | 5 weighted factors, recomputed every 60s, capped at 1.0 |
| 4 | Compound Disruption Detector | `compound_risk = 1 − Π(1 − riskᵢ)` across corridors ≥ 0.65 |
| 5 | SPR LP Optimizer | Linear-programming reserve drawdown; runs twice (estimate, then corrected) |
| 6 | Procurement Orchestrator | Builds and scores alternate-supplier candidates from the Neo4j knowledge graph |
| 7 | Constraint Validator | 4-layer validation: sanctions, grade compatibility, diversification cap, operational |
| 8 | Playbook Generator | Ministry / Procurement / Refinery views, PDF export |

**Stack:** FastAPI, LangGraph (Postgres-checkpointed), PostgreSQL, Redis Streams, Neo4j, ChromaDB, Google Gemini 2.5 Flash + spaCy fallback, React + D3.js, JWT auth with TOTP 2FA and server-side revocation, Docker Compose.

---

## Data Sources

| Source | Status |
|---|---|
| GDELT, UKMTO, yfinance, OFAC SDN (19k+ entities), Alpha Vantage, AISHub | Live |
| India daily consumption, SPR volume, import-share % | Documented constants (no reliable live public API exists at required granularity — disclosed in code, not faked as live) |

---

## Reliability

Verified, not just demoed:

- Compound-risk formula matches spec exactly (0.9766 for Hormuz 0.82 + Red Sea 0.87)
- Redis Streams: 0 events lost across a simulated 30s consumer outage
- LangGraph checkpoint-based crash recovery (mid-crisis crash resumes, doesn't restart)
- JWT logout genuinely revokes server-side (blacklist + TTL)
- TOTP 2FA gate is role-casing-safe (fixed a fail-open bypass)
- Graceful degradation verified for 6 external failure modes (feed outage, empty AIS region, LLM rate-limit, sanctions-download failure, price-feed timeout, cache reconnect)
- Signal-to-playbook: 10-trial average 0.867s (target: <180s)
- Clean-environment cold boot: ~21s (target: <5 min), re-verified via fresh-clone smoke test

See [`docs/fixes_applied.md`](./docs/fixes_applied.md) for the full bug/fix log.

---

## Demo Scenarios

1. **Single-corridor partial disruption** — WATCH → CONFIRMED → sub-crisis playbook
2. **Compound disruption** (Hormuz + Red Sea) — full compound detection, Cape-route diversion, real approved suppliers
3. **Edge case** (all corridors blocked) — graceful SPR-only emergency drawdown, no crash

---

## Repository Structure

```
backend/
  agents/          # 8 agents + crisis_graph.py (LangGraph orchestration)
  db/              # Postgres, Redis, Neo4j query layers
  routers/         # FastAPI routes (auth, api, pdf)
  scripts/         # Seeding, pre-cache, and test scripts
  tests/           # Unit + integration test suites
frontend/          # React dashboards (Ministry, Procurement, Refinery, Admin, Viewer)
docs/              # fixes_applied.md, architecture notes
SETUP.md           # Full setup instructions
docker-compose.yml
```

---

## Team

- **OJASVITA SHARMA** — Backend infrastructure, agents 1/3/4/6, Docker, testing
- **SHRAGVI PATIL** — Neo4j/Postgres, agents 2/5/7/8, simulation formulas
- **SHRUTI SANDEEP GURAV** — Frontend, dashboards, demo flow
