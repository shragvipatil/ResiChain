import {
  CorridorRiskState,
  ProcurementResponse,
  PricesResponse,
  AgentsStatusResponse,
  VesselsResponse,
  Playbook
} from "../types";

/* ----------------------------- RISK STATE ----------------------------- */

export const mockRiskState: CorridorRiskState = {
  corridors: {
    Hormuz: {
      risk_score: 0.34,
      status: "WATCH",
      trend: "stable",
    },
    Red_Sea: {
      risk_score: 0.41,
      status: "WATCH",
      trend: "rising",
    },
    Suez: {
      risk_score: 0.18,
      status: "NORMAL",
      trend: "stable",
    },
    Cape: {
      risk_score: 0.05,
      status: "NORMAL",
      trend: "stable",
    },
  },
  compound_risk: 0.52,
  last_updated: new Date().toISOString(),
  system_mode: "WATCH",
};

/* -------------------------- PROCUREMENT MOCK -------------------------- */

export const mockProcurement: ProcurementResponse = {
  evaluated_at: new Date().toISOString(),
  surviving_corridors: ["Cape"],
  options: [
    {
      option_id: "opt_001",
      supplier: "Iran",
      crude_grade: "Iranian Light",
      status: "BLOCKED",
      confidence: 0,
      rule_triggered: "OFAC_SDN",
      reason: {
        rule: "OFAC_SDN",
        value: "Islamic Republic of Iran",
        threshold: null,
        source: "ofac.treasury.gov/SDN.XML",
      },
      evaluated_at: new Date().toISOString(),
    },
    {
      option_id: "opt_002",
      supplier: "Venezuela",
      crude_grade: "Merey",
      status: "BLOCKED",
      confidence: 0,
      rule_triggered: "GRADE_INCOMPATIBLE",
      reason: {
        rule: "GRADE_INCOMPATIBLE",
        value: "Merey (API 16, sulfur 2.5%)",
        threshold: "Kochi BPCL max sulfur 1.8%",
        source: "Neo4j COMPATIBLE_WITH relationship",
      },
      evaluated_at: new Date().toISOString(),
    },
    {
      option_id: "opt_003",
      supplier: "Russia",
      crude_grade: "Urals",
      status: "PARTIAL",
      confidence: 0.61,
      max_allowed_delta_mbd: 0.02,
      rule_triggered: "DIVERSIFICATION_CAP",
      reason: {
        rule: "DIVERSIFICATION_CAP",
        value: "Russia current share 38%",
        threshold: "Max 40% per supplier",
        headroom_mbd: 0.02,
        source: "UN Comtrade + MoPNG policy",
      },
      evaluated_at: new Date().toISOString(),
    },
    {
      option_id: "opt_004",
      supplier: "UAE",
      crude_grade: "Murban",
      status: "APPROVED",
      confidence: 0.91,
      route: "Cape of Good Hope",
      transit_days: 22,
      cost_delta_usd_per_barrel: 4.2,
      volume_mbd: 0.15,
      tanker_available: true,
      reason: null,
      evaluated_at: new Date().toISOString(),
    },
    {
      option_id: "opt_005",
      supplier: "Saudi Arabia",
      crude_grade: "Arab Light",
      status: "APPROVED",
      confidence: 0.88,
      route: "Cape of Good Hope",
      transit_days: 24,
      cost_delta_usd_per_barrel: 6.8,
      volume_mbd: 0.25,
      tanker_available: true,
      reason: null,
      evaluated_at: new Date().toISOString(),
    },
  ],
};

/* ----------------------------- PRICE MOCK ----------------------------- */

export const mockPrices: PricesResponse = {
  brent_usd: 82.14,
  wti_usd: 78.92,
  brent_change_pct_24h: 1.3,
  wti_change_pct_24h: 0.9,
  fetched_at: new Date().toISOString(),
  source: "yfinance",
};

/* -------------------------- AGENT STATUS MOCK -------------------------- */

