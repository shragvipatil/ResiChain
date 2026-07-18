# Fixes Applied — ResiChain Simulation Engine

This document consolidates the fix history for `agents/simulation.py`,
`scripts/seed_knowledge_graph.py`, and Day 15 live-data verification,
covering the Day 12–15 compound-scenario and data-integrity verification
effort. Written to prevent re-deriving the same root causes across future
debugging sessions.

---

## 1. Suez Canal route missing Bab-el-Mandeb dependency

**Symptom:** During a compound Hormuz + Red Sea disruption event,
`get_surviving_routes()` incorrectly reported the "Russia to Vadinar via
Suez" route as surviving, when only the Cape route should survive.

**Root cause:** A ship sailing from Russia to the Suez Canal must also
transit Bab-el-Mandeb/Red Sea to reach it from the Arabian Sea side. The
route was only linked to `Suez Canal` via `PASSES_THROUGH`, not to
`Bab-el-Mandeb`, so a Red Sea-only disruption never blocked it.

**Fix:** Added a second `PASSES_THROUGH` edge from the route to
`Bab-el-Mandeb` in `seed_knowledge_graph.py::seed_relationships()`.

**Verification:** `verify_babelmandeb.py` confirms the edge persists across
a fresh reseed (`Found 1 matching relationship(s)`).

---

## 2. Refinery weight normalization overloaded modeled refineries

**Symptom:** Jamnagar's `util_delta_pct` came out to -57.2% for the demo
scenario, roughly 5-8x the spec's -7% to -11% target.

**Root cause:** `_compute_refinery_weights()` originally normalized
weights to sum to 1.0 across only the 4 refineries modeled in the graph
(combined capacity ~2.26 mbd). But `import_gap_mbd` is a NATIONAL figure,
computed against India's full daily consumption (~5.1 mbd). Forcing
weights to sum to 1.0 made these 4 refineries absorb the ENTIRE national
gap between themselves, overloading each one far past a realistic level.

**Fix:** Weights now sum to `(modeled capacity / national daily
consumption)` instead of 1.0, so the modeled refineries absorb only the
fraction of the national gap proportional to how much of the national
refining market they actually represent — distributed among themselves by
their own capacity share, as before.

**Note on the "Other India Refineries" aggregate node:** An aggregate
Neo4j node representing India's remaining ~2.9 mbd of unmodeled refining
capacity was added and later mistakenly deleted, then restored, during
this debugging arc. It is confirmed **inert** — `_compute_refinery_weights()`
only computes weights for names passed into `refinery_names` (default: the
4 named refineries), and the aggregate node is never included in that list.
Its presence or absence in the graph has zero effect on `util_delta_pct`.
It exists purely as documentation of national scope and is safe to ignore.

**Verification:** `test_spec_approx_7_to_11_pct_drop` in
`tests/test_simulation.py`.

---

## 3. `compatible_share` double-counted the national weight discount

**Symptom:** Even after fix #2, Jamnagar's compound-scenario
`util_delta_pct` still landed at -25.29%, well outside the -7% to -11%
target, and the discrepancy was byte-for-byte reproducible.

**Root cause:** `compatible_share` values seeded in
`seed_knowledge_graph.py` (Jamnagar 0.90, Vadinar 0.85, Kochi 0.65, Paradip
0.80) were calibrated *before* the Day 12 weight-normalization fix existed,
back when `compatible_share` was the only discount factor applied to the
national gap. Once `_compute_refinery_weights()` started applying its own
national-scope discount on top, the two compounded — the gap was being
discounted twice.

**Fix:** Halved the seeded `compatible_share` values (Jamnagar → 0.45,
Vadinar → 0.42, Kochi → 0.32, Paradip → 0.40) to cancel the double-count.

**Process gap identified:** This fix was initially described in chat but
not actually committed to the seed script executed by the FastAPI
container, causing two rounds of "identical numbers after the fix" reports.
Root cause was never a deeper formula issue — the seed script simply hadn't
been re-run against the live Neo4j instance. Resolved by running
`scripts/seed_knowledge_graph.py` directly and confirming via
`get_refinery_specs('Jamnagar RIL')` that `compatible_share: 0.45` was
actually persisted.

**Verification:** Live Neo4j query confirmed 0.45/0.42/0.32/0.40 seeded
correctly; `test_jamnagar_demo_scenario` and
`test_spec_approx_7_to_11_pct_drop` pass in `tests/test_simulation.py`.

---

## 4. Beta coefficient under-priced compound chokepoint closures

