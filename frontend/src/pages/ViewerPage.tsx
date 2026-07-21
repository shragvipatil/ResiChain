/**
 * ViewerPage.tsx — Day 11 deliverable (Person C)
 * Read-only summary view for VIEWER role — no controls, no sliders,
 * no approve/reject actions. Just the current state, at a glance.
 */

import React, { useEffect, useState } from "react";
import { getRiskState } from "../api/endpoints";
import { CorridorRiskState } from "../types";
import AppLayout from "../components/AppLayout";

const getRiskColor = (risk: number) =>
  risk > 0.65 ? "text-red-400" : risk > 0.30 ? "text-amber-400" : "text-green-400";

const ViewerPage: React.FC = () => {
  const [riskState, setRiskState] = useState<CorridorRiskState | null>(null);
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    getRiskState()
      .then((data) => { setRiskState(data); setFetchError(false); })
      .catch(() => setFetchError(true));
  }, []);

  return (
    <AppLayout showRiskStrip={false}>
      <div className="mb-8">
        <h1 className="text-2xl font-medium text-white">Supply Chain Overview</h1>
        <p className="text-slate-400 text-sm mt-1">Read-only summary — Viewer access</p>
      </div>

      {fetchError && (
        <div className="mb-6 bg-red-900/30 border border-red-800 rounded-xl px-5 py-3">
          <p className="text-red-400 text-sm">Backend unreachable — showing last known data.</p>
        </div>
      )}

      <div className="grid grid-cols-4 gap-4 mb-8">
        {riskState
          ? Object.entries(riskState.corridors).map(([corridor, detail]) => (
              <div key={corridor} className="bg-slate-800 rounded-xl p-5 border border-slate-700">
                <p className="text-slate-400 text-sm mb-2">{corridor.replace("_", " ")}</p>
                <p className={`text-4xl font-medium ${getRiskColor(detail.risk_score)}`}>
                  {(detail.risk_score * 100).toFixed(0)}%
                </p>
              </div>
            ))
          : Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="bg-slate-800 rounded-xl p-5 border border-slate-700 animate-pulse h-24" />
            ))}
      </div>

      <div className="bg-slate-800 rounded-xl p-5 border border-slate-700">
        <p className="text-slate-400 text-sm">
          System mode:{" "}
          <span className="text-white font-medium">{riskState?.system_mode ?? "—"}</span>
        </p>
        <p className="text-slate-600 text-xs mt-2">
          Contact a Procurement Analyst or Ministry account holder for detailed operations views.
        </p>
      </div>
    </AppLayout>
  );
};

export default ViewerPage;