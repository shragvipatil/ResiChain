"""
backend/scripts/measure_latency.py

Day 19, Person A — performance testing across 10 trials.

Extends the Day-14 version: adds standard deviation per node (to spot
high-variance agents — usually Gemini network latency or Neo4j query
plan changes) and the actual Day-19 milestone check: signal-to-playbook
under 3 minutes in at least 9 of 10 runs (not just "average is fine").

Groups agent_runs rows into individual TRIALS (not just per-node
buckets) using the fact that "agent4" always starts a new trial in
PIPELINE_NODES' execution order — this lets us compute each trial's
real wall-clock signal-to-playbook time (last node's ended_at minus
first node's started_at), which is what the 9-of-10 check needs.

Usage (after triggering the crisis graph 10 times):
    docker-compose exec fastapi python scripts/measure_latency.py --trials 10
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.postgres_queries import get_connection

PIPELINE_NODES = ["agent4", "agent5_first", "agent6", "agent5_second", "agent8"]
TARGET_SECONDS = 180  # 3-minute milestone
MIN_PASS_FRACTION = 0.9  # at least 9 of 10 runs


def fetch_rows(trials: int):
    """Pull enough recent rows to reconstruct `trials` full runs."""
    limit = trials * len(PIPELINE_NODES) * 3  # headroom for partial/failed runs
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT node_name, duration_ms, started_at, ended_at
                FROM agent_runs
                ORDER BY started_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()


def group_into_trials(rows) -> list[dict]:
    """
    Groups rows (already sorted by started_at ASC) into trials. A new
    trial starts every time we see "agent4" — it's always the first
    node in PIPELINE_NODES' execution order, so it's a reliable trial
    boundary without needing a dedicated run_id column.
    """
    trials = []
    current = None
    for row in rows:
        if row["node_name"] == "agent4":
            if current:
                trials.append(current)
            current = {"nodes": {}}
        if current is None:
            continue  # rows before the first agent4 — ignore
        current["nodes"][row["node_name"]] = row
    if current:
        trials.append(current)
    return trials


def analyze(trials: list[dict], requested: int):
    # Keep only the most recent `requested` COMPLETE trials (all 5 nodes present).
    complete = [t for t in trials if all(n in t["nodes"] for n in PIPELINE_NODES)]
    complete = complete[-requested:]

    print(f"\n{'='*66}")
    print(f"  DAY 19 — PIPELINE PERFORMANCE ({len(complete)} of {requested} "
          f"requested trials complete)")
    print(f"{'='*66}")

    if not complete:
        print("  No complete trials found. Trigger the crisis graph and retry.")
        return 1

    # ---- Per-node avg / stddev / variance flag ----
    print(f"\n  {'node':<16}{'avg ms':<10}{'stdev':<10}{'min':<8}{'max':<8}{'note'}")
    print(f"  {'-'*60}")
    per_node_durations = {node: [] for node in PIPELINE_NODES}
    for t in complete:
        for node in PIPELINE_NODES:
            per_node_durations[node].append(t["nodes"][node]["duration_ms"])

    high_variance_nodes = []
    for node in PIPELINE_NODES:
        vals = per_node_durations[node]
        avg = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        # Flag high variance: stdev > 40% of the mean (rule of thumb for
        # "sometimes fast, sometimes slow" rather than genuine noise).
        flag = ""
        if avg > 0 and sd / avg > 0.4:
            flag = "HIGH VARIANCE"
            high_variance_nodes.append(node)
        print(f"  {node:<16}{avg:<10.1f}{sd:<10.1f}{min(vals):<8}{max(vals):<8}{flag}")

    # ---- Per-trial real wall-clock signal-to-playbook ----
    print(f"\n  {'-'*60}")
    print(f"  {'trial':<8}{'signal_to_playbook_s':<24}{'status'}")
    pass_count = 0
    trial_seconds = []
    for i, t in enumerate(complete, 1):
        first_start = t["nodes"]["agent4"]["started_at"]
        last_end = t["nodes"]["agent8"]["ended_at"]
        wall_s = (last_end - first_start).total_seconds()
        trial_seconds.append(wall_s)
        ok = wall_s < TARGET_SECONDS
        pass_count += 1 if ok else 0
        print(f"  {i:<8}{wall_s:<24.3f}{'OK' if ok else 'OVER TARGET'}")

    print(f"  {'-'*60}")
    avg_total = statistics.mean(trial_seconds)
    sd_total = statistics.stdev(trial_seconds) if len(trial_seconds) > 1 else 0.0
    print(f"  avg signal-to-playbook: {avg_total:.3f}s  (stdev {sd_total:.3f}s)")

    fraction_passing = pass_count / len(complete)
    print(f"\n  Milestone: signal-to-playbook < {TARGET_SECONDS}s in >= "
          f"{MIN_PASS_FRACTION*100:.0f}% of runs")
    print(f"  RESULT: {pass_count}/{len(complete)} runs under target "
          f"({fraction_passing*100:.0f}%)")

    if high_variance_nodes:
        print(f"\n  HIGH-VARIANCE NODE(S): {', '.join(high_variance_nodes)}")
        print("  Usual culprits: Gemini API network latency (agent5 calls out"
              " to an LLM), or a Neo4j query plan cache miss (agent6's graph"
              " queries). Not necessarily a bug — informational for the report.")

    overall_ok = fraction_passing >= MIN_PASS_FRACTION
    print(f"\n  OVERALL: {'PASS' if overall_ok else 'FAIL'}")
    return 0 if overall_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=10)
    args = parser.parse_args()

    rows = fetch_rows(args.trials)
    trials = group_into_trials(rows)
    return analyze(trials, args.trials)


if __name__ == "__main__":
    sys.exit(main()) 