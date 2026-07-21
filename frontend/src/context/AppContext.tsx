import React, { createContext, useContext, useState, useCallback } from "react";
import { CorridorRiskState, CorridorDetail, AgentsStatusResponse, WebSocketEvent } from "../types";
import { useWebSocket } from "../hooks/useWebSocket";

/**
 * Agent 3's WebSocket broadcast sends each corridor as a raw float:
 * { "Hormuz": 0.5028, ... }. The REST endpoint GET /api/risk-state sends
 * each corridor as an object: { "Hormuz": { "risk_score": 0.5028, ... } }.
 * Confirmed root cause of dashboard showing "NaN%". Normalizing here so
 * every consumer gets a consistent shape regardless of source.
 */
function normalizeCorridorRiskState(raw: unknown): CorridorRiskState | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const rawCorridors = r.corridors as Record<string, unknown> | undefined;
  if (!rawCorridors) return null;

  const normalizedCorridors: Record<string, CorridorDetail> = {};
  for (const [name, value] of Object.entries(rawCorridors)) {
    if (typeof value === "number") {
      normalizedCorridors[name] = {
        risk_score: value,
        status: value > 0.65 ? "CRISIS" : value > 0.30 ? "WATCH" : "NORMAL",
        trend: "stable",
      };
    } else if (value && typeof value === "object" && "risk_score" in value) {
      normalizedCorridors[name] = value as CorridorDetail;
    }
  }

  return {
    corridors: normalizedCorridors as CorridorRiskState["corridors"],
    compound_risk: typeof r.compound_risk === "number" ? r.compound_risk : undefined,
    updated_at: (r.updated_at as string) ?? null,
    system_mode: (r.system_mode as CorridorRiskState["system_mode"]) ?? "NORMAL",
  };
}

interface AppContextType {
  riskState: CorridorRiskState | null;
  setRiskState: (state: CorridorRiskState) => void;
  agentStatus: AgentsStatusResponse | null;
  setAgentStatus: (status: AgentsStatusResponse) => void;
  crisisModeActive: boolean;
  compoundDisruptionDetected: boolean;   // Day 7: triggers Cape route animation
  wsConnected: boolean;
  wsReconnecting: boolean;
  playbookReady: boolean;
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [riskState, setRiskState]       = useState<CorridorRiskState | null>(null);
  const [agentStatus, setAgentStatus]   = useState<AgentsStatusResponse | null>(null);
  const [crisisModeActive, setCrisisModeActive]               = useState(false);
  const [compoundDisruptionDetected, setCompoundDisruptionDetected] = useState(false);
  const [playbookReady, setPlaybookReady] = useState(false);

