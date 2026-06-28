import React, { useEffect } from "react";
import { useAppContext } from "../context/AppContext";
import { getRiskState } from "../api/endpoints";
import ShippingMap from "../components/ShippingMap";
import AgentStatusPanel from "../components/AgentStatusPanel";
import RiskWeightSliders from "../components/RiskWeightSliders";

const getRiskColor = (risk: number) => {
  if (risk > 0.65) return "text-red-400";
  if (risk > 0.30) return "text-amber-400";
  return "text-green-400";
};

const getRiskLabel = (risk: number) => {
  if (risk > 0.65) return "CRITICAL";
  if (risk > 0.30) return "ELEVATED";
  return "NORMAL";
};

const MinistryPage: React.FC = () => {
  const { riskState, setRiskState, wsConnected } = useAppContext();

  useEffect(() => {
    if (!riskState) {
      getRiskState().then(setRiskState);
    }
  }, [riskState, setRiskState]);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium text-white">
            National Command Center
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            ResiChain AI v2.0 — Energy Supply Chain Resilience
          </p>
        </div>

        <div className="flex items-center gap-1.5 mt-1">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              wsConnected ? "bg-green-400 animate-pulse" : "bg-slate-600"
            }`}
          />
          <span className="text-slate-500 text-xs">
            {wsConnected ? "Live" : "Mock data"}
          </span>
        </div>
      </div>

      {/* Corridor Cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {riskState
          ? Object.entries(riskState.corridors).map(
              ([corridor, detail]) => (
                <div
                  key={corridor}
                  className="bg-slate-800 rounded-xl p-5 border border-slate-700"
                >
                  <p className="text-slate-400 text-sm mb-2">
                    {corridor.replace("_", " ")}
                  </p>

                  <p
                    className={`text-4xl font-medium ${getRiskColor(
                      detail.risk_score
                    )}`}
                  >
                    {(detail.risk_score * 100).toFixed(0)}%
                  </p>

                  <p
                    className={`text-xs mt-2 font-medium ${getRiskColor(
                      detail.risk_score
                    )}`}
                  >
                    {getRiskLabel(detail.risk_score)}
                  </p>

                  <p className="text-slate-500 text-xs mt-2">
                    {detail.status} · {detail.trend}
                  </p>
                </div>
              )
            )
          : Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="bg-slate-800 rounded-xl p-5 border border-slate-700 animate-pulse"
              >
                <div className="h-3 w-20 bg-slate-700 rounded mb-3" />
                <div className="h-10 w-16 bg-slate-700 rounded" />
              </div>
            ))}
      </div>

      {/* Map + Sidebar */}
      <div className="grid grid-cols-3 gap-6 mb-6">
        <div className="col-span-2 bg-slate-800 rounded-xl border border-slate-700 p-1">
          <div className="px-4 pt-4 pb-2">
            <h2 className="text-white text-sm font-medium">
              Live Shipping Map
            </h2>
            <p className="text-slate-500 text-xs mt-0.5">
              Lane colours reflect current risk levels · Click a port for details
            </p>
          </div>

          <ShippingMap riskState={riskState} height="420px" />
        </div>

        <div className="col-span-1 flex flex-col gap-4">
          <AgentStatusPanel />
        </div>
      </div>

      {/* Sliders */}
      <div className="mb-6">
        <RiskWeightSliders />
      </div>

      {/* Status Footer */}
      <div className="bg-slate-800 rounded-xl p-5 border border-slate-700">
        <p className="text-slate-400 text-sm">
          Crisis mode:{" "}
          <span
            className={
              riskState?.system_mode === "CRISIS"
                ? "text-red-400"
                : "text-green-400"
            }
          >
            {riskState?.system_mode === "CRISIS"
              ? "ACTIVE"
              : "Inactive"}
          </span>
        </p>

        <p className="text-slate-500 text-xs mt-1">
          Last updated: {riskState?.last_updated ?? "—"}
        </p>
      </div>
    </div>
  );
};

export default MinistryPage;