/**
 * AgentStatusPanel.tsx — Day 5 deliverable (Person C)
 *
 * Shows all 8 agents with:
 *  - Status badge (RUNNING / IDLE / INACTIVE / ERROR)
 *  - Spinning indicator when RUNNING
 *  - Last-run timestamp (relative: "2 min ago")
 *  - Events processed today / queue depth where available
 *
 * Data source: AppContext.agentStatus (seeded from GET /api/agents/status on mount,
 * then kept live by AGENT_STARTED / AGENT_COMPLETED WebSocket events)
 *
 * Day 9: AGENT_STARTED fires on every LangGraph node transition — this panel
 * will animate in real time during the demo without any changes here.
 */

import React, { useEffect } from "react";
import { useAppContext } from "../context/AppContext";
import { getAgentStatus } from "../api/endpoints";
import { AgentInfo, AgentStatus } from "../types";

// ── Agent metadata (labels and descriptions never come from the API) ──────────
const AGENT_META: Record<string, { label: string; description: string; mode: "background" | "crisis" }> = {
  agent_1: { label: "Agent 1",  description: "Ingestion & Verification",   mode: "background" },
  agent_2: { label: "Agent 2",  description: "RAG Event Classifier",       mode: "background" },
  agent_3: { label: "Agent 3",  description: "Corridor Risk Engine",        mode: "background" },
  agent_4: { label: "Agent 4",  description: "Compound Disruption Detector",mode: "crisis"     },
  agent_5: { label: "Agent 5",  description: "SPR LP Optimiser",            mode: "crisis"     },
  agent_6: { label: "Agent 6",  description: "Procurement Orchestrator",    mode: "crisis"     },
  agent_7: { label: "Agent 7",  description: "Constraint Validator",        mode: "crisis"     },
  agent_8: { label: "Agent 8",  description: "Playbook Generator",          mode: "crisis"     },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(iso: string | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1)  return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function statusConfig(status: AgentStatus): { dot: string; badge: string; label: string } {
  switch (status) {
    case "RUNNING":  return { dot: "bg-blue-400 animate-pulse", badge: "bg-blue-900/50 text-blue-400 border-blue-800",   label: "RUNNING"  };
    case "IDLE":     return { dot: "bg-green-400",               badge: "bg-green-900/50 text-green-400 border-green-800", label: "IDLE"     };
    case "ERROR":    return { dot: "bg-red-400 animate-pulse",   badge: "bg-red-900/50 text-red-400 border-red-800",       label: "ERROR"    };
    case "INACTIVE": return { dot: "bg-slate-600",               badge: "bg-slate-800 text-slate-500 border-slate-700",    label: "INACTIVE" };
  }
}

// ── Spinning ring shown when agent is RUNNING ─────────────────────────────────
const SpinRing: React.FC = () => (
  <svg className="w-3.5 h-3.5 animate-spin text-blue-400 shrink-0" viewBox="0 0 24 24" fill="none">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path className="opacity-75" fill="currentColor"
      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
  </svg>
);

// ── Single agent row ──────────────────────────────────────────────────────────
const AgentRow: React.FC<{ id: string; info: AgentInfo }> = ({ id, info }) => {
  const meta   = AGENT_META[id] ?? { label: id, description: "—", mode: "background" };
  const cfg    = statusConfig(info.status);
  const isRun  = info.status === "RUNNING";

  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-slate-700/50 last:border-0">
      {/* Status dot / spin ring */}
      <div className="w-5 flex justify-center shrink-0">
        {isRun ? <SpinRing /> : <div className={`w-2 h-2 rounded-full ${cfg.dot}`} />}
      </div>

      {/* Agent name + description */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-white text-xs font-medium">{meta.label}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded border ${cfg.badge} font-medium tracking-wide`}>
            {cfg.label}
          </span>
          {meta.mode === "crisis" && (
            <span className="text-xs text-slate-600">crisis only</span>
          )}
        </div>
        <p className="text-slate-500 text-xs mt-0.5 truncate">{meta.description}</p>
      </div>

      {/* Stats */}
      <div className="text-right shrink-0">
        {info.events_today != null && (
          <p className="text-slate-400 text-xs tabular-nums">{info.events_today} events</p>
        )}
        {info.queue_depth != null && (
          <p className="text-slate-500 text-xs tabular-nums">queue: {info.queue_depth}</p>
        )}
        {info.note && (
          <p className="text-slate-600 text-xs italic max-w-[140px] truncate" title={info.note}>
            {info.note}
          </p>
        )}
        <p className="text-slate-600 text-xs tabular-nums mt-0.5">
          {relativeTime(info.last_run)}
        </p>
      </div>
    </div>
  );
};

// ── Panel ─────────────────────────────────────────────────────────────────────
const AgentStatusPanel: React.FC = () => {
  const { agentStatus, setAgentStatus, wsConnected } = useAppContext();

  // Seed initial agent status from API on mount
  useEffect(() => {
    getAgentStatus().then(setAgentStatus);
  }, [setAgentStatus]);

  const agents = agentStatus?.agents ?? {};
  const runningCount  = Object.values(agents).filter((a) => a.status === "RUNNING").length;
  const streamDepths  = agentStatus?.redis_stream_depths;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-700 flex items-center justify-between">
        <div>
          <h2 className="text-white text-sm font-medium">Agent Pipeline</h2>
          <p className="text-slate-500 text-xs mt-0.5">
            {runningCount > 0
              ? `${runningCount} agent${runningCount > 1 ? "s" : ""} running`
              : "All agents idle"}
          </p>
        </div>
        {/* WebSocket connection indicator */}
        <div className="flex items-center gap-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-green-400" : "bg-red-400 animate-pulse"}`} />
          <span className="text-xs text-slate-500">{wsConnected ? "Live" : "Reconnecting"}</span>
        </div>
      </div>

      {/* Agent rows */}
      <div className="px-5 py-1">
        {Object.entries(AGENT_META).map(([id]) => {
          const info = agents[id] ?? { status: "INACTIVE" as const };
          return <AgentRow key={id} id={id} info={info} />;
        })}
      </div>

      {/* Redis stream depths footer */}
      {streamDepths && (
        <div className="px-5 py-3 border-t border-slate-700 flex items-center gap-6">
          <div>
            <p className="text-slate-600 text-xs">events:raw</p>
            <p className="text-slate-400 text-xs tabular-nums font-medium">
              {streamDepths["events:raw"]} pending
            </p>
          </div>
          <div>
            <p className="text-slate-600 text-xs">events:verified</p>
            <p className="text-slate-400 text-xs tabular-nums font-medium">
              {streamDepths["events:verified"]} pending
            </p>
          </div>
          <div className="ml-auto">
            <p className="text-slate-600 text-xs">
              Crisis mode:{" "}
            <span className={agentStatus?.crisis_mode_active ? "text-red-400" : "text-green-400"}>
              {agentStatus?.crisis_mode_active ? "ACTIVE" : "Inactive"}
            </span>
            </p>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentStatusPanel;