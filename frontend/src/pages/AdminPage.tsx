/**
 * AdminPage.tsx — Day 12 deliverable (Person C)
 *
 * System health dashboard:
 *   - All 8 agent statuses            -> LIVE  (real GET /api/agents/status,
 *                                                 shared via AppContext)
 *   - Redis stream queue depths       -> LIVE  (same real endpoint)
 *   - Crisis mode badge               -> LIVE  (same real endpoint)
 *   - PostgreSQL connection pool      -> MOCK  (no live endpoint exists yet)
 *   - External API health             -> MOCK  (no live endpoint exists yet)
 *
 * Day 20 fix: this page previously called getSystemHealth() — a fully
 * hardcoded mock (ADMIN_USE_MOCK=true) — for EVERYTHING, even though the
 * Ministry dashboard already proves GET /api/agents/status returns real,
 * live agent statuses and Redis stream depths. Wiring those two sections
 * to that same real data removes half this page's mock surface with zero
 * backend changes. The remaining two panels (Postgres pool, external API
 * health) have no live backend source at all right now — they stay on
 * mock data honestly, with a visible MOCK badge, rather than silently.
 *
 * Note: this page fetches getAgentStatus() itself on mount, same as
 * AgentStatusPanel.tsx on the Ministry page does — AppContext only ever
 * MERGES WebSocket updates into agentStatus, it never seeds the initial
 * value. Without this page performing its own seed fetch, a judge landing
 * directly on /admin (its own role's default route) without visiting
 * /ministry first would see agentStatus stuck at null forever.
 */

import React, { useEffect, useState } from "react";
import AppLayout from "../components/AppLayout";
import { getSystemHealth, getAgentStatus } from "../api/endpoints";
import { useAppContext } from "../context/AppContext";
import { SystemHealth, AgentInfo } from "../types";

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
  if (status === "RUNNING") return "bg-blue-400 animate-pulse";
  if (status === "IDLE") return "bg-green-400";
  if (status === "ERROR") return "bg-red-400 animate-pulse";
  return "bg-slate-600";
}

function apiStatusStyle(status: string) {
  switch (status) {
    case "healthy":  return { dot: "bg-green-400",  text: "text-green-400",  badge: "bg-green-900/50 border-green-800" };
    case "degraded": return { dot: "bg-amber-400",  text: "text-amber-400",  badge: "bg-amber-900/50 border-amber-800" };
    default:         return { dot: "bg-red-400",    text: "text-red-400",    badge: "bg-red-900/50 border-red-800" };
  }
}

// ── Panel: Agent statuses (LIVE — real GET /api/agents/status) ───────────────

