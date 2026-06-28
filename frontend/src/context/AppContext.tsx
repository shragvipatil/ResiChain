import React, { createContext, useContext, useState, useCallback } from "react";
import { CorridorRiskState, AgentsStatusResponse, WebSocketEvent } from "../types";
import { useWebSocket } from "../hooks/useWebSocket";

interface AppContextType {
  riskState: CorridorRiskState | null;
  setRiskState: (state: CorridorRiskState) => void;
  agentStatus: AgentsStatusResponse | null;
  setAgentStatus: (status: AgentsStatusResponse) => void;
  crisisModeActive: boolean;
  compoundDisruptionDetected: boolean;   // Day 7: triggers Cape route animation
  wsConnected: boolean;
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [riskState, setRiskState]       = useState<CorridorRiskState | null>(null);
  const [agentStatus, setAgentStatus]   = useState<AgentsStatusResponse | null>(null);
  const [crisisModeActive, setCrisisModeActive]               = useState(false);
  const [compoundDisruptionDetected, setCompoundDisruptionDetected] = useState(false);

  const handleWsEvent = useCallback((event: WebSocketEvent) => {
    switch (event.type) {
      case "RISK_STATE_UPDATED":
        setRiskState(event.payload as unknown as CorridorRiskState);
        break;
      case "CONFIRMED_ALERT":
        setCrisisModeActive(true);
        break;
      case "COMPOUND_DISRUPTION_DETECTED":
        setCrisisModeActive(true);
        setCompoundDisruptionDetected(true);  // Day 7: Leaflet Cape animation listens to this
        break;
      case "AGENT_STARTED":
      case "AGENT_COMPLETED":
        // Merge the agent update into existing status
        setAgentStatus((prev) => {
          if (!prev) return prev;
          const agentName = event.payload.agent_name as string;
          const update    = event.payload.update as Record<string, unknown>;
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
        break;
    }
  }, []);

  const { connected: wsConnected } = useWebSocket(handleWsEvent);

  return (
    <AppContext.Provider value={{
      riskState, setRiskState,
      agentStatus, setAgentStatus,
      crisisModeActive,
      compoundDisruptionDetected,
      wsConnected,
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