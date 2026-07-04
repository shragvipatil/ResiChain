import { USE_MOCK, apiClient } from "./client";
import {
  mockRiskState, mockProcurement,
  mockPrices, mockAgentStatus, mockVessels, mockPlaybook,
} from "../mocks/data";
import {
  CorridorRiskState, ProcurementResponse,
  PricesResponse, AgentsStatusResponse, VesselsResponse,
  Playbook, ApprovePlaybookRequest, ApprovePlaybookResponse,
  KGraphData, User, LoginRequest, LoginResponse,
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
// ── Playbook endpoints (Day 8) ─────────────────────────────────────────────

export const getPlaybook = async (id: string): Promise<Playbook> => {
  if (USE_MOCK) { await delay(400); return mockPlaybook; }
  const res = await apiClient.get(`/playbook/${id}`);
  return res.data;
};

export const approvePlaybook = async (
  id: string,
  body: ApprovePlaybookRequest
): Promise<ApprovePlaybookResponse> => {
  if (USE_MOCK) {
    await delay(600);
    const allApproved = body.decisions.every((d) => d.decision === "approved");
    const anyApproved = body.decisions.some((d)  => d.decision === "approved");
    return {
      playbook_id: id,
      status: allApproved ? "fully_approved" : anyApproved ? "partially_approved" : "rejected",
      updated_at: new Date().toISOString(),
    };
  }
  const res = await apiClient.patch(`/playbook/${id}/approve`, body);
  return res.data;
};
// ── Knowledge Graph (Day 9) ───────────────────────────────────────────────────

export const getKGraph = async (): Promise<KGraphData> => {
  if (USE_MOCK) {
    await delay(300);
    return {
      nodes: [
        { id: "s1", label: "Saudi Arabia", type: "Supplier", share: 18 },
        { id: "s2", label: "Iraq",         type: "Supplier", share: 22 },
        { id: "s3", label: "Russia",       type: "Supplier", share: 22 },
        { id: "s4", label: "UAE",          type: "Supplier", share: 8  },
        { id: "s5", label: "USA",          type: "Supplier", share: 5  },
        { id: "c1", label: "Hormuz",       type: "Chokepoint", risk: 0.34 },
        { id: "c2", label: "Red Sea",      type: "Chokepoint", risk: 0.41 },
        { id: "c3", label: "Suez",         type: "Chokepoint", risk: 0.18 },
        { id: "c4", label: "Cape",         type: "Chokepoint", risk: 0.05 },
        { id: "rt1",label: "Gulf–India",   type: "Route" },
        { id: "rt2",label: "Red Sea–India",type: "Route" },
        { id: "rt3",label: "Cape Route",   type: "Route" },
        { id: "r1", label: "Jamnagar RIL", type: "Refinery", capacity: 1.24 },
        { id: "r2", label: "Kochi BPCL",   type: "Refinery", capacity: 0.31 },
        { id: "r3", label: "Vadinar",      type: "Refinery", capacity: 0.40 },
        { id: "g1", label: "Arab Light",   type: "CrudeGrade", gravity: 32.8 },
        { id: "g2", label: "Murban",       type: "CrudeGrade", gravity: 40.2 },
        { id: "g3", label: "Urals",        type: "CrudeGrade", gravity: 31.0 },
        { id: "g4", label: "Basra Light",  type: "CrudeGrade", gravity: 29.7 },
      ],
      edges: [
  // Suppliers → Routes (primary)
  { from: "s1", to: "rt1", label: "SHIPS_VIA" },   // Saudi → Gulf (primary)
  { from: "s1", to: "rt2", label: "SHIPS_VIA" },   // Saudi → Red Sea via Yanbu (bypass)
  { from: "s2", to: "rt1", label: "SHIPS_VIA" },   // Iraq → Gulf
  { from: "s4", to: "rt1", label: "SHIPS_VIA" },   // UAE → Gulf
  { from: "s3", to: "rt2", label: "SHIPS_VIA" },   // Russia → Red Sea/Suez
  { from: "s5", to: "rt3", label: "SHIPS_VIA" },   // USA → Cape

  // Fallback: Gulf suppliers can divert to Cape when Hormuz blocked
  { from: "s1", to: "rt3", label: "SHIPS_VIA" },   // Saudi → Cape (fallback)
  { from: "s2", to: "rt3", label: "SHIPS_VIA" },   // Iraq → Cape (fallback)

  // Routes → Chokepoints
  { from: "rt1", to: "c1", label: "PASSES_THROUGH" },  // Gulf → Hormuz
  { from: "rt2", to: "c3", label: "PASSES_THROUGH" },  // Red Sea → Suez first
  { from: "rt2", to: "c2", label: "PASSES_THROUGH" },  // then Red Sea
  { from: "rt3", to: "c4", label: "PASSES_THROUGH" },  // Cape route → Cape

  // Routes → Refineries
  { from: "rt1", to: "r1", label: "ARRIVES_AT" },
  { from: "rt1", to: "r3", label: "ARRIVES_AT" },
  { from: "rt2", to: "r2", label: "ARRIVES_AT" },
  { from: "rt3", to: "r1", label: "ARRIVES_AT" },
  { from: "rt3", to: "r2", label: "ARRIVES_AT" },

  // Suppliers → Grades
  { from: "s1", to: "g1", label: "PRODUCES" },
  { from: "s4", to: "g2", label: "PRODUCES" },
  { from: "s3", to: "g3", label: "PRODUCES" },
  { from: "s2", to: "g4", label: "PRODUCES" },

  // Grades → Refineries
  { from: "g1", to: "r1", label: "COMPATIBLE_WITH" },
  { from: "g1", to: "r2", label: "COMPATIBLE_WITH" },
  { from: "g2", to: "r1", label: "COMPATIBLE_WITH" },
  { from: "g2", to: "r3", label: "COMPATIBLE_WITH" },
  { from: "g3", to: "r3", label: "COMPATIBLE_WITH" },
  { from: "g4", to: "r2", label: "COMPATIBLE_WITH" },
],
    };
  }
  const res = await apiClient.get("/kgraph");
  return res.data;
};
// ── Auth endpoints (Day 11) ────────────────────────────────────────────────────

// Mock users — one per role, for demo login without a real backend yet
const MOCK_USERS: Record<string, { user: User; password: string; requiresTotp: boolean }> = {
  "ministry@resichain.gov.in": {
    user: { user_id: "u1", name: "Anita Sharma", email: "ministry@resichain.gov.in", role: "MINISTRY_USER" },
    password: "demo123",
    requiresTotp: true,
  },
  "procurement@resichain.gov.in": {
    user: { user_id: "u2", name: "Rahul Mehta", email: "procurement@resichain.gov.in", role: "PROCUREMENT_ANALYST" },
    password: "demo123",
    requiresTotp: false,
  },
  "refinery@resichain.gov.in": {
    user: { user_id: "u3", name: "Priya Nair", email: "refinery@resichain.gov.in", role: "REFINERY_OPERATOR" },
    password: "demo123",
    requiresTotp: false,
  },
  "viewer@resichain.gov.in": {
    user: { user_id: "u4", name: "Guest Viewer", email: "viewer@resichain.gov.in", role: "VIEWER" },
    password: "demo123",
    requiresTotp: false,
  },
  "admin@resichain.gov.in": {
    user: { user_id: "u5", name: "System Admin", email: "admin@resichain.gov.in", role: "ADMIN" },
    password: "demo123",
    requiresTotp: true,
  },
};

export const login = async (body: LoginRequest): Promise<LoginResponse> => {
  if (USE_MOCK) {
    await delay(500);
    const record = MOCK_USERS[body.email];
    if (!record || record.password !== body.password) {
      throw new Error("Invalid email or password");
    }
    if (record.requiresTotp && !body.totp_code) {
      return { user: record.user, requires_totp: true };
    }
    if (record.requiresTotp && body.totp_code !== "123456") {
      throw new Error("Invalid authenticator code");
    }
    // In mock mode there's no real cookie — sessionStorage stands in for
    // "logged in" state only inside the mock, purely for demo continuity.
    sessionStorage.setItem("mock_user", JSON.stringify(record.user));
    return { user: record.user };
  }
  const res = await apiClient.post("/auth/login", body);
  return res.data;
};

export const logout = async (): Promise<void> => {
  if (USE_MOCK) {
    await delay(200);
    sessionStorage.removeItem("mock_user");
    return;
  }
  await apiClient.post("/auth/logout");
};

export const getCurrentUser = async (): Promise<User | null> => {
  if (USE_MOCK) {
    await delay(150);
    const raw = sessionStorage.getItem("mock_user");
    return raw ? JSON.parse(raw) : null;
  }
  try {
    const res = await apiClient.get("/auth/me");
    return res.data;
  } catch {
    return null;
  }
};