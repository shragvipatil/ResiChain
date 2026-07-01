/**
 * RiskWeightSliders.tsx — Day 5 deliverable (Person C)
 *
 * Five range sliders — one per risk factor. Weights must sum to 100%.
 * On mouse/touch release: calls PATCH /api/risk-weights.
 * On success: the riskState in AppContext updates via the API response,
 * which flows into the Ministry corridor cards immediately.
 *
 * This is the live judge interaction described in demo Minute 6:
 * "Analyst increases sanctions weight from 25% to 40% — watch scores update."
 *
 * Design decisions:
 *  - Sliders are independent; we normalise to 100% before sending to API.
 *  - Visual sum indicator turns red if weights are far from 100%.
 *  - Disabled while a PATCH is in flight to prevent double-sends.
 */

import React, { useState, useCallback } from "react";
import { useAppContext } from "../context/AppContext";
import { updateRiskWeights } from "../api/endpoints";
import { CorridorRiskState } from "../types";

// ── Factor definitions ────────────────────────────────────────────────────────

interface Factor {
  key:         keyof RiskWeights;
  label:       string;
  description: string;
  color:       string;       // Tailwind bg class for the slider track fill
}

interface RiskWeights {
  military_incidents:    number;
  conflict_escalation:  number;
  sanctions_change:     number;
  market_volatility:    number;
  seasonal_risk:        number;
}

const FACTORS: Factor[] = [
  {
    key:         "military_incidents",
    label:       "Military Incident",
    description: "GDELT codes 19–20 + UKMTO advisories",
    color:       "accent-red-500",
  },
  {
    key:         "conflict_escalation",
    label:       "Conflict Escalation",
    description: "GDELT Goldstein scale, second-order codes",
    color:       "accent-orange-500",
  },
  {
    key:         "sanctions_change",
    label:       "Active Sanctions",
    description: "OFAC SDN daily delta — new entries vs yesterday",
    color:       "accent-amber-500",
  },
  {
    key:         "market_volatility",
    label:       "Market Volatility",
    description: "Alpha Vantage Brent % move from prior close",
    color:       "accent-blue-500",
  },
  {
    key:         "seasonal_risk",
    label:       "Seasonal Risk",
    description: "Month × corridor lookup from AIS delay history",
    color:       "accent-slate-400",
  },
];

// Default weights (matching the document spec: 35/25/25/10/5)
const DEFAULT_WEIGHTS: RiskWeights = {
  military_incidents:   35,
  conflict_escalation: 25,
  sanctions_change:    25,
  market_volatility:   10,
  seasonal_risk:        5,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function normalise(weights: RiskWeights): RiskWeights {
  const total = Object.values(weights).reduce((s, v) => s + v, 0);
  if (total === 0) return { ...DEFAULT_WEIGHTS };
  const ratio = 100 / total;
  return {
    military_incidents:   Math.round(weights.military_incidents   * ratio),
    conflict_escalation: Math.round(weights.conflict_escalation * ratio),
    sanctions_change:    Math.round(weights.sanctions_change    * ratio),
    market_volatility:   Math.round(weights.market_volatility   * ratio),
    seasonal_risk:       Math.round(weights.seasonal_risk       * ratio),
  };
}

// ── Component ─────────────────────────────────────────────────────────────────

const RiskWeightSliders: React.FC = () => {
  const { setRiskState } = useAppContext();
  const [weights, setWeights]   = useState<RiskWeights>({ ...DEFAULT_WEIGHTS });
  const [saving, setSaving]     = useState(false);
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const [error, setError]       = useState<string | null>(null);

  const total = Object.values(weights).reduce((s, v) => s + v, 0);
  const sumOk = Math.abs(total - 100) <= 2;

  // Fires on slider change (real-time visual only)
  const handleChange = useCallback((key: keyof RiskWeights, value: number) => {
    setWeights((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Fires on mouse/touch release — sends to API
  const handleCommit = useCallback(async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const normalised = normalise(weights);
      // Convert from percentage (35) to decimal (0.35) for the API
      const apiPayload = Object.fromEntries(
        Object.entries(normalised).map(([k, v]) => [k, v / 100])
      ) as Parameters<typeof updateRiskWeights>[0];

      const result = await updateRiskWeights(apiPayload);

      // If the API returns the new risk state, push it into context
      // so Ministry corridor cards update immediately
      if (result && (result as unknown as CorridorRiskState).corridors) {
        setRiskState(result as unknown as CorridorRiskState);
      }

      setLastSaved(new Date().toLocaleTimeString());
    } catch {
      setError("Failed to update weights — check API connection");
    } finally {
      setSaving(false);
    }
  }, [weights, saving, setRiskState]);

  const handleReset = () => {
    setWeights({ ...DEFAULT_WEIGHTS });
    setError(null);
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-700 flex items-start justify-between">
        <div>
          <h2 className="text-white text-sm font-medium">Risk Weight Configuration</h2>
          <p className="text-slate-500 text-xs mt-0.5">
            Adjust factor weights — corridor scores update on release
          </p>
        </div>
        {/* Weight sum indicator */}
        <div className={`text-xs tabular-nums font-medium px-2 py-0.5 rounded border ${
          sumOk
            ? "text-green-400 border-green-800 bg-green-900/40"
            : "text-red-400 border-red-800 bg-red-900/40"
        }`}>
          {total}%
        </div>
      </div>

      {/* Sliders */}
      <div className="px-5 py-4 space-y-5">
        {FACTORS.map(({ key, label, description, color }) => (
          <div key={key}>
            <div className="flex items-center justify-between mb-1.5">
              <div>
                <span className="text-white text-xs font-medium">{label}</span>
                <p className="text-slate-600 text-xs mt-0.5">{description}</p>
              </div>
              <span className="text-slate-300 text-sm tabular-nums font-medium w-10 text-right">
                {weights[key]}%
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={60}
              step={1}
              value={weights[key]}
              disabled={saving}
              className={`w-full h-1.5 bg-slate-700 rounded-full appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${color}`}
              onChange={(e) => handleChange(key, Number(e.target.value))}
              onMouseUp={handleCommit}
              onTouchEnd={handleCommit}
            />
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-slate-700 flex items-center gap-3">
        <button
          onClick={handleReset}
          disabled={saving}
          className="text-xs text-slate-400 hover:text-white transition-colors disabled:opacity-40"
        >
          Reset to defaults
        </button>

        {saving && (
          <span className="text-xs text-blue-400 flex items-center gap-1.5">
            <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Updating…
          </span>
        )}

        {!saving && lastSaved && !error && (
          <span className="text-xs text-green-400">Updated {lastSaved}</span>
        )}

        {error && (
          <span className="text-xs text-red-400">{error}</span>
        )}

        {!sumOk && (
          <span className="text-xs text-amber-400 ml-auto">
            Weights sum to {total}% — will be normalised to 100%
          </span>
        )}
      </div>
    </div>
  );
};

export default RiskWeightSliders;