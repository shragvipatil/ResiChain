/**
 * ProcurementPage.tsx  —  Day 4 deliverable (Person C)
 *
 * Four panels, all on mock data today:
 *   1. Spot Price Panel        — Brent + WTI cards
 *   2. Supplier Ranking Table  — confidence, route, grade, cost delta, lead time
 *   3. Contract Headroom Bars  — used vs available volume per supplier
 *   4. Rejection Trace Table   — every evaluated option with status badge + reason
 *
 * Day 13 upgrade: swap USE_MOCK flag in api/client.ts — no component changes needed.
 */

import React, { useEffect, useState } from "react";
import { getProcurementOptions, getLivePrices } from "../api/endpoints";
import { ProcurementOption, ProcurementResponse, PricesResponse } from "../types";

// ── Mock contract headroom (no API endpoint yet — Day 13 replaces this) ──────
const CONTRACT_HEADROOM = [
  { supplier: "Russia",       used_mbd: 0.38, max_mbd: 0.40, grade: "Urals" },
  { supplier: "Saudi Arabia", used_mbd: 0.22, max_mbd: 0.40, grade: "Arab Light" },
  { supplier: "UAE",          used_mbd: 0.14, max_mbd: 0.30, grade: "Murban" },
  { supplier: "Iraq",         used_mbd: 0.18, max_mbd: 0.35, grade: "Basra Light" },
  { supplier: "USA",          used_mbd: 0.06, max_mbd: 0.20, grade: "WTI Midland" },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

const statusBadge = (status: string) => {
  const base = "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium tracking-wide";
  if (status === "APPROVED") return `${base} bg-green-900/60 text-green-400 border border-green-800`;
  if (status === "PARTIAL")  return `${base} bg-amber-900/60 text-amber-400 border border-amber-800`;
  return                            `${base} bg-red-900/60 text-red-400 border border-red-800`;
};

const changePill = (pct: number) => (
  <span className={`text-xs font-medium ${pct >= 0 ? "text-green-400" : "text-red-400"}`}>
    {pct >= 0 ? "▲" : "▼"} {Math.abs(pct).toFixed(2)}%
  </span>
);

const confidenceBar = (score: number) => {
  const pct = Math.round(score * 100);
  const color = score >= 0.8 ? "bg-green-500" : score >= 0.5 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-slate-300 text-xs tabular-nums">{pct}%</span>
    </div>
  );
};

// ── Panel 1: Spot Prices ──────────────────────────────────────────────────────

const SpotPricePanel: React.FC<{ prices: PricesResponse | null; loading: boolean }> = ({ prices, loading }) => (
  <div className="grid grid-cols-2 gap-4">
    {[
      { label: "Brent Crude", price: prices?.brent_usd, change: prices?.brent_change_pct_24h, color: "text-blue-400" },
      { label: "WTI Crude",   price: prices?.wti_usd,   change: prices?.wti_change_pct_24h,   color: "text-cyan-400" },
    ].map(({ label, price, change, color }) => (
      <div key={label} className="bg-slate-800 border border-slate-700 rounded-xl p-5">
        <p className="text-slate-400 text-xs uppercase tracking-widest mb-3">{label}</p>
        {loading || price === undefined ? (
          <div className="h-8 w-28 bg-slate-700 rounded animate-pulse" />
        ) : (
          <>
            <p className={`text-3xl font-semibold tabular-nums ${color}`}>${price?.toFixed(2)}</p>
            <div className="flex items-center gap-1.5 mt-2">
              {changePill(change ?? 0)}
              <span className="text-slate-500 text-xs">24h</span>
            </div>
          </>
        )}
        <p className="text-slate-600 text-xs mt-3">USD / barrel · {prices?.source ?? "—"}</p>
      </div>
    ))}
  </div>
);

// ── Panel 2: Supplier Ranking ─────────────────────────────────────────────────