export const mockAgentStatus: AgentsStatusResponse = {
  agents: {
    agent_1: {
      status: "RUNNING",
      last_run: new Date().toISOString(),
      events_today: 47,
    },
    agent_2: {
      status: "IDLE",
      last_run: new Date().toISOString(),
      queue_depth: 0,
    },
    agent_3: {
      status: "IDLE",
      last_run: new Date().toISOString(),
    },
    agent_4: {
      status: "INACTIVE",
      note: "Activates in crisis mode only",
    },
    agent_5: {
      status: "INACTIVE",
      note: "Activates in crisis mode only",
    },
    agent_6: {
      status: "INACTIVE",
      note: "Activates in crisis mode only",
    },
    agent_7: {
      status: "INACTIVE",
      note: "Activates in crisis mode only",
    },
    agent_8: {
      status: "INACTIVE",
      note: "Activates in crisis mode only",
    },
  },
  redis_stream_depths: {
    "events:raw": 0,
    "events:verified": 0,
  },
    crisis_mode_active: false,   // ← add this
    system_mode: "WATCH",
};

/* ----------------------------- VESSELS MOCK ---------------------------- */

export const mockVessels: VesselsResponse = {
  vessels: [
    {
      mmsi: "123456789",
      name: "VLCC PACIFIC STAR",
      vessel_type: "VLCC",
      latitude: 24.5,
      longitude: 58.2,
      speed_knots: 13.2,
      heading_degrees: 112,
      last_updated: new Date().toISOString(),
    },
    {
      mmsi: "987654321",
      name: "VLCC GULF EAGLE",
      vessel_type: "VLCC",
      latitude: 22.1,
      longitude: 61.4,
      speed_knots: 11.8,
      heading_degrees: 134,
      last_updated: new Date().toISOString(),
    },
    {
      mmsi: "456789123",
      name: "SUEZMAX INDIA SPIRIT",
      vessel_type: "Suezmax",
      latitude: 19.8,
      longitude: 65.7,
      speed_knots: 14.1,
      heading_degrees: 98,
      last_updated: new Date().toISOString(),
    },
  ],
  cache_age_seconds: 180,
  source: "static_demo",
};
/* ─────────────────────────── PLAYBOOK MOCK ─────────────────────────────── */


// Signal detected at T+00:00, playbook ready at T+02:47
// These timestamps are the core demo claim — "167 seconds from signal to playbook"
const signalTime  = new Date(Date.now() - 167_000).toISOString();
const playbookTime = new Date().toISOString();

export const mockPlaybook: Playbook = {
  playbook_id:        "pb_20240115_001",
  status:             "pending_review",
  created_at:         playbookTime,
  signal_detected_at: signalTime,
  playbook_ready_at:  playbookTime,
  corridor_affected:  "Hormuz",
  compound_risk:      0.41,
  overall_confidence: 0.87,
  actions: [
    {
      action_id:                 "act_001",
      title:                     "Increase UAE Murban allocation",
      supplier:                  "UAE (ADNOC)",
      crude_grade:               "Murban",
      route:                     "Cape of Good Hope",
      confidence:                0.91,
      cost_delta_usd_per_barrel: 4.20,
      volume_mbd:                0.15,
      transit_days:              22,
      contract_reference:        "ADNOC-2024-IND-047",
      rationale:                 "Highest confidence option — Cape route unaffected, Murban compatible with Kochi and Jamnagar",
    },
    {
      action_id:                 "act_002",
      title:                     "Activate Saudi Arabia spot purchase",
      supplier:                  "Saudi Aramco",
      crude_grade:               "Arab Light",
      route:                     "Cape of Good Hope",
      confidence:                0.88,
      cost_delta_usd_per_barrel: 3.80,
      volume_mbd:                0.10,
      transit_days:              24,
      contract_reference:        "ARAMCO-2024-IND-112",
      rationale:                 "Diversification buffer — Arab Light accepted at all five Indian refineries",
    },
    {
      action_id:                 "act_003",
      title:                     "Partial Russia Urals top-up (headroom only)",
      supplier:                  "Rosneft / Nayara",
      crude_grade:               "Urals",
      route:                     "Direct (non-Hormuz)",
      confidence:                0.61,
      cost_delta_usd_per_barrel: 1.20,
      volume_mbd:                0.02,
      transit_days:              18,
      contract_reference:        "NAYARA-2024-RU-088",
      rationale:                 "Only 0.02 Mb/d headroom before MoPNG 40% cap — partial approval only",
    },
  ],
};