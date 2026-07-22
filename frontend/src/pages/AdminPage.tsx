/**
 * AdminPage.tsx — Day 12 deliverable (Person C)
 *
 * System health dashboard — LIVE data only:
 *   - All 8 agent statuses       -> real GET /api/agents/status (AppContext)
 *   - Redis stream queue depths  -> same real endpoint
 *   - Crisis mode badge          -> same real endpoint (riskState.system_mode)
 *
 * Day 20 fix: previously showed 2 additional mock-only panels
 * (PostgreSQL connection pool, external API health) behind a visible
 * "MOCK" badge. Removed rather than displayed: this codebase's Postgres
 * layer opens a fresh connection per query with no pool object to
 * introspect (confirmed in postgres_queries.py), and no client
 * (GDELT/UKMTO/OFAC/etc.) persists a "last successful call" anywhere
 * queryable — building either for real means new architecture or new
 * instrumentation, not a quick fix. A smaller page that's 100% real
 * beats a fuller one with fabricated numbers, even labeled.
 */

import React, { useEffect } from "react";
import AppLayout from "../components/AppLayout";
import { getAgentStatus } from "../api/endpoints";
import { useAppContext } from "../context/AppContext";
import { AgentInfo } from "../types";

const AGENT_LABELS: Record<string, string> = {
  agent1: "Agent 1 — Ingestion & Verification",
  agent2: "Agent 2 — RAG Event Classifier",
  agent3: "Agent 3 — Corridor Risk Engine",
  agent4: "Agent 4 — Compound Disruption Detector",
  agent5: "Agent 5 — SPR LP Optimiser",
  agent6: "Agent 6 — Procurement Orchestrator",
  agent7: "Agent 7 — Constraint Validator",
  agent8: "Agent 8 — Playbook Generator",
};

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function statusDot(status: string) {
  if (status === "RUNNING") return "bg-status-live animate-pulse";
  if (status === "IDLE") return "bg-status-normal";
  if (status === "ERROR") return "bg-status-critical animate-pulse";
  return "bg-slate-600";
}

// ── Panel: Agent statuses (LIVE) ───────────────────────────────────────────────

const AgentGrid: React.FC<{ agents: Record<string, AgentInfo> }> = ({ agents }) => (
  <div className="bg-chart-panel border border-chart-hairline rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-chart-hairline">
      <h2 className="text-white text-sm font-medium">Agent Pipeline Status</h2>
      <p className="text-slate-500 text-xs mt-0.5">All 8 agents</p>
    </div>
    <div className="p-5 grid grid-cols-2 gap-3">
      {Object.entries(AGENT_LABELS).map(([id, label]) => {
        const info = agents[id] ?? { status: "INACTIVE" as const, last_run: undefined };
        return (
          <div key={id} className="flex items-center justify-between bg-chart-navy rounded-lg px-3 py-2.5 border border-chart-hairline/50">
            <div className="flex items-center gap-2 min-w-0">
              <div className={`w-2 h-2 rounded-full shrink-0 ${statusDot(info.status)}`} />
              <span className="text-slate-300 text-xs truncate">{label}</span>
            </div>
            <span className="text-slate-600 text-xs shrink-0 ml-2">{relativeTime(info.last_run)}</span>
          </div>
        );
      })}
    </div>
  </div>
);

// ── Panel: Redis stream depths (LIVE) ──────────────────────────────────────────

const RedisHealthPanel: React.FC<{ redisDepths: Record<string, number> }> = ({ redisDepths }) => (
  <div className="bg-chart-panel border border-chart-hairline rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-chart-hairline">
      <h2 className="text-white text-sm font-medium">Redis Stream Queue Depth</h2>
      <p className="text-slate-500 text-xs mt-0.5">events:raw · events:verified</p>
    </div>
    <div className="p-5">
      <div className="grid grid-cols-2 gap-3">
        {Object.entries(redisDepths).map(([stream, depth]) => (
          <div key={stream} className="bg-chart-navy rounded-lg px-3 py-2.5 border border-chart-hairline/50">
            <p className="text-slate-500 text-xs font-mono">{stream}</p>
            <p className={`text-lg font-medium tabular-nums ${depth > 10 ? "text-status-caution" : "text-white"}`}>
              {depth}
            </p>
          </div>
        ))}
      </div>
    </div>
  </div>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const AdminPage: React.FC = () => {
  const { agentStatus, riskState, setAgentStatus } = useAppContext();

  useEffect(() => {
    getAgentStatus().then(setAgentStatus).catch(() => {
      // Non-fatal — panel falls back to "INACTIVE"/"never" per agent
    });
  }, [setAgentStatus]);

  if (!agentStatus) {
    return (
      <AppLayout showRiskStrip={false}>
        <div className="space-y-4">
          {[1, 2].map((i) => (
            <div key={i} className="h-40 bg-chart-panel rounded-xl border border-chart-hairline animate-pulse" />
          ))}
        </div>
      </AppLayout>
    );
  }

  const crisisActive = riskState?.system_mode === "CRISIS";

  return (
    <AppLayout showRiskStrip={false}>
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-serif font-medium text-white">System Health</h1>
          <p className="text-slate-400 text-sm mt-1">Admin — live pipeline and infrastructure status</p>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-lg border font-medium ${
          crisisActive
            ? "bg-status-critical/20 text-status-critical border-status-critical/40 animate-pulse"
            : "bg-status-normal/20 text-status-normal border-status-normal/40"
        }`}>
          {crisisActive ? "CRISIS MODE ACTIVE" : "NOMINAL"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <AgentGrid agents={agentStatus.agents} />
        <RedisHealthPanel redisDepths={agentStatus.redis_stream_depths} />
      </div>
    </AppLayout>
  );
};

export default AdminPage;