const AgentGrid: React.FC<{ agents: Record<string, AgentInfo> }> = ({ agents }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700 flex items-center justify-between">
      <div>
        <h2 className="text-white text-sm font-medium">Agent Pipeline Status</h2>
        <p className="text-slate-500 text-xs mt-0.5">All 8 agents</p>
      </div>
      <span className="text-xs px-2 py-0.5 rounded bg-green-900/50 text-green-400 border border-green-800 font-medium">
        LIVE
      </span>
    </div>
    <div className="p-5 grid grid-cols-2 gap-3">
      {Object.entries(AGENT_LABELS).map(([id, label]) => {
        const info = agents[id] ?? { status: "INACTIVE" as const, last_run: undefined };
        return (
          <div key={id} className="flex items-center justify-between bg-slate-900 rounded-lg px-3 py-2.5 border border-slate-700/50">
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

// ── Panel: Redis (LIVE) + Postgres (still mock) ───────────────────────────────

const InfraHealthPanel: React.FC<{
  redisDepths: Record<string, number>;
  postgresPool: SystemHealth["postgres_pool"];
}> = ({ redisDepths, postgresPool }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Infrastructure</h2>
      <p className="text-slate-500 text-xs mt-0.5">Redis streams · PostgreSQL pool</p>
    </div>
    <div className="p-5 space-y-4">
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-slate-500 text-xs">Redis Stream Queue Depth</p>
          <span className="text-xs px-1.5 py-0.5 rounded bg-green-900/50 text-green-400 border border-green-800 font-medium">
            LIVE
          </span>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {Object.entries(redisDepths).map(([stream, depth]) => (
            <div key={stream} className="bg-slate-900 rounded-lg px-3 py-2.5 border border-slate-700/50">
              <p className="text-slate-500 text-xs font-mono">{stream}</p>
              <p className={`text-lg font-medium tabular-nums ${depth > 10 ? "text-amber-400" : "text-white"}`}>
                {depth}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-slate-500 text-xs">PostgreSQL Connection Pool</p>
          <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-400 border border-slate-600 font-medium">
            MOCK
          </span>
        </div>
        <div className="bg-slate-900 rounded-lg px-3 py-3 border border-slate-700/50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-slate-300 text-xs">
              {postgresPool.active_connections} / {postgresPool.max_connections} connections
            </span>
            <span className={`text-xs font-medium ${postgresPool.status === "healthy" ? "text-green-400" : "text-amber-400"}`}>
              {postgresPool.status.toUpperCase()}
            </span>
          </div>
          <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full"
              style={{ width: `${(postgresPool.active_connections / postgresPool.max_connections) * 100}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  </div>
);

// ── Panel: External API health (still mock — no live source exists yet) ──────

const ExternalApiPanel: React.FC<{ apis: SystemHealth["external_apis"] }> = ({ apis }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700 flex items-center justify-between">
      <div>
        <h2 className="text-white text-sm font-medium">External API Health</h2>
        <p className="text-slate-500 text-xs mt-0.5">Last successful call per data source</p>
      </div>
      <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-400 border border-slate-600 font-medium">
        MOCK
      </span>
    </div>
    <div className="p-5 space-y-2">
      {apis.map((api) => {
        const s = apiStatusStyle(api.status);
        return (
          <div key={api.name} className="flex items-center justify-between bg-slate-900 rounded-lg px-3 py-2.5 border border-slate-700/50">
            <div className="flex items-center gap-2">
              <div className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
              <span className="text-slate-300 text-xs">{api.name}</span>
            </div>
            <div className="flex items-center gap-3">
              {api.latency_ms != null && (
                <span className="text-slate-600 text-xs tabular-nums">{api.latency_ms}ms</span>
              )}
              <span className="text-slate-500 text-xs">{relativeTime(api.last_success_at)}</span>
              <span className={`text-xs px-2 py-0.5 rounded border font-medium ${s.badge} ${s.text}`}>
                {api.status.toUpperCase()}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  </div>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const AdminPage: React.FC = () => {
  // LIVE data — same AppContext state the Ministry dashboard already uses.
  const { agentStatus, riskState, setAgentStatus } = useAppContext();

  // Still-mock data — only for the two panels with no live backend source
  // (Postgres pool stats, external API health).
  const [mockHealth, setMockHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    // Seed the shared agentStatus ourselves — AppContext's WebSocket
    // handler only ever merges updates into an existing value, it never
    // fetches the initial one. Without this, landing directly on /admin
    // (its own role's home route) without visiting /ministry first would
    // leave the Agent Pipeline panel stuck showing nothing.
    getAgentStatus()
      .then(setAgentStatus)
      .catch(() => {
        // Non-fatal — mockHealth still loads below, and the panel
        // falls back to "INACTIVE"/"never" per agent if this never lands.
      });

    getSystemHealth()
      .then((h) => { setMockHealth(h); setLoading(false); })
      .catch(() => { setLoading(false); setLoadError(true); });
  }, [setAgentStatus]);

  if (loading) {
    return (
      <AppLayout showRiskStrip={false}>
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-40 bg-slate-800 rounded-xl border border-slate-700 animate-pulse" />
          ))}
        </div>
      </AppLayout>
    );
  }

  if (loadError || !mockHealth) {
    return (
      <AppLayout showRiskStrip={false}>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="bg-slate-800 border border-red-800 rounded-xl p-6 max-w-md text-center">
            <p className="text-red-400 text-sm font-medium mb-1">Unable to load system health</p>
            <p className="text-slate-500 text-xs">Backend may be unreachable.</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 text-xs text-blue-400 hover:underline"
            >
              Retry
            </button>
          </div>
        </div>
      </AppLayout>
    );
  }

  const crisisActive = riskState?.system_mode === "CRISIS";

  return (
    <AppLayout showRiskStrip={false}>
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium text-white">System Health</h1>
          <p className="text-slate-400 text-sm mt-1">Admin — full pipeline and infrastructure status</p>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-lg border font-medium ${
          crisisActive
            ? "bg-red-900/50 text-red-400 border-red-800 animate-pulse"
            : "bg-green-900/50 text-green-400 border-green-800"
        }`}>
          {crisisActive ? "CRISIS MODE ACTIVE" : "NOMINAL"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-6 mb-6">
        <AgentGrid agents={agentStatus?.agents ?? {}} />
        <InfraHealthPanel
          redisDepths={agentStatus?.redis_stream_depths ?? { "events:raw": 0, "events:verified": 0 }}
          postgresPool={mockHealth.postgres_pool}
        />
      </div>

      <div className="mb-6">
        <ExternalApiPanel apis={mockHealth.external_apis} />
      </div>
    </AppLayout>
  );
};

export default AdminPage;