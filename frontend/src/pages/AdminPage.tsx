/**
 * AdminPage.tsx — Day 12 deliverable (Person C)
 *
 * System health dashboard:
 *   - All 8 agent statuses
 *   - Redis stream queue depths (events:raw, events:verified)
 *   - PostgreSQL connection pool status
 *   - External API health (last successful call per source)
 *
 * Day 13+: swap getSystemHealth() mock for real endpoint — no component changes.
 */

import React, { useEffect, useState } from "react";
import AppLayout from "../components/AppLayout";
import { getSystemHealth } from "../api/endpoints";
import { SystemHealth } from "../types";

const AGENT_LABELS: Record<string, string> = {
  agent_1: "Agent 1 — Ingestion & Verification",
  agent_2: "Agent 2 — RAG Event Classifier",
  agent_3: "Agent 3 — Corridor Risk Engine",
  agent_4: "Agent 4 — Compound Disruption Detector",
  agent_5: "Agent 5 — SPR LP Optimiser",
  agent_6: "Agent 6 — Procurement Orchestrator",
  agent_7: "Agent 7 — Constraint Validator",
  agent_8: "Agent 8 — Playbook Generator",
};

function relativeTime(iso: string | null): string {
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

// ── Panel: Agent statuses ──────────────────────────────────────────────────────

const AgentGrid: React.FC<{ agents: SystemHealth["agents"] }> = ({ agents }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Agent Pipeline Status</h2>
      <p className="text-slate-500 text-xs mt-0.5">All 8 agents</p>
    </div>
    <div className="p-5 grid grid-cols-2 gap-3">
      {Object.entries(AGENT_LABELS).map(([id, label]) => {
        const info = agents[id] ?? { status: "INACTIVE", last_run: null };
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

// ── Panel: Redis + Postgres ────────────────────────────────────────────────────

const InfraHealthPanel: React.FC<{ health: SystemHealth }> = ({ health }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Infrastructure</h2>
      <p className="text-slate-500 text-xs mt-0.5">Redis streams · PostgreSQL pool</p>
    </div>
    <div className="p-5 space-y-4">
      <div>
        <p className="text-slate-500 text-xs mb-2">Redis Stream Queue Depth</p>
        <div className="grid grid-cols-2 gap-3">
          {Object.entries(health.redis_stream_depths).map(([stream, depth]) => (
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
        <p className="text-slate-500 text-xs mb-2">PostgreSQL Connection Pool</p>
        <div className="bg-slate-900 rounded-lg px-3 py-3 border border-slate-700/50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-slate-300 text-xs">
              {health.postgres_pool.active_connections} / {health.postgres_pool.max_connections} connections
            </span>
            <span className={`text-xs font-medium ${health.postgres_pool.status === "healthy" ? "text-green-400" : "text-amber-400"}`}>
              {health.postgres_pool.status.toUpperCase()}
            </span>
          </div>
          <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full"
              style={{ width: `${(health.postgres_pool.active_connections / health.postgres_pool.max_connections) * 100}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  </div>
);

// ── Panel: External API health ─────────────────────────────────────────────────

const ExternalApiPanel: React.FC<{ apis: SystemHealth["external_apis"] }> = ({ apis }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">External API Health</h2>
      <p className="text-slate-500 text-xs mt-0.5">Last successful call per data source</p>
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
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [pollError, setPollError] = useState(false);

  useEffect(() => {
    const load = () =>
      getSystemHealth()
        .then((h) => { setHealth(h); setLoading(false); setPollError(false); })
        .catch(() => { setLoading(false); setPollError(true); });
    load();
    const interval = setInterval(load, 5000); // live poll — /agents/status has no WS event yet
    return () => clearInterval(interval);
  }, []);

  if (loading && !health) {
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

  if (pollError && !health) {
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

  if (!health) return null;

  return (
    <AppLayout showRiskStrip={false}>
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium text-white">System Health</h1>
          <p className="text-slate-400 text-sm mt-1">Admin — full pipeline and infrastructure status</p>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-lg border font-medium ${
          health.crisis_mode_active
            ? "bg-red-900/50 text-red-400 border-red-800 animate-pulse"
            : "bg-green-900/50 text-green-400 border-green-800"
        }`}>
          {health.crisis_mode_active ? "CRISIS MODE ACTIVE" : "NOMINAL"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-6 mb-6">
        <AgentGrid agents={health.agents} />
        <InfraHealthPanel health={health} />
      </div>

      <div className="mb-6">
        <ExternalApiPanel apis={health.external_apis} />
      </div>
    </AppLayout>
  );
};

export default AdminPage;