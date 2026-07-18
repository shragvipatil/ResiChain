/**
 * MinistryPage.tsx — updated Day 7
 *
 * New in Day 7:
 *   - Polls getVessels() every 5 minutes, passes vessel list to ShippingMap
 *   - Passes compoundDisruptionDetected → animateCapeRoute to ShippingMap
 *   - Live/mock indicator driven by wsConnected
 */

import React, { useEffect, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import { getRiskState, getVessels } from "../api/endpoints";
import { Vessel } from "../types";
import ShippingMap from "../components/ShippingMap";
import AgentStatusPanel from "../components/AgentStatusPanel";
import RiskWeightSliders from "../components/RiskWeightSliders";
import KnowledgeGraph from "../components/KnowledgeGraph";
import AppLayout from "../components/AppLayout";

const VESSEL_POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

const getRiskColor = (risk: number) =>
  risk > 0.65 ? "text-red-400" : risk > 0.30 ? "text-amber-400" : "text-green-400";

const getRiskLabel = (risk: number) =>
  risk > 0.65 ? "CRITICAL" : risk > 0.30 ? "ELEVATED" : "NORMAL";

const MinistryPage: React.FC = () => {
  const { riskState, setRiskState, compoundDisruptionDetected } = useAppContext();
  const navigate = useNavigate();

  const [vessels, setVessels] = useState<Vessel[]>([]);
  const [backendUnreachable, setBackendUnreachable] = useState(false);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  // Seed initial risk state — was previously unhandled, crashing the whole
  // page with an uncaught AxiosError when the backend is unreachable
  // (confirmed via Docker stopping mid-session). Now shows a banner instead.
  useEffect(() => {
    if (!riskState) {
      getRiskState()
        .then((data) => { setRiskState(data); setBackendUnreachable(false); })
        .catch(() => setBackendUnreachable(true));
    }
  }, [riskState, setRiskState]);

  // Vessel polling — every 5 minutes
  useEffect(() => {
    const fetchVessels = () =>
      getVessels()
        .then((res) => { setVessels(res.vessels); setBackendUnreachable(false); })
        .catch(() => setBackendUnreachable(true));

    fetchVessels(); // immediate on mount
    pollRef.current = setInterval(fetchVessels, VESSEL_POLL_INTERVAL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  return (
    <AppLayout>
      {/* Page title + page-specific actions (nav/user/logout now live in AppHeader) */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium text-white">National Command Center</h1>
          <p className="text-slate-400 text-sm mt-1">ResiChain — Energy Supply Chain Resilience</p>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <button
            onClick={() => navigate("/playbook")}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium
              rounded-lg transition-colors flex items-center gap-1.5"
          >
            View Playbook →
          </button>
          {compoundDisruptionDetected && (
            <span className="text-xs bg-red-900/60 text-red-400 border border-red-800 px-2.5 py-1 rounded-lg font-medium animate-pulse">
              ⚠ COMPOUND DISRUPTION
            </span>
          )}
        </div>
      </div>

      {/* Backend unreachable banner — replaces uncaught crash with a visible, dismissible state */}
      {backendUnreachable && (
        <div className="mb-6 bg-red-900/30 border border-red-800 rounded-xl px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
            <p className="text-red-400 text-sm">
              Backend unreachable — showing last known data. Retrying automatically…
            </p>
          </div>
          <button
            onClick={() => window.location.reload()}
            className="text-xs text-red-300 hover:text-white underline"
          >
            Reload now
          </button>
        </div>
      )}

      {/* Corridor risk cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {riskState
          ? Object.entries(riskState.corridors).map(([corridor, detail]) => {
              const risk = detail.risk_score;
              return (
                <div key={corridor} className="bg-slate-800 rounded-xl p-5 border border-slate-700">
                  <p className="text-slate-400 text-sm mb-2">{corridor.replace("_", " ")}</p>
                  <p className={`text-4xl font-medium ${getRiskColor(risk)}`}>
                    {(risk * 100).toFixed(0)}%
                  </p>
                  <p className={`text-xs mt-2 font-medium ${getRiskColor(risk)}`}>
                    {getRiskLabel(risk)}
                  </p>
                  {detail.trend && (
                    <p className="text-slate-600 text-xs mt-1">
                      {detail.trend === "rising" ? "↑ rising" : detail.trend === "falling" ? "↓ falling" : "→ stable"}
                    </p>
                  )}
                </div>
              );
            })
          : Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="bg-slate-800 rounded-xl p-5 border border-slate-700 animate-pulse">
                <div className="h-3 w-20 bg-slate-700 rounded mb-3" />
                <div className="h-10 w-16 bg-slate-700 rounded" />
              </div>
            ))}
      </div>

      {/* Map (2/3) + Agent panel (1/3) */}
      <div className="grid grid-cols-3 gap-6 mb-6">
        <div className="col-span-2 bg-slate-800 rounded-xl border border-slate-700 p-1">
          <div className="px-4 pt-4 pb-2">
            <h2 className="text-white text-sm font-medium">Live Shipping Map</h2>
            <p className="text-slate-500 text-xs mt-0.5">
              {vessels.length > 0 ? `${vessels.length} AIS vessels tracked · ` : ""}
              Lane colours reflect live risk · Click a port for details
            </p>
          </div>
          <ShippingMap
            riskState={riskState}
            vessels={vessels}
            animateCapeRoute={compoundDisruptionDetected}
            height="420px"
          />
        </div>
        <div className="col-span-1">
          <AgentStatusPanel />
        </div>
      </div>

      {/* Risk weight sliders */}
      <div className="mb-6">
        <RiskWeightSliders />
      </div>

      {/* Knowledge Graph — Fix 14: replaces Neo4j Browser dependency */}
      {/*
        Per CLAUDE.md demo advice: open this full-screen first.
        "The visual communicates more in 10 seconds than a minute of description."
        blockedChokepoints prop: pass ["Hormuz","Red Sea"] during compound demo
        to grey out blocked edges and pulse the red nodes.
      */}
      <div className="mb-6">
        <KnowledgeGraph
          blockedChokepoints={compoundDisruptionDetected ? ["Hormuz", "Red Sea"] : []}
          height="480px"
        />
      </div>

      {/* Status bar */}
      <div className="bg-slate-800 rounded-xl p-5 border border-slate-700 flex items-center justify-between">
        <div>
          <p className="text-slate-400 text-sm">
            System mode:{" "}
            <span className={
              riskState?.system_mode === "CRISIS" ? "text-red-400" :
              riskState?.system_mode === "WATCH"  ? "text-amber-400" :
              "text-green-400"
            }>
              {riskState?.system_mode ?? "—"}
            </span>
          </p>
          <p className="text-slate-500 text-xs mt-1">Last updated: {riskState?.updated_at ?? "—"}</p>
        </div>
        {vessels.length > 0 && (
          <p className="text-slate-600 text-xs">{vessels.length} vessels · refreshes every 5 min</p>
        )}
      </div>
    </AppLayout>
  );
};

export default MinistryPage;