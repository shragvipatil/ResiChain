/**
 * PlaybookPage.tsx — Day 8 deliverable (Person C)
 *
 * Day 20 fix: now wrapped in AppLayout like every other page, instead
 * of its own standalone header. That standalone header had no nav
 * links at all — only a bare "Logout" button — which meant there was
 * genuinely no way back to another page except the browser's back
 * button. AppLayout's shared header already provides nav links, the
 * wordmark-as-home-link, and sign-out, so this page's own duplicate
 * name/logout block is removed to avoid two logout controls on screen.
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
 */

import React, { useEffect, useState, useCallback } from "react";
import { getPlaybook, approvePlaybook } from "../api/endpoints";
import { apiClient } from "../api/client";
import AppLayout from "../components/AppLayout";
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
    case "fully_approved":    return { label: "Fully Approved",    cls: "bg-status-normal/20 text-status-normal border-status-normal/40" };
    case "partially_approved":return { label: "Partially Approved",cls: "bg-status-caution/20 text-status-caution border-status-caution/40" };
    case "rejected":          return { label: "Rejected",          cls: "bg-status-critical/20 text-status-critical border-status-critical/40" };
    default:                  return { label: "Pending Review",    cls: "bg-slate-700 text-slate-300 border-slate-600" };
  }
}

