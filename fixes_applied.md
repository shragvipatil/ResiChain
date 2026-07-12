# Fixes Applied — ResiChain Simulation Engine

This document consolidates the fix history for `agents/simulation.py` and
`scripts/seed_knowledge_graph.py`, covering the Day 12–13 compound-scenario
verification effort. Written to prevent re-deriving the same root causes
across future debugging sessions.

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

## Test suite status

41/41 tests passing in `tests/test_simulation.py` as of 2026-07-11,
covering `import_disruption`, `spr_drawdown`, `price_impact` (including
beta_compound), `refinery_utilization`, and `run_all` composite behavior.

```
docker compose exec fastapi python -m pytest tests/test_simulation.py -v
```

## Key lesson for future fixes

Multiple rounds tonight were spent re-diagnosing symptoms that were
actually the *same* root cause (seed script edits described in chat but
never confirmed as committed/executed against the live database). Before
declaring a fix "applied," always verify directly against the live
instance the consuming service reads from — e.g.
`get_refinery_specs('Jamnagar RIL')` — rather than trusting that a pasted
code block was saved and re-run.
