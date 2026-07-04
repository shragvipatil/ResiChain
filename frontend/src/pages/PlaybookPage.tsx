/**
 * PlaybookPage.tsx — Day 8 deliverable (Person C)
 *
 * Playbook approval UI:
 *   - Header: playbook status badge + the two timestamps (signal detected → playbook ready)
 *   - One card per action: title, supplier, route, confidence badge, cost delta, lead time
 *   - Approve / Reject buttons per card
 *   - Reject → text area for analyst note slides open
 *   - Submit → confirmation modal → PATCH /api/playbook/{id}/approve
 *   - Status badge updates to "Partially Approved" or "Fully Approved" after response
 *
 * The timestamp pair (signal_detected_at → playbook_ready_at) is the core demo claim.
 * Per CLAUDE.md: "Print it large on the playbook PDF. Show it in the Ministry dashboard.
 * Repeat it twice in the demo." — so it's prominent here.
 *
 * Day 13: no changes needed — just feed real API data.
 */

import React, { useEffect, useState, useCallback } from "react";
import { getPlaybook, approvePlaybook } from "../api/endpoints";
import { apiClient } from "../api/client";
import {
  Playbook, PlaybookAction, ActionDecision,
  PlaybookStatus, ApprovePlaybookRequest,
} from "../types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function elapsedSeconds(from: string, to: string): number {
  return Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000);
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toFixed(0).padStart(2, "0")}s`;
}

function statusConfig(status: PlaybookStatus) {
  switch (status) {
    case "fully_approved":    return { label: "Fully Approved",    cls: "bg-green-900/60 text-green-400 border-green-700" };
    case "partially_approved":return { label: "Partially Approved",cls: "bg-amber-900/60 text-amber-400 border-amber-700" };
    case "rejected":          return { label: "Rejected",          cls: "bg-red-900/60 text-red-400 border-red-700" };
    default:                  return { label: "Pending Review",    cls: "bg-slate-700 text-slate-300 border-slate-600" };
  }
}

function confidenceBadge(score: number) {
  const pct = Math.round(score * 100);
  const cls = score >= 0.8 ? "text-green-400 border-green-800 bg-green-900/40"
    : score >= 0.5          ? "text-amber-400 border-amber-800 bg-amber-900/40"
    :                         "text-red-400 border-red-800 bg-red-900/40";
  return (
    <span className={`text-xs px-2 py-0.5 rounded border font-medium tabular-nums ${cls}`}>
      {pct}%
    </span>
  );
}

// ── Decision state per action ─────────────────────────────────────────────────

interface ActionState {
  decision: ActionDecision;
  note:     string;
  noteOpen: boolean;
}

function initActionStates(actions: PlaybookAction[]): Record<string, ActionState> {
  return Object.fromEntries(
    actions.map((a) => [a.action_id, { decision: "pending", note: "", noteOpen: false }])
  );
}

// ── Action card ───────────────────────────────────────────────────────────────

interface ActionCardProps {
  action:   PlaybookAction;
  state:    ActionState;
  onChange: (id: string, patch: Partial<ActionState>) => void;
  disabled: boolean;
}

const ActionCard: React.FC<ActionCardProps> = ({ action, state, onChange, disabled }) => {
  const { decision, note, noteOpen } = state;

  const borderCls =
    decision === "approved" ? "border-green-700 bg-green-900/10"
    : decision === "rejected" ? "border-red-800 bg-red-900/10"
    : "border-slate-700 bg-slate-800";

  return (
    <div className={`rounded-xl border p-5 transition-all duration-300 ${borderCls}`}>
      {/* Card header */}
      <div className="flex items-start justify-between gap-4 mb-4">
        <div className="flex-1 min-w-0">
          <p className="text-white font-medium text-sm">{action.title}</p>
          <p className="text-slate-400 text-xs mt-0.5">{action.rationale}</p>
        </div>
        {confidenceBadge(action.confidence)}
      </div>

      {/* Detail grid */}
      <div className="grid grid-cols-2 gap-x-8 gap-y-2 mb-4 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-slate-500 w-20 shrink-0">Supplier</span>
          <span className="text-slate-200 font-medium">{action.supplier}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 w-20 shrink-0">Grade</span>
          <span className="text-slate-200">{action.crude_grade}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 w-20 shrink-0">Route</span>
          <span className="text-slate-200">{action.route}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 w-20 shrink-0">Volume</span>
          <span className="text-slate-200">{action.volume_mbd.toFixed(2)} Mb/d</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 w-20 shrink-0">Cost Δ</span>
          <span className="text-amber-400 font-medium">+${action.cost_delta_usd_per_barrel.toFixed(2)}/bbl</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 w-20 shrink-0">Lead time</span>
          <span className="text-slate-200">{action.transit_days}d</span>
        </div>
        <div className="flex items-center gap-2 col-span-2">
          <span className="text-slate-500 w-20 shrink-0">Contract</span>
          <span className="text-slate-400 font-mono text-xs">{action.contract_reference}</span>
        </div>
      </div>

      {/* Approve / Reject buttons */}
      <div className="flex items-center gap-3">
        <button
          disabled={disabled}
          onClick={() => onChange(action.action_id, { decision: "approved", noteOpen: false })}
          className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all
            ${decision === "approved"
              ? "bg-green-600 text-white border border-green-500"
              : "bg-slate-700 text-slate-300 border border-slate-600 hover:border-green-700 hover:text-green-400"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          {decision === "approved" && <span>✓</span>} Approve
        </button>

        <button
          disabled={disabled}
          onClick={() => onChange(action.action_id, {
            decision: "rejected",
            noteOpen: true,
          })}
          className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all
            ${decision === "rejected"
              ? "bg-red-700 text-white border border-red-600"
              : "bg-slate-700 text-slate-300 border border-slate-600 hover:border-red-700 hover:text-red-400"}
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          {decision === "rejected" && <span>✕</span>} Reject
        </button>

        {decision !== "pending" && (
          <button
            disabled={disabled}
            onClick={() => onChange(action.action_id, { decision: "pending", noteOpen: false, note: "" })}
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40"
          >
            Clear
          </button>
        )}
      </div>

      {/* Analyst note — slides open on reject */}
      {noteOpen && (
        <div className="mt-3">
          <label className="text-slate-500 text-xs block mb-1.5">
            Rejection reason <span className="text-slate-600">(optional but recommended)</span>
          </label>
          <textarea
            value={note}
            onChange={(e) => onChange(action.action_id, { note: e.target.value })}
            disabled={disabled}
            placeholder="e.g. Geopolitical exposure too high given current sanctions review…"
            rows={2}
            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-300 text-xs
              placeholder:text-slate-600 focus:outline-none focus:border-slate-500 resize-none
              disabled:opacity-40"
          />
        </div>
      )}
    </div>
  );
};

// ── Confirmation modal ────────────────────────────────────────────────────────

interface ConfirmModalProps {
  approved: number;
  rejected: number;
  pending:  number;
  onConfirm: () => void;
  onCancel:  () => void;
}

const ConfirmModal: React.FC<ConfirmModalProps> = ({ approved, rejected, pending, onConfirm, onCancel }) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 w-full max-w-md shadow-2xl">
      <h3 className="text-white font-medium text-base mb-1">Submit Playbook Decision</h3>
      <p className="text-slate-400 text-sm mb-5">This decision will be logged to the audit trail and cannot be undone.</p>

      <div className="bg-slate-900 rounded-xl p-4 mb-5 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">Approved actions</span>
          <span className="text-green-400 font-medium">{approved}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">Rejected actions</span>
          <span className="text-red-400 font-medium">{rejected}</span>
        </div>
        {pending > 0 && (
          <div className="flex justify-between text-sm">
            <span className="text-amber-400">Undecided (will submit as pending)</span>
            <span className="text-amber-400 font-medium">{pending}</span>
          </div>
        )}
      </div>

      <div className="flex gap-3">
        <button
          onClick={onCancel}
          className="flex-1 px-4 py-2.5 rounded-lg border border-slate-600 text-slate-300 text-sm hover:border-slate-400 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          className="flex-1 px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors"
        >
          Confirm & Submit
        </button>
      </div>
    </div>
  </div>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const PlaybookPage: React.FC = () => {
  const DEMO_PLAYBOOK_ID = "pb_20240115_001";
  // The PDF endpoint is real backend code (not mockable) — it always targets
  // the backend's own demo playbook id, independent of the frontend USE_MOCK flag.
  const BACKEND_PLAYBOOK_ID = "pb_001";

  const [playbook, setPlaybook]       = useState<Playbook | null>(null);
  const [loading, setLoading]         = useState(true);
  const [actionStates, setActionStates] = useState<Record<string, ActionState>>({});
  const [showModal, setShowModal]     = useState(false);
  const [submitting, setSubmitting]   = useState(false);
  const [submitted, setSubmitted]     = useState(false);
  const [downloadingRole, setDownloadingRole] = useState<"ministry" | "procurement" | null>(null);
  const [downloadError, setDownloadError]     = useState<string | null>(null);

  const handleDownloadPdf = useCallback(async (role: "ministry" | "procurement") => {
    setDownloadingRole(role);
    setDownloadError(null);
    try {
      const res = await apiClient.get(
        `/playbook/${BACKEND_PLAYBOOK_ID}/pdf`,
        { params: { role }, responseType: "blob" }
      );
      const blob = new Blob([res.data], { type: "application/pdf" });
      const url  = window.URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = `resichain_${role}_${BACKEND_PLAYBOOK_ID}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setDownloadError("Could not generate PDF — check backend connection");
    } finally {
      setDownloadingRole(null);
    }
  }, []);

  useEffect(() => {
    getPlaybook(DEMO_PLAYBOOK_ID).then((pb) => {
      setPlaybook(pb);
      setActionStates(initActionStates(pb.actions));
      setLoading(false);
    });
  }, []);

  const handleActionChange = useCallback((id: string, patch: Partial<ActionState>) => {
    setActionStates((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }, []);

  const decisions = Object.entries(actionStates).map(([action_id, s]) => ({
    action_id,
    decision: s.decision,
    note:     s.note || undefined,
  }));

  const approvedCount = decisions.filter((d) => d.decision === "approved").length;
  const rejectedCount = decisions.filter((d) => d.decision === "rejected").length;
  const pendingCount  = decisions.filter((d) => d.decision === "pending").length;

  const handleSubmit = async () => {
    if (!playbook) return;
    setSubmitting(true);
    setShowModal(false);
    try {
      const body: ApprovePlaybookRequest = { decisions };
      const res = await approvePlaybook(playbook.playbook_id, body);
      setPlaybook((prev) => prev ? { ...prev, status: res.status } : prev);
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-900 p-8">
        <div className="space-y-4">
          {[1,2,3].map((i) => (
            <div key={i} className="h-40 bg-slate-800 rounded-xl border border-slate-700 animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!playbook) return null;

  const sc       = statusConfig(playbook.status);
  const elapsed  = elapsedSeconds(playbook.signal_detected_at, playbook.playbook_ready_at);
  const allDecided = pendingCount === 0;

  return (
    <div className="min-h-screen bg-slate-900 p-8">

      {/* Confirmation modal */}
      {showModal && (
        <ConfirmModal
          approved={approvedCount}
          rejected={rejectedCount}
          pending={pendingCount}
          onConfirm={handleSubmit}
          onCancel={() => setShowModal(false)}
        />
      )}

      {/* Header */}
      <div className="mb-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-medium text-white">Playbook Review</h1>
            <p className="text-slate-400 text-sm mt-1">
              Corridor: <span className="text-white">{playbook.corridor_affected}</span>
              {" · "}Compound risk: <span className="text-amber-400">{(playbook.compound_risk * 100).toFixed(0)}%</span>
            </p>
          </div>
          <span className={`text-sm px-3 py-1.5 rounded-lg border font-medium ${sc.cls}`}>
            {sc.label}
          </span>
        </div>

        {/* PDF export buttons */}
        <div className="flex items-center gap-2 mt-3">
          <button
            onClick={() => handleDownloadPdf("ministry")}
            disabled={downloadingRole !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700
              text-slate-300 text-xs hover:border-blue-600 hover:text-blue-400 transition-colors
              disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {downloadingRole === "ministry" ? (
              <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            ) : "↓"} Ministry PDF
          </button>
          <button
            onClick={() => handleDownloadPdf("procurement")}
            disabled={downloadingRole !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700
              text-slate-300 text-xs hover:border-blue-600 hover:text-blue-400 transition-colors
              disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {downloadingRole === "procurement" ? (
              <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            ) : "↓"} Procurement PDF
          </button>
          {downloadError && (
            <span className="text-red-400 text-xs">{downloadError}</span>
          )}
        </div>

        {/* ── Timestamp pair — the core demo claim ─────────────────────────── */}
        <div className="mt-5 bg-slate-800 border border-slate-700 rounded-xl p-4 flex items-center gap-8">
          <div>
            <p className="text-slate-500 text-xs uppercase tracking-widest mb-1">Signal Detected</p>
            <p className="text-white text-sm tabular-nums font-medium">
              {new Date(playbook.signal_detected_at).toLocaleTimeString()}
            </p>
          </div>
          <div className="flex-1 flex items-center gap-2">
            <div className="flex-1 h-px bg-slate-700" />
            <span className="text-blue-400 text-sm font-semibold tabular-nums whitespace-nowrap">
              {formatElapsed(elapsed)}
            </span>
            <div className="flex-1 h-px bg-slate-700" />
          </div>
          <div>
            <p className="text-slate-500 text-xs uppercase tracking-widest mb-1">Playbook Ready</p>
            <p className="text-white text-sm tabular-nums font-medium">
              {new Date(playbook.playbook_ready_at).toLocaleTimeString()}
            </p>
          </div>
          <div className="border-l border-slate-700 pl-8">
            <p className="text-slate-500 text-xs uppercase tracking-widest mb-1">Confidence</p>
            <p className="text-white text-sm font-semibold">{(playbook.overall_confidence * 100).toFixed(0)}%</p>
          </div>
        </div>
      </div>

      {/* Action cards */}
      <div className="space-y-4 mb-8">
        {playbook.actions.map((action) => (
          <ActionCard
            key={action.action_id}
            action={action}
            state={actionStates[action.action_id] ?? { decision: "pending", note: "", noteOpen: false }}
            onChange={handleActionChange}
            disabled={submitted || submitting}
          />
        ))}
      </div>

      {/* Submit footer */}
      {!submitted ? (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 flex items-center justify-between">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-green-400">{approvedCount} approved</span>
            <span className="text-red-400">{rejectedCount} rejected</span>
            {pendingCount > 0 && <span className="text-slate-500">{pendingCount} undecided</span>}
          </div>
          <button
            onClick={() => setShowModal(true)}
            disabled={submitting || (approvedCount === 0 && rejectedCount === 0)}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed
              text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
          >
            {submitting && (
              <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            )}
            {submitting ? "Submitting…" : "Submit Decision"}
          </button>
        </div>
      ) : (
        <div className="bg-green-900/20 border border-green-800 rounded-xl p-5 text-center">
          <p className="text-green-400 font-medium">
            Decision submitted — playbook is now{" "}
            <span className="font-semibold">{statusConfig(playbook.status).label}</span>
          </p>
          <p className="text-slate-500 text-xs mt-1">Logged to audit trail · use the PDF buttons above to export</p>
        </div>
      )}
    </div>
  );
};

export default PlaybookPage;