function confidenceBadge(score: number) {
  const pct = Math.round(score * 100);
  const cls = score >= 0.8 ? "text-status-normal border-status-normal/40 bg-status-normal/10"
    : score >= 0.5          ? "text-status-caution border-status-caution/40 bg-status-caution/10"
    :                         "text-status-critical border-status-critical/40 bg-status-critical/10";
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
    decision === "approved" ? "border-status-normal/60 bg-status-normal/5"
    : decision === "rejected" ? "border-status-critical/60 bg-status-critical/5"
    : "border-chart-hairline bg-chart-panel";

  return (
    <div className={`rounded-xl border p-5 transition-all duration-300 ${borderCls}`}>
      <div className="flex items-start justify-between gap-4 mb-4">
        <div className="flex-1 min-w-0">
          <p className="text-white font-medium text-sm">{action.title}</p>
          <p className="text-slate-400 text-xs mt-0.5">{action.rationale}</p>
        </div>
        {confidenceBadge(action.confidence)}
      </div>

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
          <span className="text-status-caution font-medium">+${action.cost_delta_usd_per_barrel.toFixed(2)}/bbl</span>
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

      <div className="flex items-center gap-3">
        <button
          disabled={disabled}
          onClick={() => onChange(action.action_id, { decision: "approved", noteOpen: false })}
          className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all
            ${decision === "approved"
              ? "bg-status-normal text-white border border-status-normal"
              : "bg-slate-700 text-slate-300 border border-slate-600 hover:border-status-normal hover:text-status-normal"}
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
              ? "bg-status-critical text-white border border-status-critical"
              : "bg-slate-700 text-slate-300 border border-slate-600 hover:border-status-critical hover:text-status-critical"}
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
            className="w-full bg-chart-navy border border-chart-hairline rounded-lg px-3 py-2 text-slate-300 text-xs
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
    <div className="bg-chart-panel border border-chart-hairline rounded-2xl p-6 w-full max-w-md shadow-2xl">
      <h3 className="text-white font-medium text-base mb-1">Submit Playbook Decision</h3>
      <p className="text-slate-400 text-sm mb-5">This decision will be logged to the audit trail and cannot be undone.</p>

      <div className="bg-chart-navy rounded-xl p-4 mb-5 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">Approved actions</span>
          <span className="text-status-normal font-medium">{approved}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">Rejected actions</span>
          <span className="text-status-critical font-medium">{rejected}</span>
        </div>
        {pending > 0 && (
          <div className="flex justify-between text-sm">
            <span className="text-status-caution">Undecided (will submit as pending)</span>
            <span className="text-status-caution font-medium">{pending}</span>
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
          className="flex-1 px-4 py-2.5 rounded-lg bg-signal text-chart-navy text-sm font-medium hover:brightness-110 transition-all"
        >
          Confirm & Submit
        </button>
      </div>
    </div>
  </div>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const PlaybookPage: React.FC = () => {
  const BACKEND_PLAYBOOK_ID = "pb_001";

  const [playbook, setPlaybook]       = useState<Playbook | null>(null);
  const [loading, setLoading]         = useState(true);
  const [loadError, setLoadError]     = useState(false);
  const [actionStates, setActionStates] = useState<Record<string, ActionState>>({});
  const [showModal, setShowModal]     = useState(false);
  const [submitting, setSubmitting]   = useState(false);
  const [submitted, setSubmitted]     = useState(false);
  const [downloadingRole, setDownloadingRole] = useState<"ministry" | "procurement" | null>(null);
  const [downloadError, setDownloadError]     = useState<string | null>(null);

  const handleDownloadPdf = useCallback(async (role: "ministry" | "procurement") => {
    setDownloadingRole(role);
    setDownloadError(null);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10_000);

    try {
      const res = await apiClient.get(
        `/playbook/${BACKEND_PLAYBOOK_ID}/pdf`,
        { params: { role }, responseType: "blob", signal: controller.signal }
      );
      clearTimeout(timeoutId);
      const blob = new Blob([res.data], { type: "application/pdf" });
      const url  = window.URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = `resichain_${role}_${BACKEND_PLAYBOOK_ID}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: unknown) {
      clearTimeout(timeoutId);
      const isTimeout = err instanceof Error && err.name === "CanceledError";
      setDownloadError(
        isTimeout
          ? "PDF generation timed out after 10s — backend may be busy. Try again."
          : "Could not generate PDF — check backend connection"
      );
    } finally {
      setDownloadingRole(null);
    }
  }, []);

  useEffect(() => {
    getPlaybook(BACKEND_PLAYBOOK_ID)
      .then((pb) => {
        setPlaybook(pb);
        setActionStates(initActionStates(pb.actions));
        setLoading(false);
        setLoadError(false);
      })
      .catch(() => {
        setLoading(false);
        setLoadError(true);
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
      <AppLayout>
        <div className="space-y-4">
          {[1,2,3].map((i) => (
            <div key={i} className="h-40 bg-chart-panel rounded-xl border border-chart-hairline animate-pulse" />
          ))}
        </div>
      </AppLayout>
    );
  }

  if (loadError || !playbook) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="bg-chart-panel border border-status-critical/50 rounded-xl p-6 max-w-md text-center">
            <p className="text-status-critical text-sm font-medium mb-1">Unable to load playbook</p>
            <p className="text-slate-500 text-xs">
              The backend may be unreachable, or no playbook exists yet with id "{BACKEND_PLAYBOOK_ID}".
            </p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 text-xs text-signal hover:underline"
            >
              Retry
            </button>
          </div>
        </div>
      </AppLayout>
    );
  }

  const sc       = statusConfig(playbook.status);
  const elapsed  = elapsedSeconds(playbook.signal_detected_at, playbook.playbook_ready_at);

  return (
    <AppLayout>
      {showModal && (
        <ConfirmModal
          approved={approvedCount}
          rejected={rejectedCount}
          pending={pendingCount}
          onConfirm={handleSubmit}
          onCancel={() => setShowModal(false)}
        />
      )}

      <div className="mb-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="font-serif text-2xl font-medium text-white">Playbook Review</h1>
            <p className="text-slate-400 text-sm mt-1">
              Corridor: <span className="text-white">{playbook.corridor_affected}</span>
              {" · "}Compound risk: <span className="text-status-caution">{(playbook.compound_risk * 100).toFixed(0)}%</span>
            </p>
          </div>
          <span className={`text-sm px-3 py-1.5 rounded-lg border font-medium ${sc.cls}`}>
            {sc.label}
          </span>
        </div>

        <div className="flex items-center gap-2 mt-3">
          <button
            onClick={() => handleDownloadPdf("ministry")}
            disabled={downloadingRole !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-chart-hairline
              text-slate-300 text-xs hover:border-signal/50 hover:text-signal transition-colors
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
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-chart-hairline
              text-slate-300 text-xs hover:border-signal/50 hover:text-signal transition-colors
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
            <span className="text-status-critical text-xs">{downloadError}</span>
          )}
        </div>

        <div className="mt-5 bg-chart-panel border border-chart-hairline rounded-xl p-4 flex items-center gap-8">
          <div>
            <p className="text-slate-500 text-xs uppercase tracking-widest mb-1">Signal Detected</p>
            <p className="text-white text-sm tabular-nums font-medium">
              {new Date(playbook.signal_detected_at).toLocaleTimeString()}
            </p>
          </div>
          <div className="flex-1 flex items-center gap-2">
            <div className="flex-1 h-px bg-chart-hairline" />
            <span className="text-signal text-sm font-semibold tabular-nums whitespace-nowrap">
              {formatElapsed(elapsed)}
            </span>
            <div className="flex-1 h-px bg-chart-hairline" />
          </div>
          <div>
            <p className="text-slate-500 text-xs uppercase tracking-widest mb-1">Playbook Ready</p>
            <p className="text-white text-sm tabular-nums font-medium">
              {new Date(playbook.playbook_ready_at).toLocaleTimeString()}
            </p>
          </div>
          <div className="border-l border-chart-hairline pl-8">
            <p className="text-slate-500 text-xs uppercase tracking-widest mb-1">Confidence</p>
            <p className="text-white text-sm font-semibold">{(playbook.overall_confidence * 100).toFixed(0)}%</p>
          </div>
        </div>
      </div>

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

      {!submitted ? (
        <div className="bg-chart-panel border border-chart-hairline rounded-xl p-5 flex items-center justify-between">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-status-normal">{approvedCount} approved</span>
            <span className="text-status-critical">{rejectedCount} rejected</span>
            {pendingCount > 0 && <span className="text-slate-500">{pendingCount} undecided</span>}
          </div>
          <button
            onClick={() => setShowModal(true)}
            disabled={submitting || (approvedCount === 0 && rejectedCount === 0)}
            className="px-6 py-2.5 bg-signal text-chart-navy disabled:opacity-40 disabled:cursor-not-allowed
              text-sm font-medium rounded-lg hover:brightness-110 transition-all flex items-center gap-2"
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
        <div className="bg-status-normal/10 border border-status-normal/40 rounded-xl p-5 text-center">
          <p className="text-status-normal font-medium">
            Decision submitted — playbook is now{" "}
            <span className="font-semibold">{statusConfig(playbook.status).label}</span>
          </p>
          <p className="text-slate-500 text-xs mt-1">Logged to audit trail · use the PDF buttons above to export</p>
        </div>
      )}
    </AppLayout>
  );
};

export default PlaybookPage;