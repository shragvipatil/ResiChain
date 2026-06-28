import React, { useEffect, useState } from "react";
import { getProcurementOptions, getLivePrices } from "../api/endpoints";
import {
  ProcurementOption,
  ProcurementResponse,
  PricesResponse,
} from "../types";
import RejectionTraceAnimation from "../components/RejectionTraceAnimation";
import { useAppContext } from "../context/AppContext";

// ── Mock contract headroom ─────────────────────────────────────────
const CONTRACT_HEADROOM = [
  { supplier: "Russia", used_mbd: 0.38, max_mbd: 0.4, grade: "Urals" },
  { supplier: "Saudi Arabia", used_mbd: 0.22, max_mbd: 0.4, grade: "Arab Light" },
  { supplier: "UAE", used_mbd: 0.14, max_mbd: 0.3, grade: "Murban" },
  { supplier: "Iraq", used_mbd: 0.18, max_mbd: 0.35, grade: "Basra Light" },
  { supplier: "USA", used_mbd: 0.06, max_mbd: 0.2, grade: "WTI Midland" },
];

// ── Helpers ────────────────────────────────────────────────────────
const changePill = (pct: number) => (
  <span className={`text-xs ${pct >= 0 ? "text-green-400" : "text-red-400"}`}>
    {pct >= 0 ? "▲" : "▼"} {Math.abs(pct).toFixed(2)}%
  </span>
);

// ── Spot Prices Panel ─────────────────────────────────────────────
const SpotPricePanel: React.FC<{
  prices: PricesResponse | null;
  loading: boolean;
}> = ({ prices, loading }) => (
  <div className="grid grid-cols-2 gap-4">
    {[
      {
        label: "Brent Crude",
        price: prices?.brent_usd,
        change: prices?.brent_change_pct_24h,
        color: "text-blue-400",
      },
      {
        label: "WTI Crude",
        price: prices?.wti_usd,
        change: prices?.wti_change_pct_24h,
        color: "text-cyan-400",
      },
    ].map((item) => (
      <div
        key={item.label}
        className="bg-slate-800 border border-slate-700 rounded-xl p-5"
      >
        <p className="text-slate-400 text-xs mb-2">{item.label}</p>

        {loading || item.price === undefined ? (
          <div className="h-8 w-28 bg-slate-700 rounded animate-pulse" />
        ) : (
          <>
            <p className={`text-3xl font-semibold ${item.color}`}>
              ${item.price.toFixed(2)}
            </p>
            <div className="mt-2">{changePill(item.change ?? 0)}</div>
          </>
        )}

        <p className="text-slate-600 text-xs mt-3">
          USD / barrel · {prices?.source ?? "—"}
        </p>
      </div>
    ))}
  </div>
);

// ── Supplier Ranking ──────────────────────────────────────────────
const SupplierRankingTable: React.FC<{
  options: ProcurementOption[];
}> = ({ options }) => {
  const ranked = [...options]
    .filter((o) => o.status !== "BLOCKED")
    .sort((a, b) => b.confidence - a.confidence);

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
      <h2 className="text-white text-sm mb-3">Supplier Ranking</h2>

      <div className="space-y-2">
        {ranked.map((o) => (
          <div
            key={o.option_id}
            className="flex justify-between text-sm text-slate-300"
          >
            <span>{o.supplier}</span>
            <span>{o.crude_grade}</span>
            <span className="text-xs text-slate-500">
              {o.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ── Contract Headroom ─────────────────────────────────────────────
const ContractHeadroomPanel: React.FC = () => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
    <h2 className="text-white text-sm mb-4">Contract Headroom</h2>

    <div className="space-y-4">
      {CONTRACT_HEADROOM.map((c) => {
        const pct = (c.used_mbd / c.max_mbd) * 100;

        return (
          <div key={c.supplier}>
            <div className="flex justify-between text-xs text-slate-400">
              <span>{c.supplier}</span>
              <span>
                {c.used_mbd.toFixed(2)} / {c.max_mbd.toFixed(2)}
              </span>
            </div>

            <div className="h-2 bg-slate-700 rounded mt-1 overflow-hidden">
              <div
                className="h-full bg-blue-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  </div>
);

// ── MAIN PAGE ──────────────────────────────────────────────────────
const ProcurementPage: React.FC = () => {
  const { playbookReady } = useAppContext();

  const [procurement, setProcurement] =
    useState<ProcurementResponse | null>(null);

  const [prices, setPrices] = useState<PricesResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [p, pr] = await Promise.all([
          getProcurementOptions(),
          getLivePrices(),
        ]);

        setProcurement(p);
        setPrices(pr);
      } finally {
        setLoading(false);
      }
    };

    load();
  }, []);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl text-white">Procurement Dashboard</h1>
        <p className="text-slate-400 text-sm">
          Supply analysis & evaluation engine
        </p>
      </div>

      {/* Panel 1 */}
      <section className="mb-6">
        <SpotPricePanel prices={prices} loading={loading} />
      </section>

      {/* Panel 2 */}
      <section className="mb-6">
        {loading ? (
          <div className="h-32 bg-slate-800 rounded-xl animate-pulse" />
        ) : (
          <SupplierRankingTable
            options={procurement?.options ?? []}
          />
        )}
      </section>

      {/* Panel 3 */}
      <section className="mb-6">
        <ContractHeadroomPanel />
      </section>

      {/* Panel 4 — Animation */}
      <section className="mb-6">
        {loading ? (
          <div className="h-48 bg-slate-800 rounded-xl animate-pulse" />
        ) : (
          <RejectionTraceAnimation
            options={procurement?.options ?? []}
            autoPlay={true}
            replayTrigger={playbookReady}
          />
        )}
      </section>
    </div>
  );
};

export default ProcurementPage;