  const handleWsEvent = useCallback((event: WebSocketEvent) => {
    // Normalize event type casing before matching — backend has sent both
    // uppercase (RISK_STATE_UPDATED) and lowercase (compound_disruption_detected)
    // variants of the same logical event. Case-sensitive switch was silently
    // dropping any non-exact-case message. Flagged to Person A as a contract
    // drift worth aligning on, but the frontend should be robust regardless.
    const normalizedType = (event.type as string).toUpperCase();

    switch (normalizedType) {
      case "RISK_STATE_UPDATED": {
        const normalized = normalizeCorridorRiskState(event.data);
        if (normalized) setRiskState(normalized);
        break;
      }
      case "CONFIRMED_ALERT":
        setCrisisModeActive(true);
        break;
      case "COMPOUND_DISRUPTION_DETECTED":
        setCrisisModeActive(true);
        setCompoundDisruptionDetected(true);  // Day 7: Leaflet Cape animation listens to this
        break;
      case "PLAYBOOK_READY":
        setPlaybookReady(true);
        setTimeout(() => setPlaybookReady(false), 1000);
        break;
      case "WATCH_ALERT":
        // WATCH alert = early warning state (yellow state in UI)
        // Does NOT trigger full crisis mode
        // Future: could set watchAlertActive flag for UI badge
        break;
      case "AGENT_STARTED":
      case "AGENT_COMPLETED": {
        // Merge the agent update into existing status.
        //
        // Day 20 fix: the backend's AGENT_STARTED broadcast flips
        // `status` to "running" but doesn't always include a fresh
        // `last_run` timestamp in its `update` payload — that field
        // is normally only written on completion. Without a fallback,
        // a row could show RUNNING next to a stale "5h ago" carried
        // over from the last completed run (confirmed live in the
        // Command Center demo). Stamp the transition time from the
        // client clock whenever the backend didn't supply its own —
        // WS delivery latency is negligible for a "just now"/"Xm ago"
        // label either way, and this keeps the row honest regardless
        // of what the backend does or doesn't send.
        const agentName = event.data.agent_name as string;
        const update = (event.data.update as Record<string, unknown>) ?? {};
        setAgentStatus((prev) => {
          if (!prev) return prev;
          const existing = prev.agents[agentName] ?? {};
          return {
            ...prev,
            agents: {
              ...prev.agents,
              [agentName]: {
                ...existing,
                ...update,
                last_run: (update.last_run as string) ?? new Date().toISOString(),
              },
            },
          };
        });
        break;
      }
      case "PIPELINE_NODE_COMPLETE": {
        // crisis_graph.py's LangGraph nodes broadcast this on completion —
        // shape differs from AGENT_STARTED/AGENT_COMPLETED ({node,
        // timestamp} instead of {agent_name, update}), and there's no
        // matching "node started" broadcast, so this can only show
        // "just completed" per stage, not a live spinner. Still gives
        // real-time pipeline progress instead of the panel sitting
        // frozen at all-INACTIVE during a crisis run.
        const rawNode = event.data.node as string;
        // Agent 5 runs twice (first pass, then again after Agent 6's
        // approved options) — both map to the single "agent5" row in
        // AGENT_META, since there's no separate entry for the two passes.
        const agentKey = rawNode.startsWith("agent5") ? "agent5" : rawNode;

        setAgentStatus((prev) => {
          if (!prev) return prev;
          const existing = prev.agents[agentKey] ?? {};
          return {
            ...prev,
            agents: {
              ...prev.agents,
              [agentKey]: {
                ...existing,
                status: "idle",
                last_run: (event.data.timestamp as string) ?? new Date().toISOString(),
              },
            },
          };
        });
        break;
      }
      default:
        // Log unrecognized event types during development so contract
        // drift like this surfaces immediately instead of silently no-oping.
        if (process.env.NODE_ENV === "development") {
          console.warn(`[WebSocket] Unhandled event type: "${event.type}"`, event.data);
        }
        break;
    }
  }, []);

  const { connected: wsConnected, reconnecting: wsReconnecting } = useWebSocket(handleWsEvent);

  // Re-fetch fresh risk state every time the WebSocket (re)connects —
  // covers both initial load AND tab reopen/reconnect after a drop.
  // Without this, a reopened tab could sit on stale seeded/baseline
  // data indefinitely if it happens to reconnect between broadcasts.
  const prevConnectedRef = React.useRef(false);
  React.useEffect(() => {
    const justConnected = wsConnected && !prevConnectedRef.current;
    prevConnectedRef.current = wsConnected;

    if (justConnected) {
      import("../api/endpoints").then(({ getRiskState }) => {
        getRiskState().then(setRiskState).catch(() => {
          // Non-fatal — next WebSocket broadcast will still correct it
        });
      });
    }
  }, [wsConnected]);

  return (
    <AppContext.Provider value={{
      riskState,
      setRiskState,
      agentStatus,
      setAgentStatus,
      crisisModeActive,
      compoundDisruptionDetected,
      wsConnected,
      wsReconnecting,
      playbookReady,
    }}>
      {children}
    </AppContext.Provider>
  );
};

export const useAppContext = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be inside AppProvider");
  return ctx;
};