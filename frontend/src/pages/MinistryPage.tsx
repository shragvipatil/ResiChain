import React, { useEffect, useState } from "react";
import { getRiskState } from "../api/endpoints";
import { CorridorRiskState } from "../types";
import ShippingMap from "../components/ShippingMap";

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
  const [riskState, setRiskState] = useState<CorridorRiskState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getRiskState().then((data) => {
      setRiskState(data);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-slate-900">
        <p className="text-slate-400 text-lg">Loading risk state...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-medium text-white">National Command Center</h1>
        <p className="text-slate-400 text-sm mt-1">ResiChain AI v2.0 — Energy Supply Chain Resilience</p>
      </div>

      {/* Corridor risk score cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {riskState && Object.entries(riskState.corridors).map(([corridor, risk]) => (
          <div key={corridor} className="bg-slate-800 rounded-xl p-5 border border-slate-700">
            <p className="text-slate-400 text-sm mb-2">{corridor.replace("_", " ")}</p>
            <p className={`text-4xl font-medium ${getRiskColor(risk as number)}`}>
              {((risk as number) * 100).toFixed(0)}%
            </p>
            <p className={`text-xs mt-2 font-medium ${getRiskColor(risk as number)}`}>
              {getRiskLabel(risk as number)}
            </p>
          </div>
        ))}
      </div>

      {/* Shipping Map */}
      <div className="bg-slate-800 rounded-xl border border-slate-700 p-1 mb-6">
        <div className="px-4 pt-4 pb-2">
          <h2 className="text-white text-sm font-medium">Live Shipping Map</h2>
          <p className="text-slate-500 text-xs mt-0.5">
            Shipping corridors — colours reflect current risk levels. Click a port marker for details.
          </p>
        </div>
        {/*
          Day 7 upgrade: add vessels={vesselData} prop here.
          The map renders AIS tanker positions automatically — no other changes needed.
        */}
        <ShippingMap riskState={riskState} height="520px" />
      </div>

      {/* Crisis mode status */}
      <div className="bg-slate-800 rounded-xl p-5 border border-slate-700">
        <p className="text-slate-400 text-sm">
          Crisis mode:{" "}
          <span className={riskState?.crisis_mode_active ? "text-red-400" : "text-green-400"}>
            {riskState?.crisis_mode_active ? "ACTIVE" : "Inactive"}
          </span>
        </p>
        <p className="text-slate-500 text-xs mt-1">Last updated: {riskState?.updated_at}</p>
      </div>
    </div>
  );
};

export default MinistryPage;