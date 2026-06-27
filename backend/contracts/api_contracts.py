# ============================================================
# ResiChain — API Contracts
# These are the exact shapes Person C builds against
# ============================================================

MOCK_RISK_STATE = {
    "corridors": {
        "Hormuz": {"risk_score": 0.34, "status": "WATCH", "trend": "stable"},
        "Red_Sea": {"risk_score": 0.41, "status": "WATCH", "trend": "rising"},
        "Suez": {"risk_score": 0.18, "status": "NORMAL", "trend": "stable"},
        "Cape": {"risk_score": 0.05, "status": "NORMAL", "trend": "stable"}
    },
    "compound_risk": 0.52,
    "last_updated": "2026-06-27T07:00:00Z",
    "system_mode": "WATCH"
}

MOCK_EVENTS = [
    {
        "id": "evt_001",
        "timestamp": "2026-06-27T06:23:00Z",
        "source": "GDELT",
        "headline": "Iran signals readiness to restrict Hormuz passage",
        "severity": 6,
        "corridor": "Hormuz",
        "stage": "WATCH",
        "confidence": 0.71
    },
    {
        "id": "evt_002",
        "timestamp": "2026-06-27T05:41:00Z",
        "source": "UKMTO",
        "headline": "Maritime advisory issued for Red Sea shipping lanes",
        "severity": 5,
        "corridor": "Red_Sea",
        "stage": "WATCH",
        "confidence": 0.88
    }
]

MOCK_PROCUREMENT_OPTIONS = [
    {
        "id": "proc_001",
        "supplier": "UAE",
        "grade": "Murban",
        "volume_mbd": 0.15,
        "price_premium_pct": 4.2,
        "transit_days": 7,
        "route": "Cape",
        "status": "APPROVED",
        "compatible_refineries": ["Jamnagar RIL", "Vadinar Nayara"],
        "sanctions_clear": True,
        "confidence": 0.91
    },
    {
        "id": "proc_002",
        "supplier": "Saudi Arabia",
        "grade": "Arab Light",
        "volume_mbd": 0.20,
        "price_premium_pct": 6.8,
        "transit_days": 19,
        "route": "Cape",
        "status": "APPROVED",
        "compatible_refineries": ["Jamnagar RIL", "Kochi BPCL", "Paradip IOCL"],
        "sanctions_clear": True,
        "confidence": 0.87
    },
    {
        "id": "proc_003",
        "supplier": "Iran",
        "grade": "Iranian Heavy",
        "volume_mbd": 0.10,
        "price_premium_pct": -8.0,
        "transit_days": 3,
        "route": "Hormuz",
        "status": "BLOCKED",
        "block_reason": "OFAC SDN match — IRAN program",
        "sanctions_clear": False,
        "confidence": 0.0
    }
]

MOCK_PLAYBOOK = {
    "id": "pb_001",
    "created_at": "2026-06-27T07:15:00Z",
    "status": "pending",
    "event_summary": "Hormuz partial closure risk elevated to 82%",
    "overall_confidence": 0.84,
    "cost_delta_bn": 2.1,
    "supply_continuity_pct": 78,
    "spr_schedule": {
        "daily_drawdown_mbd": 0.3,
        "duration_days": 12,
        "total_release_mb": 3.6
    },
    "approved_actions": [],
    "rejected_actions": [],
    "evidence_chain": {
        "agent1_confidence": 0.91,
        "agent2_confidence": 0.88,
        "agent3_confidence": 0.82,
        "signal_to_playbook_seconds": 187
    }
}

MOCK_AGENT_STATUS = [
    {
        "agent": "Agent1_Ingestion",
        "status": "running",
        "last_run": "2026-06-27T07:04:00Z",
        "next_run": "2026-06-27T07:09:00Z",
        "events_processed": 142,
        "mode": "WATCH"
    },
    {
        "agent": "Agent2_Extraction",
        "status": "idle",
        "last_run": "2026-06-27T07:04:12Z",
        "events_extracted": 3,
        "mode": "WATCH"
    },
    {
        "agent": "Agent3_RiskEngine",
        "status": "idle",
        "last_run": "2026-06-27T07:04:15Z",
        "risk_scores_updated": 4,
        "mode": "WATCH"
    },
    {
        "agent": "Agent4_Compound",
        "status": "standby",
        "last_run": None,
        "mode": "STANDBY"
    },
    {
        "agent": "Agent5_SPR",
        "status": "standby",
        "last_run": None,
        "mode": "STANDBY"
    },
    {
        "agent": "Agent6_Procurement",
        "status": "standby",
        "last_run": None,
        "mode": "STANDBY"
    },
    {
        "agent": "Agent7_Validator",
        "status": "standby",
        "last_run": None,
        "mode": "STANDBY"
    },
    {
        "agent": "Agent8_Playbook",
        "status": "standby",
        "last_run": None,
        "mode": "STANDBY"
    }
]

MOCK_VESSELS = [
    {
        "mmsi": "477123456",
        "name": "GULF CARRIER",
        "lat": 24.5,
        "lon": 56.3,
        "speed": 12.4,
        "destination": "SIKKA",
        "vessel_type": "crude_tanker"
    },
    {
        "mmsi": "477234567",
        "name": "ARABIAN STAR",
        "lat": 22.1,
        "lon": 60.2,
        "speed": 11.8,
        "destination": "VADINAR",
        "vessel_type": "crude_tanker"
    },
    {
        "mmsi": "477345678",
        "name": "INDIA SPIRIT",
        "lat": 19.8,
        "lon": 63.4,
        "speed": 13.1,
        "destination": "PARADIP",
        "vessel_type": "crude_tanker"
    }
]

MOCK_KGRAPH = {
    "nodes": [
        {"id": "s1", "label": "Saudi Arabia", "type": "Supplier", "share": 18},
        {"id": "s2", "label": "Iraq", "type": "Supplier", "share": 22},
        {"id": "s3", "label": "Russia", "type": "Supplier", "share": 22},
        {"id": "s4", "label": "UAE", "type": "Supplier", "share": 8},
        {"id": "c1", "label": "Hormuz", "type": "Chokepoint", "risk": 0.34},
        {"id": "c2", "label": "Red_Sea", "type": "Chokepoint", "risk": 0.41},
        {"id": "c3", "label": "Cape", "type": "Chokepoint", "risk": 0.05},
        {"id": "r1", "label": "Jamnagar RIL", "type": "Refinery"},
        {"id": "r2", "label": "Kochi BPCL", "type": "Refinery"},
        {"id": "g1", "label": "Arab Light", "type": "CrudeGrade"},
        {"id": "g2", "label": "Murban", "type": "CrudeGrade"}
    ],
    "edges": [
        {"from": "s1", "to": "c1", "label": "SHIPS_VIA"},
        {"from": "s2", "to": "c1", "label": "SHIPS_VIA"},
        {"from": "s4", "to": "c1", "label": "SHIPS_VIA"},
        {"from": "s1", "to": "g1", "label": "PRODUCES"},
        {"from": "s4", "to": "g2", "label": "PRODUCES"},
        {"from": "g1", "to": "r1", "label": "COMPATIBLE_WITH"},
        {"from": "g1", "to": "r2", "label": "COMPATIBLE_WITH"},
        {"from": "g2", "to": "r1", "label": "COMPATIBLE_WITH"}
    ]
} 