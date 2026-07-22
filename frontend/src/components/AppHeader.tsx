/**
 * AppHeader.tsx
 *
 * Shared navigation header used across every authenticated page.
 *
 * Day 20 UI pass: nautical-chart palette (see tailwind.config.js —
 * chart.navy/panel/hairline, status.critical/caution/normal/live).
 * Critical uses IHO chart-standard magenta (#C81E5C), the real
 * Admiralty convention for traffic separation/danger zones — chosen
 * deliberately over a generic red, grounded in what this app actually
 * monitors (maritime chokepoints). The one signature element: the
 * connection indicator is a compass needle, not a plain dot — sweeps
 * slowly when live, pulses when reconnecting, sits still when offline.
 * All logic (props, hooks, handlers) is unchanged from the prior version.
 */

import React from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useAppContext } from "../context/AppContext";
import { UserRole } from "../types";

interface NavLink {
  to: string;
  label: string;
  allow: UserRole[];
}

const NAV_LINKS: NavLink[] = [
  { to: "/ministry",    label: "Command Center", allow: ["MINISTRY_USER"] },
  { to: "/procurement", label: "Procurement",    allow: ["PROCUREMENT_ANALYST"] },
  { to: "/playbook",    label: "Playbook",       allow: ["PROCUREMENT_ANALYST", "MINISTRY_USER"] },
  { to: "/refinery",    label: "Refinery",       allow: ["REFINERY_OPERATOR"] },
  { to: "/admin",       label: "Admin",          allow: ["ADMIN"] },
  { to: "/viewer",      label: "Viewer",         allow: ["VIEWER"] },
];

const CORRIDOR_ORDER = ["Hormuz", "Red_Sea", "Suez", "Cape"] as const;
const CORRIDOR_LABEL: Record<string, string> = {
  Hormuz: "Hormuz",
  Red_Sea: "Red Sea",
  Suez: "Suez",
  Cape: "Cape",
};

function riskTone(score: number | undefined) {
  if (score == null) return { text: "text-slate-600", bar: "bg-slate-700" };
  if (score > 0.65) return { text: "text-status-critical", bar: "bg-status-critical" };
  if (score > 0.30) return { text: "text-status-caution", bar: "bg-status-caution" };
  return { text: "text-status-normal", bar: "bg-status-normal" };
}

// Small compass-needle SVG — the header's one signature element.
// Sweeps when live, pulses when reconnecting, dims when offline.
const CompassIndicator: React.FC<{ state: "live" | "reconnecting" | "offline" }> = ({ state }) => {
  const color =
    state === "live" ? "text-status-live"
    : state === "reconnecting" ? "text-status-caution"
    : "text-slate-600";

  return (
    <svg
      viewBox="0 0 24 24"
      className={`w-3.5 h-3.5 ${color} ${state === "reconnecting" ? "animate-pulse" : ""}`}
      style={state === "live" ? { animation: "compassSweep 6s linear infinite" } : undefined}
    >
      <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.35" />
      <path d="M12 3 L14 12 L12 21 L10 12 Z" fill="currentColor" opacity="0.9" />
      <circle cx="12" cy="12" r="1.3" fill="currentColor" />
    </svg>
  );
};

interface AppHeaderProps {
  showRiskStrip?: boolean;
}

const AppHeader: React.FC<AppHeaderProps> = ({ showRiskStrip = true }) => {
  const { user, logout } = useAuth();
  const { riskState, wsConnected, wsReconnecting } = useAppContext();
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const visibleLinks = NAV_LINKS.filter(
    (link) => !user?.role || link.allow.includes(user.role)
  );

  const connState: "live" | "reconnecting" | "offline" =
    wsConnected ? "live" : wsReconnecting ? "reconnecting" : "offline";

  return (
    <header className="sticky top-0 z-30 bg-chart-navy/90 backdrop-blur-md border-b border-chart-hairline/80">
      {/* Row 1 — wordmark, nav, identity */}
      <div className="px-6 md:px-8 h-14 flex items-center justify-between gap-6">
        <div className="flex items-center gap-7 min-w-0">
          <button
            onClick={() => navigate("/")}
            className="flex items-center gap-2 shrink-0 group"
          >
            <span className="font-mono text-signal text-[13px] font-semibold tracking-tight">
              RC
            </span>
            <span className="w-px h-3.5 bg-chart-hairline" />
            <span className="text-slate-200 text-[13px] font-medium tracking-tight group-hover:text-white transition-colors">
              ResiChain
            </span>
          </button>

          <nav className="hidden md:flex items-center gap-0.5">
            {visibleLinks.map((link) => {
              const active = location.pathname === link.to;
              return (
                <button
                  key={link.to}
                  onClick={() => navigate(link.to)}
                  className={`relative px-2.5 py-1.5 text-[12.5px] font-medium transition-colors ${
                    active ? "text-white" : "text-slate-500 hover:text-slate-200"
                  }`}
                >
                  {link.label}
                  {active && (
                    <span className="absolute left-2.5 right-2.5 -bottom-[1px] h-px bg-signal" />
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-4 shrink-0">
          <div className="flex items-center gap-1.5 font-mono">
            <CompassIndicator state={connState} />
            <span className={`text-[10px] uppercase tracking-wider ${
              connState === "live" ? "text-slate-500"
              : connState === "reconnecting" ? "text-status-caution"
              : "text-slate-500"
            }`}>
              {connState === "live" ? "Live" : connState === "reconnecting" ? "Reconnecting…" : "Offline"}
            </span>
          </div>

          <div className="flex items-center gap-2.5 pl-4 border-l border-chart-hairline">
            <div className="text-right leading-none hidden sm:block">
              <p className="text-slate-300 text-[12px]">{user?.name ?? "—"}</p>
              <p className="text-slate-600 text-[10px] mt-0.5 font-mono uppercase tracking-wide">
                {user?.role?.replace("_", " ") ?? ""}
              </p>
            </div>
            <button
              onClick={handleLogout}
              className="text-slate-500 hover:text-status-critical text-[11px] font-medium transition-colors px-2 py-1 rounded hover:bg-chart-panel/60"
            >
              Sign out
            </button>
          </div>
        </div>
      </div>

      {/* Row 2 — live corridor risk strip, in physical route order */}
      {showRiskStrip && (
        <div className="px-6 md:px-8 h-8 bg-black/20 border-t border-chart-hairline/60 flex items-center gap-6 overflow-x-auto">
          {CORRIDOR_ORDER.map((corridor, i) => {
            const score = riskState?.corridors?.[corridor]?.risk_score;
            const tone = riskTone(score);
            return (
              <div key={corridor} className="flex items-center gap-2 shrink-0">
                <span className="font-mono text-slate-700 text-[10px]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="text-slate-500 text-[11px]">{CORRIDOR_LABEL[corridor]}</span>
                <div className="w-8 h-[3px] rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${tone.bar}`}
                    style={{ width: score != null ? `${Math.round(score * 100)}%` : "0%" }}
                  />
                </div>
                <span className={`font-mono text-[11px] tabular-nums ${tone.text}`}>
                  {score != null ? `${Math.round(score * 100)}%` : "—"}
                </span>
              </div>
            );
          })}
          {riskState?.system_mode && riskState.system_mode !== "NORMAL" && (
            <span className="ml-auto shrink-0 font-mono text-[10px] font-medium px-2 py-0.5 rounded bg-status-critical/20 text-status-critical border border-status-critical/40 tracking-wide">
              {riskState.system_mode}
            </span>
          )}
        </div>
      )}
    </header>
  );
};

export default AppHeader;