/**
 * RefineryPage.tsx — Day 12 deliverable (Person C)
 *
 * Four panels:
 *   1. Crude grade availability — per refinery, which grades are
 *      available/disrupted/reduced right now
 *   2. Tanker ETA — vessel name, position, cargo, arrival estimate
 *   3. Grade switch feasibility — from Neo4j COMPATIBLE_WITH traversal
 *   4. Daily delivery schedule — next 14 days
 *
 * Day 13+: replace mock endpoints with real API calls — no component changes.
 */

import React, { useEffect, useState } from "react";
import AppLayout from "../components/AppLayout";
import {
  getRefineryGrades, getTankerETAs, getGradeSwitchOptions, getDeliverySchedule,
} from "../api/endpoints";
import {
  RefineryGradeInfo, TankerETA, GradeSwitchOption, DeliveryScheduleDay,
} from "../types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function gradeStatusStyle(status: string) {
  switch (status) {
    case "available": return { dot: "bg-green-400", text: "text-green-400", badge: "bg-green-900/50 border-green-800" };
    case "reduced":   return { dot: "bg-amber-400",  text: "text-amber-400", badge: "bg-amber-900/50 border-amber-800" };
    default:          return { dot: "bg-red-400",    text: "text-red-400",   badge: "bg-red-900/50 border-red-800" };
  }
}

function tankerStatusBadge(status: string) {
  switch (status) {
    case "arrived":     return "bg-green-900/50 text-green-400 border-green-800";
    case "delayed":     return "bg-red-900/50 text-red-400 border-red-800";
    default:            return "bg-blue-900/50 text-blue-400 border-blue-800";
  }
}

function formatEta(iso: string): string {
  const days = Math.round((new Date(iso).getTime() - Date.now()) / 86400000);
  return `${days}d · ${new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

// ── Panel 1: Grade Availability ───────────────────────────────────────────────

const GradeAvailabilityPanel: React.FC<{ data: RefineryGradeInfo[] }> = ({ data }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Crude Grade Availability</h2>
      <p className="text-slate-500 text-xs mt-0.5">Per refinery, by grade</p>
    </div>
    <div className="p-5 space-y-5">
      {data.map((refinery) => (
        <div key={refinery.refinery_id}>
          <p className="text-white text-sm font-medium mb-2">{refinery.refinery_name}</p>
          <div className="space-y-1.5">
            {refinery.grades.map((g) => {
              const s = gradeStatusStyle(g.status);
              return (
                <div key={g.grade} className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <div className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                    <span className="text-slate-300">{g.grade}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {g.note && <span className="text-slate-600 italic max-w-xs truncate">{g.note}</span>}
                    <span className={`${s.text} tabular-nums font-medium`}>{g.volume_mbd.toFixed(2)} Mb/d</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  </div>
);

// ── Panel 2: Tanker ETA ───────────────────────────────────────────────────────

const TankerEtaPanel: React.FC<{ data: TankerETA[] }> = ({ data }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Tanker ETA</h2>
      <p className="text-slate-500 text-xs mt-0.5">Live AIS positions · {data.length} vessels inbound</p>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700">
            {["Vessel", "Type", "Origin", "Destination", "Cargo", "Volume", "ETA", "Status"].map((h) => (
              <th key={h} className="text-left text-slate-500 text-xs font-medium px-4 py-3 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((t) => (
            <tr key={t.vessel_name} className="border-b border-slate-700/50">
              <td className="px-4 py-3 text-white font-medium whitespace-nowrap">{t.vessel_name}</td>
              <td className="px-4 py-3 text-slate-400 text-xs">{t.vessel_type}</td>
              <td className="px-4 py-3 text-slate-400 text-xs">{t.origin}</td>
              <td className="px-4 py-3 text-slate-300 text-xs">{t.destination_port}</td>
              <td className="px-4 py-3 text-slate-300 text-xs">{t.cargo_grade}</td>
              <td className="px-4 py-3 text-slate-400 text-xs tabular-nums">{t.volume_mbd.toFixed(2)} Mb/d</td>
              <td className="px-4 py-3 text-slate-300 text-xs tabular-nums">{formatEta(t.eta)}</td>
              <td className="px-4 py-3">
                <span className={`text-xs px-2 py-0.5 rounded border font-medium ${tankerStatusBadge(t.status)}`}>
                  {t.status.replace("_", " ")}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </div>
);

// ── Panel 3: Grade Switch Feasibility ─────────────────────────────────────────

const GradeSwitchPanel: React.FC<{ data: GradeSwitchOption[] }> = ({ data }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Grade Switch Feasibility</h2>
      <p className="text-slate-500 text-xs mt-0.5">From Knowledge Graph COMPATIBLE_WITH traversal</p>
    </div>
    <div className="p-5 space-y-3">
      {data.map((opt, i) => (
        <div
          key={i}
          className={`rounded-lg border p-3 ${opt.feasible ? "border-green-800 bg-green-900/10" : "border-red-800 bg-red-900/10"}`}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-white font-medium">{opt.refinery_name}</span>
              <span className="text-slate-500">·</span>
              <span className="text-slate-300">{opt.from_grade}</span>
              <span className="text-slate-500">→</span>
              <span className="text-slate-300">{opt.to_grade}</span>
            </div>
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${opt.feasible ? "text-green-400" : "text-red-400"}`}>
              {opt.feasible ? "✓ FEASIBLE" : "✕ NOT FEASIBLE"}
            </span>
          </div>
          <p className="text-slate-500 text-xs mt-1.5">
            {opt.reason}
            {opt.switch_time_days != null && (
              <span className="text-slate-400"> · switch time: {opt.switch_time_days}d</span>
            )}
          </p>
        </div>
      ))}
    </div>
  </div>
);

