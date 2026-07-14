"""
backend/scripts/measure_latency.py

Day 14, Task 1 — pipeline latency measurement.

Reads the timing rows that crisis_graph.py's _timed() wrapper already
persists to the Postgres `agent_runs` table (node_name, started_at,
ended_at, duration_ms), and reports per-node averages plus total
end-to-end pipeline time across the most recent N trials.

Usage (after triggering the crisis graph 5 times):
    docker-compose exec fastapi python scripts/measure_latency.py
    docker-compose exec fastapi python scripts/measure_latency.py --trials 5

This does NOT trigger the graph itself — trigger it 5 times first
(see the PowerShell loop in the Day-14 runbook), then run this to
crunch the numbers. Keeping measurement separate from triggering means
you can re-analyze without re-running, and the numbers come straight
from the same instrumentation the app uses in production.

Milestone check: total pipeline time must be < 180 s (3 minutes).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.postgres_queries import get_connection

# The 5 crisis-graph nodes, in pipeline execution order, as written by
# crisis_graph.py's workflow.add_node("<name>", _timed("<name>", ...)) calls.
PIPELINE_NODES = ["agent4", "agent5_first", "agent6", "agent5_second", "agent8"]

TARGET_SECONDS = 180  # 3-minute milestone


def fetch_recent_runs(trials: int) -> dict[str, list[int]]:
    """
    Pull the most recent (trials x len(PIPELINE_NODES)) rows from
    agent_runs and bucket duration_ms by node_name.

    We over-fetch a little and then take the last `trials` samples per
    node, so a partial/failed run in the middle doesn't skew the window.
    """
    limit = trials * len(PIPELINE_NODES) * 2  # headroom for partial runs

    per_node: dict[str, list[int]] = {node: [] for node in PIPELINE_NODES}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT node_name, duration_ms, started_at
                FROM agent_runs
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    # rows are newest-first; collect up to `trials` per node
    for row in rows:
        node = row["node_name"]
        if node in per_node and len(per_node[node]) < trials:
            per_node[node].append(row["duration_ms"])

    return per_node


def report(per_node: dict[str, list[int]], trials: int) -> int:
    print(f"\n{'='*60}")
    print(f"  PIPELINE LATENCY — last {trials} trials per node")
    print(f"{'='*60}")
    print(f"  {'node':<16}{'samples':<9}{'avg ms':<10}{'min':<8}{'max':<8}")
    print(f"  {'-'*54}")

    total_avg_ms = 0.0
    incomplete = False

    for node in PIPELINE_NODES:
        samples = per_node.get(node, [])
        if not samples:
            print(f"  {node:<16}{'0':<9}{'NO DATA':<10}")
            incomplete = True
            continue
        avg = sum(samples) / len(samples)
        total_avg_ms += avg
        print(
            f"  {node:<16}{len(samples):<9}{avg:<10.1f}"
            f"{min(samples):<8}{max(samples):<8}"
        )

    print(f"  {'-'*54}")
    total_s = total_avg_ms / 1000.0
    print(f"  {'TOTAL (avg)':<16}{'':<9}{total_avg_ms:<10.1f}({total_s:.3f} s)")
    print(f"{'='*60}")

    if incomplete:
        print("\n  WARNING: some nodes have no timing data. Did the crisis")
        print("  graph actually run? Trigger it, then re-run this script.")
        return 1

    print(f"\n  Milestone target: < {TARGET_SECONDS} s (3 minutes)")
    if total_s < TARGET_SECONDS:
        margin = TARGET_SECONDS / total_s if total_s > 0 else float("inf")
        print(f"  RESULT: PASS — {total_s:.3f} s, ~{margin:.0f}x under budget.")
        # Point at the slowest node factually, without prescribing a fix.
        slowest = max(
            PIPELINE_NODES,
            key=lambda n: (sum(per_node[n]) / len(per_node[n])) if per_node[n] else 0,
        )
        slow_avg = sum(per_node[slowest]) / len(per_node[slowest])
        print(f"  Slowest node: {slowest} ({slow_avg:.0f} ms avg) — "
              f"informational only; no optimization needed under budget.")
        return 0

    print(f"  RESULT: FAIL — {total_s:.3f} s exceeds the {TARGET_SECONDS}s target.")
    print("  Investigate the slowest node above before optimizing anything.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure crisis-pipeline latency.")
    parser.add_argument("--trials", type=int, default=5,
                        help="How many recent trials to average (default 5).")
    args = parser.parse_args()

    per_node = fetch_recent_runs(args.trials)
    return report(per_node, args.trials)


if __name__ == "__main__":
    sys.exit(main()) 