**Symptom:** `price_impact()`'s single fixed `beta=0.45` (calibrated from
the 2019 Abqaiq attack, where rerouting optionality remained intact)
mathematically capped compound-event price deltas near +$10.75, well below
the spec's +$14-15 target for a simultaneous Hormuz + Bab-el-Mandeb
closure.

**Root cause:** A single-chokepoint closure still leaves reroute
optionality (e.g. Cape route). A simultaneous closure of both major
corridors removes that optionality entirely — markets price the loss of
redundancy itself, not just the barrel count, which a single Abqaiq-era
beta structurally cannot capture.

**Fix:** Introduced `_BETA_SINGLE = 0.45` (unchanged, Abqaiq-calibrated)
and `_BETA_COMPOUND = 0.60` (loss-of-optionality calibrated) in
`agents/simulation.py`. `price_impact()` now auto-selects between them
based on `affected_chokepoint_count` (≥2 → compound) when `beta` is not
explicitly passed. An explicit `beta` argument always overrides
auto-selection. The value actually used is returned in `beta_used` for
demo transparency.

**Verification:** `TestPriceImpactBetaCompound` (5 tests) and
`test_run_all_compound_uses_beta_compound` in `tests/test_simulation.py` —
compound demo scenario (Kuwait + Russia via Hormuz + Bab-el-Mandeb) lands
at $13-15 price delta, matching the hand-calculated ~$14 target.

---

## 5. Gemini API returning 404 — model endpoint mismatch (Day 15)

**Symptom:** Since the June 27 API verification pass, Agent 2's Gemini
calls returned a 404 on every attempt, silently falling through the Fix 6
exponential-backoff chain to the spaCy `fallback_ner` extraction path.
`extraction_method` in every Agent 2 output read `fallback_ner`, not
`gemini_structured`, despite the API key being valid.

**Root cause:** Google AI Studio's key format changed to the `AQ.` prefix
standard, and the correct model identifier string had to be confirmed
against the new endpoint version rather than assumed from older
documentation. `GEMINI_MODEL` was also unset in the running container's
environment (read back as `None`) even after being added to `.env`, because
Docker Compose environment variables are injected at container creation
time and are not re-read from `.env` on a live, already-running container.

**Fix:** Confirmed `GEMINI_MODEL=gemini-2.5-flash` in `.env` matches the
CLAUDE.md spec exactly, then ran `docker compose restart backend` (not a
full rebuild) to force the container to re-read the updated `.env` file via
its `env_file` directive.

**Verification:** Live, non-mocked call through
`agents.agent2.extract_intelligence()` against a real test event now
returns `extraction_method: gemini_structured` with a valid populated JSON
extraction. Confirmed the module-level `agents.agent2.GEMINI_MODEL`
attribute matches the `.env` value exactly (`gemini-2.5-flash`), proving
the code reads from environment rather than a hardcoded fallback string —
required by the "no hardcoded config values" rule.

---

## 6. Iran supplier import share seeded as 100% instead of ~1.4% (Day 15)

**Symptom:** During the Day 15 live-data verification pass, summing
`get_supplier_current_share()` across all 8 modeled `Supplier` nodes
returned a total of 185% — a mathematically impossible result for a set of
import-share percentages that should sum to at most 100%.

**Root cause:** `Supplier.import_share_pct` for Iran was seeded into Neo4j
as the raw integer `1` (evidently intended to mean "1%"), but
`get_supplier_current_share()`'s normalization logic —
`share / 100.0 if share > 1.0 else share` — cannot distinguish "1 meaning
1%, needs dividing by 100" from "1.0 meaning already a fraction equal to
100%." The value `1` failed the `> 1.0` check and was passed straight
through as `1.0`, i.e., displayed and used internally as 100% for a single
sanctioned supplier that should represent a small residual/gray-market
share.

**Fix:** Corrected the seeded value directly in Neo4j —
`Supplier.import_share_pct` for Iran set to `1.4` (1.4%), a defensible
residual estimate consistent with Iran's sanctioned status. All other 7
suppliers' seeded values were spot-checked and confirmed already correct
(Saudi Arabia 18.2%, Iraq 22.1%, Russia 21.3%, UAE 8.4%, Kuwait 6.8%, USA
5.7%, Venezuela 2.5%).

**Verification:** Post-fix, the 8 modeled suppliers sum to 86.4%. The
remaining ~13.6% is intentionally unmodeled — the Knowledge Graph only
represents suppliers relevant to Hormuz/Red Sea disruption demo scenarios
(Gulf states, Russia, USA, Venezuela, Iran); minor global suppliers
(Nigeria, Angola, Mexico, Brazil, etc.) were never seeded, since Agent 7
and Agent 8 correctly exclude any supplier absent from
`get_supplier_route_chokepoints()` from disruption calculations by design
— this is documented directly in `agent8.py`'s
`build_supplier_route_risks()` docstring as a deliberate false-positive
guard, not a gap to fix.

