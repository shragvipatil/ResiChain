import { USE_MOCK, AUTH_USE_MOCK, apiClient } from "./client";
import {
  mockRiskState, mockProcurement,
  mockPrices, mockAgentStatus, mockVessels, mockPlaybook,
} from "../mocks/data";
import {
  CorridorRiskState, ProcurementResponse,
  PricesResponse, AgentsStatusResponse, VesselsResponse,
  Playbook, ApprovePlaybookRequest, ApprovePlaybookResponse,
  KGraphData, User, LoginRequest, LoginResponse, RefineryGradeInfo, TankerETA, GradeSwitchOption, DeliveryScheduleDay, SystemHealth} from "../types";

const delay = (ms: number) => new Promise((res) => setTimeout(res, ms));

export const getRiskState = async (): Promise<CorridorRiskState> => {
  if (USE_MOCK) { await delay(300); return mockRiskState; }
  const res = await apiClient.get("/risk-state");
  return res.data;
};

export const getProcurementOptions = async (): Promise<ProcurementResponse> => {
  if (USE_MOCK) { await delay(500); return mockProcurement; }
  const res = await apiClient.get("/procurement/options");
  const raw = res.data;

  // Real backend returns { options: [...], total, generated_at?, source }
  // with each option using id/grade instead of option_id/crude_grade.
  const rawOptions: any[] = raw?.options ?? [];
  const options = rawOptions.map((o: any) => ({
    option_id:                  o.option_id ?? o.id ?? "",
    supplier:                   o.supplier ?? "Unknown",
    crude_grade:                o.crude_grade ?? o.grade ?? "",
    status:                     o.status ?? "BLOCKED",
    confidence:                 o.confidence ?? 0,
    rule_triggered:             o.rule_triggered,
    reason:                     o.reason ?? (o.block_reason
      ? { rule: o.rule_triggered ?? "UNKNOWN", value: o.block_reason, threshold: null, source: "" }
      : null),
    route:                      o.route,
    transit_days:               o.transit_days,
    cost_delta_usd_per_barrel:  o.cost_delta_usd_per_barrel ?? o.price_premium_pct,
    volume_mbd:                 o.volume_mbd,
    tanker_available:           o.tanker_available,
    max_allowed_delta_mbd:      o.max_allowed_delta_mbd,
    evaluated_at:               o.evaluated_at ?? raw?.generated_at ?? new Date().toISOString(),
  }));

  return {
    evaluated_at:         raw?.generated_at ?? new Date().toISOString(),
    surviving_corridors:  raw?.surviving_corridors ?? [],
    options,
  };
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
  const raw = res.data;

  // Real backend sends { mmsi, name, lat, lon, speed, destination, vessel_type }
  // Frontend Vessel type expects { latitude, longitude, speed_knots, heading_degrees }.
  // Confirmed root cause of "Invalid LatLng object: (undefined, undefined)" crash
  // in ShippingMap. Normalizing here.
  const rawVessels: unknown[] = Array.isArray(raw) ? raw : raw?.vessels ?? [];
  const vessels = rawVessels
    .map((v: any) => ({
      mmsi:            v.mmsi ?? "",
      name:            v.name ?? "Unknown Vessel",
      vessel_type:     v.vessel_type ?? "unknown",
      latitude:        v.latitude ?? v.lat,
      longitude:       v.longitude ?? v.lon,
      speed_knots:     v.speed_knots ?? v.speed ?? 0,
      heading_degrees: v.heading_degrees ?? 0,
      last_updated:    v.last_updated ?? new Date().toISOString(),
    }))
    .filter((v) => typeof v.latitude === "number" && typeof v.longitude === "number");

  return {
    vessels,
    cache_age_seconds: raw?.cache_age_seconds ?? 0,
    source: raw?.source ?? "live",
  };
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
  const raw = res.data;

  // Real backend playbook shape (confirmed live via curl) is entirely
  // different from the frontend Playbook type — this is Agent 8's mock
  // output shape (id, event_summary, spr_schedule, evidence_chain) with
  // NO actions array at all, vs. what PlaybookPage was built against
  // (playbook_id, corridor_affected, actions[]). Without this actions
  // array, initActionStates(pb.actions) called .map() on undefined and
  // crashed. Normalizing here — synthesizing a single action summarizing
  // the playbook until Agent 8's real per-action breakdown is available.
  const evidence = raw.evidence_chain ?? {};
  const signalSeconds = evidence.signal_to_playbook_seconds ?? 0;
  const readyAt = raw.created_at ?? new Date().toISOString();
  const detectedAt = new Date(
    new Date(readyAt).getTime() - signalSeconds * 1000
  ).toISOString();

  const syntheticActions = Array.isArray(raw.actions) && raw.actions.length > 0
    ? raw.actions
    : [{
        action_id:                 raw.id ?? id,
        title:                     raw.event_summary ?? "Recommended response",
        supplier:                  "Diversified suppliers",
        crude_grade:               "Mixed",
        route:                     "Cape of Good Hope",
        confidence:                raw.overall_confidence ?? 0,
        cost_delta_usd_per_barrel: raw.cost_delta_bn ? raw.cost_delta_bn * 1000 : 0,
        volume_mbd:                raw.spr_schedule?.daily_drawdown_mbd ?? 0,
        transit_days:              raw.spr_schedule?.duration_days ?? 0,
        contract_reference:        "—",
        rationale:                 `Supply continuity: ${raw.supply_continuity_pct ?? "—"}%`,
      }];

  return {
    playbook_id:        raw.id ?? raw.playbook_id ?? id,
    status:              raw.status ?? "pending_review",
    created_at:          raw.created_at ?? new Date().toISOString(),
    signal_detected_at:  raw.signal_detected_at ?? detectedAt,
    playbook_ready_at:   raw.playbook_ready_at ?? readyAt,
    corridor_affected:   raw.corridor_affected ?? raw.event_summary ?? "—",
    compound_risk:       raw.compound_risk ?? 0,
    overall_confidence:  raw.overall_confidence ?? 0,
    actions:             syntheticActions,
    analyst_notes:       raw.analyst_notes,
  };
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
  if (AUTH_USE_MOCK) {
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
  if (AUTH_USE_MOCK) {
    await delay(200);
    sessionStorage.removeItem("mock_user");
    return;
  }
  await apiClient.post("/auth/logout");
};

export const getCurrentUser = async (): Promise<User | null> => {
  if (AUTH_USE_MOCK) {
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
// ── Refinery Operator endpoints (Day 12) ──────────────────────────────────────

const MOCK_REFINERY_GRADES: RefineryGradeInfo[] = [
  {
    refinery_id: "jamnagar", refinery_name: "Jamnagar Complex (RIL)",
    grades: [
      { grade: "Arab Light",   status: "available", volume_mbd: 0.45 },
      { grade: "Murban",       status: "available", volume_mbd: 0.30 },
      { grade: "Urals",        status: "reduced",   volume_mbd: 0.12, note: "Diversification cap nearly reached" },
      { grade: "Iranian Light",status: "disrupted", volume_mbd: 0.0,  note: "OFAC sanctions — sourcing halted" },
    ],
  },
  {
    refinery_id: "kochi_bpcl", refinery_name: "Kochi Refinery (BPCL)",
    grades: [
      { grade: "Arab Light", status: "available", volume_mbd: 0.18 },
      { grade: "Murban",     status: "available", volume_mbd: 0.13 },
    ],
  },
  {
    refinery_id: "vadinar", refinery_name: "Vadinar (Nayara Energy)",
    grades: [
      { grade: "Arab Light", status: "available", volume_mbd: 0.20 },
      { grade: "Urals",      status: "reduced",   volume_mbd: 0.08, note: "Cape route adds 12 days transit" },
      { grade: "Basra Light",status: "available", volume_mbd: 0.12 },
    ],
  },
];

const MOCK_TANKER_ETAS: TankerETA[] = [
  {
    vessel_name: "PACIFIC STAR", vessel_type: "VLCC", origin: "Fujairah, UAE",
    destination_port: "Vadinar", eta: new Date(Date.now() + 4 * 86400000).toISOString(),
    cargo_grade: "Murban", volume_mbd: 0.15, current_lat: 23.1, current_lng: 61.4, status: "on_schedule",
  },
  {
    vessel_name: "GULF EAGLE", vessel_type: "Suezmax", origin: "Ras Tanura, Saudi Arabia",
    destination_port: "Jamnagar", eta: new Date(Date.now() + 6 * 86400000).toISOString(),
    cargo_grade: "Arab Light", volume_mbd: 0.10, current_lat: 20.5, current_lng: 63.2, status: "on_schedule",
  },
  {
    vessel_name: "NAYARA PRIDE", vessel_type: "VLCC", origin: "Novorossiysk, Russia",
    destination_port: "Vadinar", eta: new Date(Date.now() + 14 * 86400000).toISOString(),
    cargo_grade: "Urals", volume_mbd: 0.08, current_lat: 12.4, current_lng: 48.1, status: "delayed",
  },
  {
    vessel_name: "KOCHI VOYAGER", vessel_type: "Aframax", origin: "Jebel Ali, UAE",
    destination_port: "Kochi", eta: new Date(Date.now() + 8 * 86400000).toISOString(),
    cargo_grade: "Murban", volume_mbd: 0.06, current_lat: 15.8, current_lng: 55.6, status: "on_schedule",
  },
];

const MOCK_GRADE_SWITCHES: GradeSwitchOption[] = [
  { refinery_id: "jamnagar",   refinery_name: "Jamnagar Complex",  from_grade: "Iranian Light", to_grade: "Murban",     feasible: true,  reason: "Fully compatible — both light sweet grades", switch_time_days: 2 },
  { refinery_id: "jamnagar",   refinery_name: "Jamnagar Complex",  from_grade: "Urals",         to_grade: "Arab Light", feasible: true,  reason: "Fully compatible", switch_time_days: 1 },
  { refinery_id: "kochi_bpcl", refinery_name: "Kochi Refinery",    from_grade: "Arab Light",    to_grade: "Basra Light",feasible: false, reason: "Kochi lacks coker unit for high-sulfur heavy crude" },
  { refinery_id: "vadinar",    refinery_name: "Vadinar (Nayara)",  from_grade: "Urals",         to_grade: "Murban",     feasible: true,  reason: "Fully compatible", switch_time_days: 3 },
];

const MOCK_DELIVERY_SCHEDULE: DeliveryScheduleDay[] = Array.from({ length: 14 }).map((_, i) => {
  const refineries = [
    { id: "jamnagar", name: "Jamnagar Complex", grade: "Arab Light", vol: 0.45, source: "Saudi Aramco" },
    { id: "kochi_bpcl", name: "Kochi Refinery", grade: "Murban", vol: 0.13, source: "ADNOC" },
    { id: "vadinar", name: "Vadinar (Nayara)", grade: "Urals", vol: 0.08, source: "Rosneft" },
  ];
  const r = refineries[i % refineries.length];
  return {
    date: new Date(Date.now() + i * 86400000).toISOString().split("T")[0],
    refinery_id: r.id, refinery_name: r.name, grade: r.grade,
    volume_mbd: r.vol, source: r.source, confirmed: i < 7,
  };
});

export const getRefineryGrades = async (): Promise<RefineryGradeInfo[]> => {
  if (USE_MOCK) { await delay(300); return MOCK_REFINERY_GRADES; }
  const res = await apiClient.get("/refinery/grades");
  return res.data;
};

export const getTankerETAs = async (): Promise<TankerETA[]> => {
  if (USE_MOCK) { await delay(300); return MOCK_TANKER_ETAS; }
  const res = await apiClient.get("/refinery/tanker-etas");
  return res.data;
};

export const getGradeSwitchOptions = async (): Promise<GradeSwitchOption[]> => {
  if (USE_MOCK) { await delay(300); return MOCK_GRADE_SWITCHES; }
  const res = await apiClient.get("/refinery/grade-switches");
  return res.data;
};

export const getDeliverySchedule = async (): Promise<DeliveryScheduleDay[]> => {
  if (USE_MOCK) { await delay(300); return MOCK_DELIVERY_SCHEDULE; }
  const res = await apiClient.get("/refinery/delivery-schedule");
  return res.data;
};

// ── Admin System Health endpoint (Day 12) ─────────────────────────────────────

export const getSystemHealth = async (): Promise<SystemHealth> => {
  if (USE_MOCK) {
    await delay(300);
    return {
      agents: {
        agent_1: { status: "RUNNING", last_run: new Date().toISOString() },
        agent_2: { status: "IDLE",    last_run: new Date(Date.now() - 60000).toISOString() },
        agent_3: { status: "IDLE",    last_run: new Date(Date.now() - 45000).toISOString() },
        agent_4: { status: "INACTIVE", last_run: null },
        agent_5: { status: "INACTIVE", last_run: null },
        agent_6: { status: "INACTIVE", last_run: null },
        agent_7: { status: "INACTIVE", last_run: null },
        agent_8: { status: "INACTIVE", last_run: null },
      },
      redis_stream_depths: { "events:raw": 0, "events:verified": 0 },
      postgres_pool: { active_connections: 4, max_connections: 20, status: "healthy" },
      external_apis: [
        { name: "GDELT",         last_success_at: new Date(Date.now() - 120000).toISOString(), status: "healthy", latency_ms: 340 },
        { name: "UKMTO RSS",     last_success_at: new Date(Date.now() - 300000).toISOString(), status: "healthy", latency_ms: 210 },
        { name: "OFAC SDN",      last_success_at: new Date(Date.now() - 3600000 * 5).toISOString(), status: "healthy", latency_ms: 890 },
        { name: "Alpha Vantage", last_success_at: new Date(Date.now() - 300000).toISOString(), status: "healthy", latency_ms: 450 },
        { name: "Gemini 2.5 Flash", last_success_at: null, status: "down", latency_ms: undefined },
      ],
      crisis_mode_active: false,
    };
  }
  const res = await apiClient.get("/admin/system-health");
  return res.data;
};