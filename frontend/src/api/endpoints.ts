import { USE_MOCK, apiClient } from "./client";
import {
  mockRiskState, mockProcurement,
  mockPrices, mockAgentStatus, mockVessels,
} from "../mocks/data";
import {
  CorridorRiskState, ProcurementResponse,
  PricesResponse, AgentsStatusResponse, VesselsResponse,
} from "../types";

const delay = (ms: number) => new Promise((res) => setTimeout(res, ms));

export const getRiskState = async (): Promise<CorridorRiskState> => {
  if (USE_MOCK) { await delay(300); return mockRiskState; }
  const res = await apiClient.get("/risk-state");
  return res.data;
};

export const getProcurementOptions = async (): Promise<ProcurementResponse> => {
  if (USE_MOCK) { await delay(500); return mockProcurement; }
  const res = await apiClient.get("/procurement/options");
  return res.data;
};

export const getLivePrices = async (): Promise<PricesResponse> => {
  if (USE_MOCK) { await delay(200); return mockPrices; }
  const res = await apiClient.get("/prices/live");
  return res.data;
};

export const getAgentStatus = async (): Promise<AgentsStatusResponse> => {
  if (USE_MOCK) { await delay(200); return mockAgentStatus; }
  const res = await apiClient.get("/agents/status");
  return res.data;
};

export const getVessels = async (): Promise<VesselsResponse> => {
  if (USE_MOCK) { await delay(300); return mockVessels; }
  const res = await apiClient.get("/map/vessels");
  return res.data;
};

export const updateRiskWeights = async (weights: {
  military_incidents: number;
  conflict_escalation: number;
  sanctions_change: number;
  market_volatility: number;
  seasonal_risk: number;
}) => {
  if (USE_MOCK) { await delay(400); return { weights_updated: true }; }
  const res = await apiClient.patch("/risk-weights", weights);
  return res.data;
};