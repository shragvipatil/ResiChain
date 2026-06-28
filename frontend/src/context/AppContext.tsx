import React, { createContext, useContext, useState, useCallback } from "react";
import { CorridorRiskState, WebSocketEvent } from "../types";
import { useWebSocket } from "../hooks/useWebSocket";

interface AppContextType {
  riskState: CorridorRiskState | null;
  setRiskState: (state: CorridorRiskState) => void;
  crisisModeActive: boolean;
  wsConnected: boolean;
}

const AppContext = createContext<AppContextType | null>(null);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [riskState, setRiskState] = useState<CorridorRiskState | null>(null);
  const [crisisModeActive, setCrisisModeActive] = useState(false);

  const handleWsEvent = useCallback((event: WebSocketEvent) => {
    if (event.type === "RISK_STATE_UPDATED") {
      setRiskState(event.payload as unknown as CorridorRiskState);
    }
    if (event.type === "CONFIRMED_ALERT" || event.type === "COMPOUND_DISRUPTION_DETECTED") {
      setCrisisModeActive(true);
    }
  }, []);

  const { connected: wsConnected } = useWebSocket(handleWsEvent);

  return (
    <AppContext.Provider value={{ riskState, setRiskState, crisisModeActive, wsConnected }}>
      {children}
    </AppContext.Provider>
  );
};

export const useAppContext = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be inside AppProvider");
  return ctx;
};