const SupplierRankingTable: React.FC<{ options: ProcurementOption[] }> = ({ options }) => {
  const ranked = [...options]
    .filter((o) => o.status !== "BLOCKED")
    .sort((a, b) => b.confidence - a.confidence);

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-700">
        <h2 className="text-white text-sm font-medium">Supplier Alternatives</h2>
        <p className="text-slate-500 text-xs mt-0.5">Ranked by confidence score — approved and partial options only</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700">
              {["#", "Supplier", "Grade", "Status", "Confidence", "Route", "Cost Δ ($/bbl)", "Lead Time"].map((h) => (
                <th key={h} className="text-left text-slate-500 text-xs font-medium px-5 py-3 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ranked.map((opt, i) => (
              <tr key={opt.option_id} className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors">
                <td className="px-5 py-3.5 text-slate-500 text-xs tabular-nums">{i + 1}</td>
                <td className="px-5 py-3.5 text-white font-medium">{opt.supplier}</td>
                <td className="px-5 py-3.5 text-slate-300">{opt.crude_grade}</td>
                <td className="px-5 py-3.5"><span className={statusBadge(opt.status)}>{opt.status}</span></td>
                <td className="px-5 py-3.5">{confidenceBar(opt.confidence)}</td>
                <td className="px-5 py-3.5 text-slate-400 text-xs">{opt.route ?? "—"}</td>
                <td className="px-5 py-3.5 text-xs tabular-nums">
                  {opt.cost_delta_usd_per_barrel != null
                    ? <span className="text-amber-400">+${opt.cost_delta_usd_per_barrel.toFixed(2)}</span>
                    : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-5 py-3.5 text-slate-400 text-xs">
                  {opt.transit_days != null ? `${opt.transit_days}d` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {ranked.length === 0 && (
          <p className="text-slate-500 text-sm text-center py-10">No viable options available</p>
        )}
      </div>
    </div>
  );
};

// ── Panel 3: Contract Headroom ────────────────────────────────────────────────

const ContractHeadroomPanel: React.FC = () => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Contract Headroom</h2>
      <p className="text-slate-500 text-xs mt-0.5">Used vs. maximum contracted volume (Mb/d) — MoPNG diversification policy</p>
    </div>
    <div className="p-5 space-y-5">
      {CONTRACT_HEADROOM.map(({ supplier, used_mbd, max_mbd, grade }) => {
        const usedPct = (used_mbd / max_mbd) * 100;
        const nearCap = usedPct >= 90;
        const barColor = nearCap ? "bg-red-500" : usedPct >= 70 ? "bg-amber-500" : "bg-blue-500";
        return (
          <div key={supplier}>
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-2">
                <span className="text-white text-sm font-medium">{supplier}</span>
                <span className="text-slate-500 text-xs">{grade}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-slate-400 text-xs tabular-nums">
                  {used_mbd.toFixed(2)} / {max_mbd.toFixed(2)} Mb/d
                </span>
                {nearCap && <span className="text-xs text-red-400 font-medium">Near cap</span>}
              </div>
            </div>
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden flex">
              <div className={`h-full ${barColor} rounded-l-full transition-all`} style={{ width: `${usedPct}%` }} />
              <div className="h-full bg-slate-600/40" style={{ width: `${100 - usedPct}%` }} />
            </div>
            <div className="flex justify-between mt-1">
              <span className="text-slate-600 text-xs">{usedPct.toFixed(0)}% used</span>
              <span className="text-slate-600 text-xs">{(max_mbd - used_mbd).toFixed(2)} Mb/d headroom</span>
            </div>
          </div>
        );
      })}
    </div>
  </div>
);

// ── Panel 4: Rejection Trace ──────────────────────────────────────────────────

const RejectionTraceTable: React.FC<{ options: ProcurementOption[]; evaluatedAt: string }> = ({ options, evaluatedAt }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700 flex items-start justify-between">
      <div>
        <h2 className="text-white text-sm font-medium">Evaluation Trace</h2>
        <p className="text-slate-500 text-xs mt-0.5">Every option evaluated this cycle — full audit trail</p>
      </div>
      <span className="text-slate-600 text-xs tabular-nums mt-0.5">
        {new Date(evaluatedAt).toLocaleTimeString()}
      </span>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700">
            {["Supplier", "Grade", "Status", "Rule Triggered", "Detail", "Source"].map((h) => (
              <th key={h} className="text-left text-slate-500 text-xs font-medium px-5 py-3 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {options.map((opt) => (
            <tr key={opt.option_id} className="border-b border-slate-700/50 hover:bg-slate-700/20 transition-colors">
              <td className="px-5 py-3.5 text-white font-medium whitespace-nowrap">{opt.supplier}</td>
              <td className="px-5 py-3.5 text-slate-300 whitespace-nowrap">{opt.crude_grade}</td>
              <td className="px-5 py-3.5 whitespace-nowrap"><span className={statusBadge(opt.status)}>{opt.status}</span></td>
              <td className="px-5 py-3.5 whitespace-nowrap">
                {opt.rule_triggered
                  ? <span className="text-xs font-mono bg-slate-900 text-slate-300 px-2 py-0.5 rounded border border-slate-700">{opt.rule_triggered}</span>
                  : <span className="text-slate-600 text-xs">—</span>}
              </td>
              <td className="px-5 py-3.5 text-slate-400 text-xs max-w-xs">
                {opt.reason
                  ? <>
                      {opt.reason.value}
                      {opt.reason.threshold && <span className="text-slate-600"> · threshold: {opt.reason.threshold}</span>}
                    </>
                  : <span className="text-green-500 text-xs">All checks passed</span>}
              </td>
              <td className="px-5 py-3.5 text-slate-600 text-xs font-mono whitespace-nowrap">{opt.reason?.source ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    <div className="px-5 py-3 border-t border-slate-700 flex items-center gap-6">
      {(["APPROVED", "PARTIAL", "BLOCKED"] as const).map((s) => {
        const count = options.filter((o) => o.status === s).length;
        const color = s === "APPROVED" ? "text-green-400" : s === "PARTIAL" ? "text-amber-400" : "text-red-400";
        return <span key={s} className={`text-xs ${color}`}>{count} {s.toLowerCase()}</span>;
      })}
      <span className="text-slate-600 text-xs ml-auto">{options.length} total evaluated</span>
    </div>
  </div>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const ProcurementPage: React.FC = () => {
  const [procurement, setProcurement] = useState<ProcurementResponse | null>(null);
  const [prices, setPrices]           = useState<PricesResponse | null>(null);
  const [loading, setLoading]         = useState(true);

  useEffect(() => {
    Promise.all([getProcurementOptions(), getLivePrices()]).then(([p, pr]) => {
      setProcurement(p);
      setPrices(pr);
      setLoading(false);
    });
  }, []);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium text-white">Procurement Operations</h1>
          <p className="text-slate-400 text-sm mt-1">Supply alternatives · Contract headroom · Evaluation trace</p>
        </div>
        {procurement && (
          <div className="text-right">
            <p className="text-slate-500 text-xs">Surviving corridors</p>
            <div className="flex gap-2 mt-1 justify-end">
              {procurement.surviving_corridors.map((c) => (
                <span key={c} className="text-xs bg-green-900/50 text-green-400 border border-green-800 px-2 py-0.5 rounded">
                  {c.replace("_", " ")}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Panel 1 — Spot Prices */}
      <section className="mb-6">
        <p className="text-slate-500 text-xs uppercase tracking-widest mb-3">Spot Prices</p>
        <SpotPricePanel prices={prices} loading={loading} />
      </section>

      {/* Panel 2 — Supplier Ranking */}
      <section className="mb-6">
        {loading
          ? <div className="h-48 bg-slate-800 rounded-xl border border-slate-700 animate-pulse" />
          : <SupplierRankingTable options={procurement?.options ?? []} />}
      </section>

      {/* Panel 3 — Contract Headroom */}
      <section className="mb-6">
        <ContractHeadroomPanel />
      </section>

      {/* Panel 4 — Rejection Trace */}
      <section className="mb-6">
        {loading
          ? <div className="h-48 bg-slate-800 rounded-xl border border-slate-700 animate-pulse" />
          : <RejectionTraceTable
              options={procurement?.options ?? []}
              evaluatedAt={procurement?.evaluated_at ?? new Date().toISOString()}
            />}
      </section>
    </div>
  );
};

export default ProcurementPage;