---

## 7. Data source status clarification — live vs. documented constant (Day 15)

**Context:** Day 15 live-data verification initially treated EIA (India
consumption), PPAC (SPR volume), and UN Comtrade (import shares) as live
API reads to be verified against a running 30-minute polling window. This
was a mischaracterization of the actual architecture — all three are
intentional, documented constants, not live fetches, and treating them as
"live" during judge Q&A would be indefensible if directly checked.

**Clarification, confirmed against actual code and Neo4j:**

| Data point | Value | Status | Actual source |
|---|---|---|---|
| India daily consumption | 5.1 mbd | Documented constant | `INDIA_DAILY_CONSUMPTION_MBD` in `.env`, read via `_get_india_consumption()` in `agent5.py`. EIA's India consumption series (activityId=2/productId=54) returns empty/unpublished rows for India — confirmed via direct API inspection on 2026-07-08 — and is structurally annual/projection data, not a live daily feed, even when populated. |
| SPR total volume | 38 mb | Documented constant | Seeded into Neo4j `StorageFacility.capacity_mb` nodes at build time. No public PPAC API exists; PPAC data was sourced manually. Read via `get_spr_total_volume()` in `neo4j_queries.py`. |
| Supplier import shares | Sum 86.4% (8 modeled suppliers) | Documented constant | Seeded into Neo4j as a direct property, `Supplier.import_share_pct` — **not** a relationship property on `SUPPLIES` (that relationship is actually `Port`→`Refinery` in this schema, unrelated to supplier shares). Comtrade-derived reference data seeded at build time; the public UN Comtrade endpoint is confirmed non-functional. Read via `get_supplier_current_share()` in `neo4j_queries.py`. |

**Actually live, confirmed via the same Day 15 verification pass:**
yfinance (Brent ~$82, WTI), GDELT, UKMTO RSS, OFAC SDN (19,110 real
entries, fixed Day 13), Alpha Vantage, and Gemini (fixed same day, see
Fix 5 above). AISHub is wired end-to-end but falls back to 3 hardcoded
demo vessel positions, since the free tier requires a physical AIS
receiver.

**Why this matters:** The honest, defensible framing for judges is "live
where a real-time public source exists (6 of 9 data sources); documented
constants where no reliable live source exists (EIA series empty, no
public PPAC/Comtrade API), each with an inline code comment explaining the
specific verification and decision." This is a stronger and more credible
answer under scrutiny than claiming all sources are live.

**Verification:** Confirmed directly against running containers —
`get_spr_total_volume()` returns `38.0`, `_get_india_consumption()`
returns `5.1`, and `get_supplier_current_share()` summed across all 8
`Supplier` nodes returns `0.864` post-fix (see Fix 6 above).

---

## 8. Agent 7 Layer 1 OFAC/Russia false-positive block (Day 18)

**Symptom:** Agent 7's `layer1_sanctions` check flagged Russia as a
blocked candidate supplier even though Russia is not a comprehensively
sanctioned jurisdiction under the current OFAC SDN list — a false
positive that would have incorrectly removed a legitimate procurement
option from `ranked_options`.

**Root cause:** The sanctions-matching logic in `layer1_sanctions` was
over-matching on partial/country-level SDN entries rather than checking
for actual entity-level or sectoral designations relevant to the
candidate's specific route/counterparty.

**Fix:** Corrected the match condition in `agent7.py::layer1_sanctions` so
Russia routes are no longer auto-rejected outright; Russia now correctly
falls through to later layers (where it may still be flagged PARTIAL per
other constraints, but is not hard-blocked at Layer 1).

**Follow-on issue found:** The same edit introduced a `NameError` that
blocked deployment; this was caught and fixed immediately (see Fix 9).

**Verification:** Confirmed via direct Agent 7 run that Russia now passes
Layer 1 and appears in the candidate trace instead of being silently
dropped.

---

## 9. `agent7.py` NameError — deploy blocker (Day 18)

**Symptom:** Immediately after the Fix 8 edit, the FastAPI container
failed to start / import `agent7.py`, throwing a `NameError` and blocking
deployment entirely.

**Root cause:** A variable referenced in the corrected `layer1_sanctions`
logic was renamed/removed during the Fix 8 edit but a downstream reference
to the old name was left in place.