// ── Panel 4: Delivery Schedule ─────────────────────────────────────────────────

const DeliverySchedulePanel: React.FC<{ data: DeliveryScheduleDay[] }> = ({ data }) => (
  <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
    <div className="px-5 py-4 border-b border-slate-700">
      <h2 className="text-white text-sm font-medium">Delivery Schedule</h2>
      <p className="text-slate-500 text-xs mt-0.5">Next 14 days</p>
    </div>
    <div className="overflow-x-auto max-h-96 overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-slate-800">
          <tr className="border-b border-slate-700">
            {["Date", "Refinery", "Grade", "Source", "Volume", "Status"].map((h) => (
              <th key={h} className="text-left text-slate-500 text-xs font-medium px-4 py-2.5 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((d, i) => (
            <tr key={i} className="border-b border-slate-700/50">
              <td className="px-4 py-2.5 text-slate-300 text-xs tabular-nums whitespace-nowrap">
                {new Date(d.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </td>
              <td className="px-4 py-2.5 text-white text-xs">{d.refinery_name}</td>
              <td className="px-4 py-2.5 text-slate-300 text-xs">{d.grade}</td>
              <td className="px-4 py-2.5 text-slate-400 text-xs">{d.source}</td>
              <td className="px-4 py-2.5 text-slate-300 text-xs tabular-nums">{d.volume_mbd.toFixed(2)} Mb/d</td>
              <td className="px-4 py-2.5">
                <span className={`text-xs px-1.5 py-0.5 rounded ${d.confirmed ? "text-green-400" : "text-slate-500"}`}>
                  {d.confirmed ? "Confirmed" : "Projected"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </div>
);

// ── Page ──────────────────────────────────────────────────────────────────────

const RefineryPage: React.FC = () => {
  const [grades, setGrades]     = useState<RefineryGradeInfo[]>([]);
  const [tankers, setTankers]   = useState<TankerETA[]>([]);
  const [switches, setSwitches] = useState<GradeSwitchOption[]>([]);
  const [schedule, setSchedule] = useState<DeliveryScheduleDay[]>([]);
  const [loading, setLoading]   = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    Promise.all([
      getRefineryGrades(), getTankerETAs(), getGradeSwitchOptions(), getDeliverySchedule(),
    ]).then(([g, t, s, sch]) => {
      setGrades(g); setTankers(t); setSwitches(s); setSchedule(sch);
      setLoading(false);
    }).catch(() => {
      // Outer safety net — the 4 functions above already fall back to
      // mock data internally on failure, but this guards against the
      // whole page crashing if that internal fallback is ever missing
      // or something else in the chain throws unexpectedly.
      setLoading(false);
      setLoadError(true);
    });
  }, []);

  if (loading) {
    return (
      <AppLayout showRiskStrip={false}>
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-40 bg-slate-800 rounded-xl border border-slate-700 animate-pulse" />
          ))}
        </div>
      </AppLayout>
    );
  }

  if (loadError) {
    return (
      <AppLayout showRiskStrip={false}>
        <div className="flex items-center justify-center">
          <div className="bg-slate-800 border border-red-800 rounded-xl p-6 max-w-md text-center">
            <p className="text-red-400 text-sm font-medium mb-1">Unable to load refinery data</p>
            <p className="text-slate-500 text-xs">Backend may be unreachable.</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 text-xs text-blue-400 hover:underline"
            >
              Retry
            </button>
          </div>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout showRiskStrip={false}>
      <div className="mb-8">
        <h1 className="text-2xl font-medium text-white">Refinery Operations</h1>
        <p className="text-slate-400 text-sm mt-1">Grade availability · Tanker ETAs · Switch feasibility · Delivery schedule</p>
      </div>

      <div className="grid grid-cols-2 gap-6 mb-6">
        <GradeAvailabilityPanel data={grades} />
        <GradeSwitchPanel data={switches} />
      </div>

      <div className="mb-6">
        <TankerEtaPanel data={tankers} />
      </div>

      <div className="mb-6">
        <DeliverySchedulePanel data={schedule} />
      </div>
    </AppLayout>
  );
};

export default RefineryPage;