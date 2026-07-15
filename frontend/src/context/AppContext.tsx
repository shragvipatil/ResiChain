import React, { createContext, useContext, useState, useCallback } from "react";
import { CorridorRiskState, CorridorDetail, AgentsStatusResponse, WebSocketEvent } from "../types";
import { useWebSocket } from "../hooks/useWebSocket";

/**
 * Agent 3's WebSocket broadcast (agent3_risk_engine.py `_emit_risk_update`)
 * sends each corridor as a raw float: { "Hormuz": 0.5028, ... }
 * The REST endpoint GET /api/risk-state sends each corridor as an object:
 * { "Hormuz": { "risk_score": 0.5028, "status": "...", "trend": "..." } }
 * Confirmed root cause of dashboard showing "NaN%" — detail.risk_score on
 * a raw number is undefined. Normalizing here so every consumer (Ministry,
 * Viewer, ShippingMap, KnowledgeGraph) gets a consistent shape regardless
 * of which backend code path produced the update. Flagged to Person A as
 * a contract drift worth aligning at the source, but the frontend needs
 * to be robust to both shapes in the meantime given time constraints.
 */
function normalizeCorridorRiskState(raw: unknown): CorridorRiskState | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const rawCorridors = r.corridors as Record<string, unknown> | undefined;
  if (!rawCorridors) return null;

  const normalizedCorridors: Record<string, CorridorDetail> = {};
  for (const [name, value] of Object.entries(rawCorridors)) {
    if (typeof value === "number") {
      // Agent 3 broadcast shape — raw float, no status/trend info available
      normalizedCorridors[name] = {
        risk_score: value,
        status: value > 0.65 ? "CRISIS" : value > 0.30 ? "WATCH" : "NORMAL",
        trend: "stable",
      };
    } else if (value && typeof value === "object" && "risk_score" in value) {
      // REST endpoint shape — already correct
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
      case "CONNECTED":
        // Initial handshake confirmation from backend on socket open — no-op
        break;
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
      case "AGENT_COMPLETED":
        // Merge the agent update into existing status
        setAgentStatus((prev) => {
          if (!prev) return prev;
          const agentName = event.data.agent_name as string;
          const update    = event.data.update as Record<string, unknown>;
          return {
            ...prev,
            agents: {
              ...prev.agents,
              [agentName]: { ...prev.agents[agentName], ...update },
            },
          };
        });
        break;
      default:
        // Log unrecognized event types during development so contract
        // drift like this surfaces immediately instead of silently no-oping.
        if (process.env.NODE_ENV === "development") {
          console.warn(`[WebSocket] Unhandled event type: "${event.type}"`, event.data);
        }
        break;
    }
  }, []);

  const { connected: wsConnected } = useWebSocket(handleWsEvent);

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
        getRiskState().then((raw) => {
          const normalized = normalizeCorridorRiskState(raw);
          if (normalized) setRiskState(normalized);
        }).catch(() => {
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