**Fix:** Corrected the stale variable reference in `agent7.py`. Container
rebuilt and confirmed to start cleanly.

**Verification:** `docker exec -it resichain_fastapi python -m pytest
tests/test_agent7_fix10_race_condition.py -v` — all 4 tests pass,
confirming the Fix 10 sequential-diversification logic (per-supplier
running-share isolation, fresh tracker per batch, confidence-sort ordering)
was not disturbed by the Fix 8/9 edits.

---

## 10. Landmine test script cleanup (Day 18)

**Symptom:** A temporary/exploratory test script used to isolate the Fix
8 root cause was left in the repo after the fix landed.

**Fix:** Removed the scratch script; no production code depended on it.

**Verification:** Confirmed `agent7.py` test suite still passes without it.

---

## 11. Regression check — simulation.py unaffected by Day 18 agent7.py edits

**Context:** Fixes 8–10 only touched `agent7.py`. As a safety check before
calling the session done, the full `test_simulation.py` suite was re-run
to confirm no cross-module regression, since `agent7.py`'s downstream
diversification logic consumes `simulation.py` outputs indirectly via the
candidate-ranking pipeline.

**Verification:** `docker exec -it resichain_fastapi python -m pytest
tests/test_simulation.py -v` — all 41 tests pass, including
`TestRunAll::test_runall_compound_uses_beta_compound`, confirming the
compound-scenario numbers (disrupted share ~28.1%, beta_compound 0.60,
Jamnagar utilization drop within -7% to -11%) are unchanged.

---

## Known issue — deferred, not fixed

**Agent 6 never generates a Russia procurement candidate.** Even after Fix
8 lets Russia pass Agent 7 Layer 1, Agent 6's `build_candidates()` never
produces a Russia option in `ranked_options` / `full_rejection_trace` in
observed runs. Suspected cause: `get_surviving_routes(blocked_chokepoints)`
may not be returning a Russia→[chokepoint]→India route at all, so Russia
never enters the `surviving_routes` loop. Needs checking whether Russia's
`SHIPS_VIA` relationship is seeded correctly in Neo4j and whether its
primary chokepoint (Bab-el-Mandeb/Red Sea) is being excluded incorrectly.
Logged verbally with the team (owner: Person B, `agents/agent6.py` /
`db/neo4j_queries.py`); priority Low, deferred until post-deploy since it
is not demo-blocking.

---

## Test suite status

As of 2026-07-18 (Day 18):

- `tests/test_agent7_fix10_race_condition.py`: 4/4 passing (re-verified
  post Fix 8/9).
- `tests/test_simulation.py`: 41/41 passing (re-verified post Fix 8/9,
  no regression).
- Combined with the Day 14 baseline (113/113 across `test_agent2.py`,
  `test_agent5.py`, `test_agent7.py`, `test_simulation.py`), total known
  passing test count remains fully green with no regressions introduced
  by the Day 18 `agent7.py` OFAC/Russia fix.

```
docker compose exec fastapi python -m pytest tests/test_simulation.py tests/test_agent2.py tests/test_agent5.py tests/test_agent7.py -v
```

## Key lessons for future fixes

1. Multiple rounds on Day 12–13 were spent re-diagnosing symptoms that
   were actually the *same* root cause (seed script edits described in
   chat but never confirmed as committed/executed against the live
   database). Before declaring a fix "applied," always verify directly
   against the live instance the consuming service reads from — e.g.
   `get_refinery_specs('Jamnagar RIL')` — rather than trusting that a
   pasted code block was saved and re-run.

2. On Day 15, a `.env` change did not take effect until the container was
   explicitly restarted (`docker compose restart backend`) — environment
   variables are injected at container start time from the `env_file`
   directive, not re-read live from disk. A full rebuild is only needed
   when a Dockerfile or dependency changes, not for `.env`-only edits.

3. Silent unit-normalization ambiguity (is this value a fraction or a
   percentage?) is a recurring risk across this codebase. The Iran bug
   (Fix 6) exists because a value of exactly `1` is valid under either
   interpretation. Any function that normalizes based on a magnitude
   threshold (`> 1.0`) should be paired with a seed-time validation step
   that flags any single supplier share above a sane ceiling (e.g. 40%,
   matching `MAX_SUPPLIER_SHARE_PCT`) for manual review before it reaches
   production.

4. On Day 18, a one-line logic fix (Fix 8) introduced a stale variable
   reference (Fix 9) that blocked deployment entirely. Any edit to a
   validation layer that renames or removes a variable should be followed
   immediately by a full module import/smoke test, not just a targeted
   unit test of the intended behavior change, before declaring the fix
   safe to deploy.
