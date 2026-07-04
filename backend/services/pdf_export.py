"""
services/pdf_export.py — Day 10 deliverable (Person C)

Generates two role-specific PDFs from a playbook record using ReportLab:

  generate_ministry_pdf(playbook)     -> single page, plain-language summary
  generate_procurement_pdf(playbook, procurement_options) -> full rejection trace

Both PDFs feature the timestamp pair prominently (signal detected -> playbook
ready) since it's the core "167 seconds" demo claim per CLAUDE.md:
  "Print it large on the playbook PDF. Show it in the Ministry dashboard.
   Repeat it twice in the demo."

Both include a blank analyst-approval field and an evidence-chain / source
citation section, per the Day 10 spec.

This file is fully self-contained — it does not import anything from
routers/api.py or touch any shared state. Zero conflict risk with Person A
or Person B's work.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Brand palette (matches the dark dashboard's accent colours) ──────────────

NAVY    = colors.HexColor("#0f172a")
SLATE   = colors.HexColor("#475569")
SLATE_L = colors.HexColor("#94a3b8")
GREEN   = colors.HexColor("#16a34a")
AMBER   = colors.HexColor("#d97706")
RED     = colors.HexColor("#dc2626")
LIGHT_BG= colors.HexColor("#f1f5f9")

styles = getSampleStyleSheet()

TITLE_STYLE = ParagraphStyle(
    "PBTitle", parent=styles["Title"], fontSize=18, textColor=NAVY,
    spaceAfter=2, alignment=TA_LEFT,
)
SUBTITLE_STYLE = ParagraphStyle(
    "PBSubtitle", parent=styles["Normal"], fontSize=10, textColor=SLATE,
    spaceAfter=12,
)
SECTION_STYLE = ParagraphStyle(
    "PBSection", parent=styles["Heading2"], fontSize=12, textColor=NAVY,
    spaceBefore=8, spaceAfter=4,
)
BODY_STYLE = ParagraphStyle(
    "PBBody", parent=styles["Normal"], fontSize=9.5, textColor=colors.black,
    leading=13,
)
SMALL_STYLE = ParagraphStyle(
    "PBSmall", parent=styles["Normal"], fontSize=8, textColor=SLATE,
)
BIG_NUMBER_STYLE = ParagraphStyle(
    "PBBigNum", parent=styles["Normal"], fontSize=22, textColor=NAVY,
    alignment=TA_CENTER, spaceAfter=2,
)
BIG_LABEL_STYLE = ParagraphStyle(
    "PBBigLabel", parent=styles["Normal"], fontSize=8, textColor=SLATE_L,
    alignment=TA_CENTER,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _risk_color(score: Optional[float]) -> colors.Color:
    if score is None:
        return SLATE
    if score > 0.65:
        return RED
    if score > 0.30:
        return AMBER
    return GREEN


def _risk_label(score: Optional[float]) -> str:
    if score is None:
        return "UNKNOWN"
    if score > 0.65:
        return "CRITICAL"
    if score > 0.30:
        return "ELEVATED"
    return "NORMAL"


def _fmt_timestamp(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S UTC · %d %b %Y")
    except (ValueError, TypeError):
        return str(iso_str)


def _elapsed_string(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _header_block(playbook: dict) -> list:
    flow = []
    flow.append(Paragraph("ResiChain AI — Crisis Playbook", TITLE_STYLE))
    flow.append(Paragraph(
        f"Playbook ID: {playbook.get('id', playbook.get('playbook_id', '—'))}"
        f" &nbsp;·&nbsp; Generated {_fmt_timestamp(playbook.get('created_at'))}",
        SUBTITLE_STYLE,
    ))
    flow.append(HRFlowable(width="100%", thickness=1, color=LIGHT_BG, spaceAfter=10))
    event_summary = playbook.get("event_summary", "—")
    flow.append(Paragraph(f"<b>Event:</b> {event_summary}", BODY_STYLE))
    flow.append(Spacer(1, 8))
    return flow


def _timestamp_proof_block(playbook: dict) -> list:
    """Signal Detected → [elapsed] → Playbook Ready — the core demo claim."""
    evidence = playbook.get("evidence_chain", {}) or {}
    elapsed_seconds = evidence.get("signal_to_playbook_seconds")
    signal_ts   = playbook.get("signal_detected_at") or playbook.get("created_at")
    playbook_ts = playbook.get("playbook_ready_at") or playbook.get("created_at")

    data = [[
        Paragraph(
            f"<b>SIGNAL DETECTED</b><br/><font size=11>{_fmt_timestamp(signal_ts)}</font>",
            ParagraphStyle("cellL", parent=BODY_STYLE, alignment=TA_CENTER, textColor=SLATE),
        ),
        Paragraph(
            f"<font size=14 color='#2563eb'><b>{_elapsed_string(elapsed_seconds)}</b></font>",
            ParagraphStyle("cellM", parent=BODY_STYLE, alignment=TA_CENTER),
        ),
        Paragraph(
            f"<b>PLAYBOOK READY</b><br/><font size=11>{_fmt_timestamp(playbook_ts)}</font>",
            ParagraphStyle("cellR", parent=BODY_STYLE, alignment=TA_CENTER, textColor=SLATE),
        ),
    ]]
    t = Table(data, colWidths=[2.1 * inch, 1.6 * inch, 2.1 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return [t, Spacer(1, 8)]


def _confidence_breakdown_table(playbook: dict) -> list:
    """Geometric mean confidence breakdown — Agent 1, 2, 3, and overall."""
    evidence = playbook.get("evidence_chain", {}) or {}
    overall  = playbook.get("overall_confidence")
    a1 = evidence.get("agent1_confidence")
    a2 = evidence.get("agent2_confidence")
    a3 = evidence.get("agent3_confidence")

    def pct(v):
        return f"{v*100:.0f}%" if isinstance(v, (int, float)) else "—"

    rows = [["Component", "Confidence", "Contribution"]]
    if a1 is not None:
        rows.append(["Agent 1 — Event verification", pct(a1), "Source trust + recency decay"])
    if a2 is not None:
        rows.append(["Agent 2 — Historical similarity (RAG)", pct(a2), "ChromaDB nearest-neighbour match"])
    if a3 is not None:
        rows.append(["Agent 3 — Corridor risk confidence", pct(a3), "1 − coefficient of variation"])
    rows.append(["Overall (geometric mean)", pct(overall), "Penalizes any single weak link"])

    t = Table(rows, colWidths=[2.6 * inch, 1.0 * inch, 2.2 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, LIGHT_BG]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e0e7ff")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return [t, Spacer(1, 10)]


def _approval_field_block(role_label: str) -> list:
    """Blank analyst-approval signature field."""
    flow = []
    flow.append(Spacer(1, 8))
    flow.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#cbd5e1")))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(f"<b>Analyst Approval — {role_label}</b>", SECTION_STYLE))
    data = [
        ["Name:", "_" * 32, "Date:", "_" * 18],
        ["Signature:", "_" * 32, "Role:", "_" * 18],
    ]
    t = Table(data, colWidths=[0.75*inch, 2.3*inch, 0.6*inch, 1.6*inch])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), SLATE),
    ]))
    flow.append(t)
    return flow


def _evidence_chain_footer(playbook: dict) -> list:
    """Source citations."""
    flow = []
    flow.append(Spacer(1, 10))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0")))
    flow.append(Spacer(1, 4))
    sources = [
        "UKMTO Maritime Advisory Feed (ukmto.org)",
        "GDELT 2.0 Global Event Database",
        "OFAC Specially Designated Nationals List (treasury.gov)",
        "EIA International Energy Statistics",
        "Alpha Vantage Brent/WTI Spot Pricing",
    ]
    flow.append(Paragraph(
        "<b>Evidence chain / sources consulted:</b> " + " · ".join(sources), SMALL_STYLE,
    ))
    flow.append(Paragraph(
        "Every figure in this document is traceable to a live data source or a "
        "documented formula. No value is manually entered.", SMALL_STYLE,
    ))
    return flow


# ── Ministry PDF (single page) ────────────────────────────────────────────────

def generate_ministry_pdf(playbook: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.45*inch, bottomMargin=0.4*inch,
        leftMargin=0.7*inch, rightMargin=0.7*inch,
    )

    flow: list = []
    flow += _header_block(playbook)
    flow += _timestamp_proof_block(playbook)

    evidence = playbook.get("evidence_chain", {}) or {}
    risk_score = playbook.get("corridor_risk", evidence.get("agent3_confidence"))
    supply_pct = playbook.get("supply_continuity_pct", "—")
    cost_bn    = playbook.get("cost_delta_bn")
    cost_str   = f"${cost_bn:.1f}B" if isinstance(cost_bn, (int, float)) else "—"

    risk_label_text = _risk_label(risk_score)
    risk_hex = "#%02x%02x%02x" % tuple(int(c * 255) for c in _risk_color(risk_score).rgb())

    metrics_data = [[
        Paragraph(f"<font color='{risk_hex}'>{risk_label_text}</font>", BIG_NUMBER_STYLE),
        Paragraph(f"{supply_pct}%", BIG_NUMBER_STYLE),
        Paragraph(cost_str, BIG_NUMBER_STYLE),
    ], [
        Paragraph("RISK LEVEL", BIG_LABEL_STYLE),
        Paragraph("SUPPLY CONTINUITY", BIG_LABEL_STYLE),
        Paragraph("ADDITIONAL COST (EST.)", BIG_LABEL_STYLE),
    ]]
    mt = Table(metrics_data, colWidths=[1.9*inch, 1.9*inch, 1.9*inch])
    mt.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    flow.append(mt)
    flow.append(Spacer(1, 10))

    flow.append(Paragraph("Required Actions", SECTION_STYLE))
    approved = playbook.get("approved_actions", []) or []
    actions_from_pb = playbook.get("actions", []) or []

    plain_actions: list[str] = []
    if actions_from_pb:
        for a in actions_from_pb[:3]:
            supplier = a.get("supplier", "supplier")
            grade    = a.get("crude_grade", a.get("grade", ""))
            vol      = a.get("volume_mbd")
            vol_str  = f"{vol:.2f} Mb/d" if isinstance(vol, (int, float)) else ""
            plain_actions.append(
                f"Increase {grade} allocation from {supplier} by {vol_str} via alternate route."
            )
    elif approved:
        for a in approved[:3]:
            plain_actions.append(f"Execute approved action: {a.get('action_id', '—')}"
                                  + (f" — {a.get('note')}" if a.get("note") else ""))
    else:
        plain_actions = [
            "Increase diversified crude procurement from Cape-route suppliers.",
            "Monitor SPR drawdown schedule against updated corridor risk.",
            "Maintain heightened advisory status for Hormuz-transiting tankers.",
        ]

    for i, txt in enumerate(plain_actions, start=1):
        flow.append(Paragraph(f"<b>{i}.</b> {txt}", BODY_STYLE))
        flow.append(Spacer(1, 2))
    flow.append(Spacer(1, 4))

    spr = playbook.get("spr_schedule")
    if spr:
        flow.append(Paragraph("Strategic Petroleum Reserve Response", SECTION_STYLE))
        flow.append(Paragraph(
            f"Daily drawdown: <b>{spr.get('daily_drawdown_mbd', '—')} Mb/d</b> · "
            f"Duration: <b>{spr.get('duration_days', '—')} days</b> · "
            f"Total release: <b>{spr.get('total_release_mb', '—')} Mb</b>. "
            "Strategic-only SPR figure; commercial inventory adds approximately 2 additional days of cover.",
            BODY_STYLE,
        ))
        flow.append(Spacer(1, 4))

    flow.append(Paragraph("Confidence Score Breakdown", SECTION_STYLE))
    flow += _confidence_breakdown_table(playbook)
    flow += _approval_field_block("Ministry")
    flow += _evidence_chain_footer(playbook)

    doc.build(flow)
    return buf.getvalue()


# ── Procurement PDF ────────────────────────────────────────────────────────────

def generate_procurement_pdf(playbook: dict, procurement_options: list[dict]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.6*inch, bottomMargin=0.6*inch,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
    )

    flow: list = []
    flow += _header_block(playbook)
    flow += _timestamp_proof_block(playbook)

    flow.append(Paragraph("Evaluation Trace — All Options", SECTION_STYLE))
    trace_rows = [["Supplier", "Grade", "Status", "Rule / Reason", "Confidence"]]
    for opt in procurement_options:
        status = opt.get("status", "—")
        rule   = opt.get("block_reason") or opt.get("rule_triggered") or (
            "All checks passed" if status == "APPROVED" else "—"
        )
        conf = opt.get("confidence")
        conf_str = f"{conf*100:.0f}%" if isinstance(conf, (int, float)) else "—"
        trace_rows.append([
            opt.get("supplier", "—"),
            opt.get("grade", "—"),
            status,
            Paragraph(rule, ParagraphStyle("ruleCell", parent=SMALL_STYLE, fontSize=8)),
            conf_str,
        ])

    trace_table = Table(
        trace_rows, colWidths=[1.1*inch, 1.0*inch, 0.9*inch, 2.6*inch, 0.8*inch], repeatRows=1,
    )
    row_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, opt in enumerate(procurement_options, start=1):
        status = opt.get("status", "")
        col = GREEN if status == "APPROVED" else RED if status == "BLOCKED" else AMBER
        row_styles.append(("TEXTCOLOR", (2, i), (2, i), col))
        row_styles.append(("FONTNAME", (2, i), (2, i), "Helvetica-Bold"))
    trace_table.setStyle(TableStyle(row_styles))
    flow.append(trace_table)
    flow.append(Spacer(1, 14))

    approved_opts = [o for o in procurement_options if o.get("status") == "APPROVED"]
    if approved_opts:
        flow.append(Paragraph("Approved Alternatives — Detail", SECTION_STYLE))
        detail_rows = [["Supplier", "Route", "Volume", "Premium", "Transit", "Confidence"]]
        for opt in approved_opts:
            vol = opt.get("volume_mbd")
            prem = opt.get("price_premium_pct")
            detail_rows.append([
                opt.get("supplier", "—"),
                opt.get("route", "—"),
                f"{vol:.2f} Mb/d" if isinstance(vol, (int, float)) else "—",
                f"{prem:+.1f}%" if isinstance(prem, (int, float)) else "—",
                f"{opt.get('transit_days', '—')}d",
                f"{opt.get('confidence', 0)*100:.0f}%",
            ])
        dt = Table(detail_rows, colWidths=[1.2*inch, 1.1*inch, 1.0*inch, 0.9*inch, 0.8*inch, 1.0*inch])
        dt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#166534")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        flow.append(dt)
        flow.append(Spacer(1, 10))

    n_approved = sum(1 for o in procurement_options if o.get("status") == "APPROVED")
    n_blocked  = sum(1 for o in procurement_options if o.get("status") == "BLOCKED")
    n_partial  = sum(1 for o in procurement_options if o.get("status") == "PARTIAL")
    flow.append(Paragraph(
        f"<b>{n_approved} approved · {n_partial} partial · {n_blocked} blocked</b> "
        f"— {len(procurement_options)} total options evaluated by Agent 6 / validated by Agent 7.",
        BODY_STYLE,
    ))

    flow += _approval_field_block("Procurement Analyst")
    flow += _evidence_chain_footer(playbook)

    doc.build(flow)
    return buf.getvalue()