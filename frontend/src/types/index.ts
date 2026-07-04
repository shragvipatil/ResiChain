export interface CorridorDetail {
  risk_score: number;
  status: "NORMAL" | "WATCH" | "CRISIS";
  trend: "stable" | "rising" | "falling";
}

export interface CorridorRiskState {
  corridors: {
    Hormuz: CorridorDetail;
    Red_Sea: CorridorDetail;
    Suez: CorridorDetail;
    Cape: CorridorDetail;
  };
  compound_risk: number;
  last_updated: string;
  system_mode: "NORMAL" | "WATCH" | "CRISIS";
}

export type SourceName =
  | "GDELT"
  | "UKMTO"
  | "OFAC"
  | "RELIEFWEB"
  | "ALPHA_VANTAGE";

export type CorridorName =
  | "Hormuz"
  | "Red_Sea"
  | "Suez"
  | "Cape"
  | "Unknown";

export type EventStage = "WATCH" | "CONFIRMED";

export type OptionStatus = "APPROVED" | "BLOCKED" | "PARTIAL";

export type AgentStatus = "RUNNING" | "IDLE" | "INACTIVE" | "ERROR";

export interface VerifiedEvent {
  event_id: string;
  event: string;
  source: SourceName;
  sources_confirming: SourceName[];
  location: string;
  corridor: CorridorName;
  severity: number;
  stage: EventStage;
  confidence: number;
  event_timestamp: string;
  verified_at: string;
  hours_since_event: number;
}

export interface ProcurementOption {
  option_id: string;
  supplier: string;
  crude_grade: string;
  status: OptionStatus;
  confidence: number;
  rule_triggered?: string;
  reason: {
    rule: string;
    value: string;
    threshold: string | null;
    source: string;
    headroom_mbd?: number;
  } | null;
  route?: string;
  transit_days?: number;
  cost_delta_usd_per_barrel?: number;
  volume_mbd?: number;
  tanker_available?: boolean;
  max_allowed_delta_mbd?: number;
  evaluated_at: string;
}

export interface ProcurementResponse {
  evaluated_at: string;
  surviving_corridors: CorridorName[];
  options: ProcurementOption[];
}

export interface PricesResponse {
  brent_usd: number;
  wti_usd: number;
  brent_change_pct_24h: number;
  wti_change_pct_24h: number;
  fetched_at: string;
  source: string;
}

export interface Vessel {
  mmsi: string;
  name: string;
  vessel_type: string;
  latitude: number;
  longitude: number;
  speed_knots: number;
  heading_degrees: number;
  last_updated: string;
}

export interface VesselsResponse {
  vessels: Vessel[];
  cache_age_seconds: number;
  source: string;
}

export interface AgentInfo {
  status: AgentStatus;
  last_run?: string;
  events_today?: number;
  queue_depth?: number;
  note?: string;
}

export interface AgentsStatusResponse {
  agents: Record<string, AgentInfo>;
  redis_stream_depths: {
    "events:raw": number;
    "events:verified": number;
  };
  crisis_mode_active: boolean;        // ← add this back
  system_mode: "NORMAL" | "WATCH" | "CRISIS";
}
export type WebSocketEventType =
  | "RISK_STATE_UPDATED"
  | "WATCH_ALERT"
  | "CONFIRMED_ALERT"
  | "AGENT_STARTED"
  | "AGENT_COMPLETED"
  | "COMPOUND_DISRUPTION_DETECTED"
  | "PLAYBOOK_READY";

export interface WebSocketEvent {
  type: WebSocketEventType;
  payload: Record<string, unknown>;
}
// ── Playbook types (Day 8) ────────────────────────────────────────────────────

export type PlaybookStatus =
  | "pending_review"
  | "partially_approved"
  | "fully_approved"
  | "rejected";

export type ActionDecision = "approved" | "rejected" | "pending";

export interface PlaybookAction {
  action_id:                  string;
  title:                      string;
  supplier:                   string;
  crude_grade:                string;
  route:                      string;
  confidence:                 number;
  cost_delta_usd_per_barrel:  number;
  volume_mbd:                 number;
  transit_days:               number;
  contract_reference:         string;
  rationale:                  string;       // one-line reason this was recommended
}

export interface Playbook {
  playbook_id:        string;
  status:             PlaybookStatus;
  created_at:         string;
  signal_detected_at: string;             // T+00:00 — when Agent 1 first saw the event
  playbook_ready_at:  string;             // T+02:47 — the timestamp pair claim
  corridor_affected:  string;
  compound_risk:      number;
  overall_confidence: number;
  actions:            PlaybookAction[];
  analyst_notes?:     string;
}

export interface ApprovePlaybookRequest {
  decisions: {
    action_id: string;
    decision:  ActionDecision;
    note?:     string;
  }[];
}

export interface ApprovePlaybookResponse {
  playbook_id: string;
  status:      PlaybookStatus;
  updated_at:  string;
}
// ── Knowledge Graph types (Day 9) ─────────────────────────────────────────────

export type KNodeType = "Supplier" | "CrudeGrade" | "Route" | "Chokepoint" | "Refinery";

export interface KNode {
  id:       string;
  label:    string;
  type:     KNodeType;
  // Optional properties shown in detail panel
  share?:   number;   // Supplier: import share %
  risk?:    number;   // Chokepoint: current risk score 0–1
  capacity?:number;   // Refinery: Mb/d
  gravity?: number;   // CrudeGrade: API gravity
  [key: string]: unknown;
}

export interface KEdge {
  from:   string;
  to:     string;
  label:  string;      // SHIPS_VIA, PRODUCES, COMPATIBLE_WITH, etc.
}

export interface KGraphData {
  nodes: KNode[];
  edges: KEdge[];
}
// ── Auth types (Day 11) ────────────────────────────────────────────────────────

export type UserRole =
  | "MINISTRY_USER"
  | "PROCUREMENT_ANALYST"
  | "REFINERY_OPERATOR"
  | "VIEWER"
  | "ADMIN";

export interface User {
  user_id:  string;
  name:     string;
  email:    string;
  role:     UserRole;
}

export interface LoginRequest {
  email:    string;
  password: string;
  totp_code?: string;   // required for ADMIN / MINISTRY_USER (2FA, Day 11 backend)
}

export interface LoginResponse {
  user: User;
  // No token field — the real access token is set server-side as an
  // httpOnly cookie via Set-Cookie header. It is never exposed to JS.
  requires_totp?: boolean;  // true if password was correct but TOTP still needed
}