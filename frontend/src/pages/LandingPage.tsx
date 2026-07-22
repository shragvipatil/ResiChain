/**
 * LandingPage.tsx — Day 20 addition (Person C)
 *
 * Day 20 UI pass v4: added scroll-triggered reveal animations to the
 * three lower sections (How it works, Every corridor watched, One view
 * for every role) via Framer Motion's whileInView + viewport={{ once:
 * true }} — each section's heading and cards pop in once as you scroll
 * to them, staggered by index. Kept to a restrained fade + rise +
 * gentle scale rather than a bouncy spring, per the design pass's
 * "spend boldness in one place" principle — the hero already carries
 * the bigger motion moment.
 */

import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useAppContext } from "../context/AppContext";
import { ROLE_HOME } from "../components/ProtectedRoute";

const CORRIDORS = [
  { key: "Hormuz",  name: "Strait of Hormuz", note: "The narrowest point for Gulf crude — carries the largest single share of India's imports." },
  { key: "Red_Sea", name: "Red Sea",          note: "The route north toward Suez, past the Bab-el-Mandeb strait." },
  { key: "Suez",    name: "Suez Canal",       note: "The shortcut to the Mediterranean and onward." },
  { key: "Cape",    name: "Cape of Good Hope", note: "The long way round, when the shorter routes aren't safe." },
] as const;

const PHASES = [
  { n: "01", title: "Ingest & verify",     body: "Agent 1 pulls disruption signals from GDELT and UKMTO, and cross-checks each one against a second independent source before it's trusted." },
  { n: "02", title: "Score every corridor", body: "Agent 3 recalculates live risk across all four chokepoints every 60 seconds — military incidents, conflict escalation, shipping data." },
  { n: "03", title: "Detect compound risk", body: "When more than one corridor crosses threshold at once, Agent 4 flags it as a compound event — a materially harder problem than a single disruption." },
  { n: "04", title: "Generate the response", body: "Agents 5–8 optimize the strategic reserve, rank alternative suppliers, validate sanctions and contract limits, and draft the full playbook." },
];

const ROLE_CARDS = [
  { role: "MINISTRY_USER" as const,       label: "Command Center", to: "/ministry",    body: "Live risk across every corridor, the shipping map, and the full agent pipeline, in one view." },
  { role: "PROCUREMENT_ANALYST" as const, label: "Procurement",    to: "/procurement", body: "Rank alternative suppliers the moment a corridor turns unreliable, ruled in or out against sanctions and contract limits." },
  { role: "REFINERY_OPERATOR" as const,   label: "Refinery",       to: "/refinery",    body: "Track which crude grades each refinery can still run, and how fast it could switch." },
  { role: "MINISTRY_USER" as const,       label: "Playbook",       to: "/playbook",    body: "Review the agents' recommended response, approve or reject each action, and export it." },
  { role: "ADMIN" as const,               label: "Admin",          to: "/admin",       body: "Full system health — every agent's status, and how deep the event queue is running." },
  { role: "VIEWER" as const,              label: "Viewer",         to: "/viewer",      body: "A read-only view of where things stand, for anyone who needs visibility without needing to act." },
];

function riskColor(score: number | undefined) {
  if (score == null) return "#475569";
  if (score > 0.65) return "#C81E5C";
  if (score > 0.30) return "#C08A3E";
  return "#1B8577";
}

function riskToneDot(score: number | undefined) {
  if (score == null) return "bg-slate-700";
  if (score > 0.65) return "bg-status-critical";
  if (score > 0.30) return "bg-status-caution";
  return "bg-status-normal";
}

// Shared "pop in on scroll" motion props — fade + rise + gentle scale,
// once per element, staggered by index where used.
const popIn = (i = 0) => ({
  initial: { opacity: 0, y: 22, scale: 0.97 },
  whileInView: { opacity: 1, y: 0, scale: 1 },
  viewport: { once: true, amount: 0.3 },
  transition: { duration: 0.45, delay: i * 0.08, ease: "easeOut" },
});

// ── Horizontal risk gauges ──────────────────────────────────────────────────

const RiskGauges: React.FC<{
  riskState: ReturnType<typeof useAppContext>["riskState"];
}> = ({ riskState }) => {
  const [hovered, setHovered] = useState<string | null>(null);
  const scores = CORRIDORS.map((c) => riskState?.corridors?.[c.key]?.risk_score ?? 0);
  const maxScore = Math.max(...scores);
  const highestKey = maxScore > 0 ? CORRIDORS[scores.indexOf(maxScore)].key : null;

  return (
    <div className="relative bg-chart-panel border border-chart-hairline rounded-2xl p-6 md:p-8 overflow-hidden">
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.06]"
        style={{
          backgroundImage: "repeating-linear-gradient(0deg, #4FD1C5 0px, #4FD1C5 1px, transparent 1px, transparent 40px)",
        }}
      />

      <div className="relative flex items-center justify-between mb-8">
        <p className="text-slate-500 text-xs uppercase tracking-widest">Live corridor risk</p>
        <span className="flex items-center gap-1.5 font-mono text-[10px] text-status-live">
          <motion.span
            className="w-1.5 h-1.5 rounded-full bg-status-live"
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 1.6, repeat: Infinity }}
          />
          LIVE
        </span>
      </div>

      <div className="relative space-y-6">
        {CORRIDORS.map((c, i) => {
          const score = riskState?.corridors?.[c.key]?.risk_score;
          const pct = Math.round((score ?? 0) * 100);
          const color = riskColor(score);
          const isHighest = c.key === highestKey;

          return (
            <div
              key={c.key}
              className="cursor-pointer"
              onMouseEnter={() => setHovered(c.key)}
              onMouseLeave={() => setHovered(null)}
            >
              <div className="flex items-baseline justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${riskToneDot(score)}`} />
                  <span className="text-slate-200 text-sm font-medium">{c.name}</span>
                </div>
                <span className="font-mono text-xl tabular-nums" style={{ color }}>
                  {score != null ? `${pct}%` : "—"}
                </span>
              </div>

              <div className="relative h-3 rounded-full bg-chart-navy border border-chart-hairline overflow-hidden">
                <motion.div
                  className="h-full rounded-full"
                  style={{ backgroundColor: color }}
                  initial={{ width: 0 }}
                  animate={{ width: `${Math.max(pct, 2)}%` }}
                  transition={{ duration: 0.9, delay: i * 0.12, ease: "easeOut" }}
                />
                {isHighest && (
                  <motion.div
                    className="absolute top-0 bottom-0 w-10 bg-white/25 blur-[2px]"
                    animate={{ left: ["-15%", "115%"] }}
                    transition={{ duration: 2.4, repeat: Infinity, ease: "linear" }}
                  />
                )}
              </div>

              <AnimatePresence>
                {hovered === c.key && (
                  <motion.p
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.18 }}
                    className="text-slate-500 text-xs leading-relaxed mt-2 overflow-hidden"
                  >
                    {c.note}
                  </motion.p>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, user } = useAuth();
  const { riskState } = useAppContext();

  const primaryCta = isAuthenticated && user
    ? { label: `Continue to ${user.role === "MINISTRY_USER" ? "Command Center" : user.role.replace("_", " ").toLowerCase()}`, to: ROLE_HOME[user.role] }
    : { label: "Sign In", to: "/login" };

  return (
    <div className="min-h-screen bg-chart-navy text-slate-200">
      <header className="px-6 md:px-8 h-14 flex items-center justify-between border-b border-chart-hairline/60 relative z-10">
        <div className="max-w-7xl mx-auto w-full flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-mono text-signal text-[13px] font-semibold tracking-tight">RC</span>
            <span className="w-px h-3.5 bg-chart-hairline" />
            <span className="text-slate-200 text-[13px] font-medium tracking-tight">ResiChain</span>
          </div>
          <button
            onClick={() => navigate(primaryCta.to)}
            className="text-[12.5px] font-medium text-slate-300 hover:text-white px-3 py-1.5 rounded-lg border border-chart-hairline hover:border-signal/50 transition-colors"
          >
            {primaryCta.label} →
          </button>
        </div>
      </header>

      {/* Hero */}
      <section className="relative px-6 md:px-8 py-16 md:py-20 overflow-hidden">
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              "radial-gradient(circle at 70% 30%, rgba(79,209,197,0.10), transparent 55%), radial-gradient(circle at 10% 85%, rgba(200,30,92,0.06), transparent 50%)",
          }}
        />

        <div className="relative max-w-7xl mx-auto grid lg:grid-cols-2 gap-12 items-center">
          <div>
            <motion.p
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className="font-mono text-[11px] tracking-widest text-signal uppercase mb-4"
            >
              National Energy Security · Multi-Agent AI
            </motion.p>

            <motion.h1
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, delay: 0.08 }}
              className="font-serif text-4xl md:text-[3.25rem] font-medium text-white leading-[1.12] mb-5"
            >
              Four chokepoints carry India's crude. One system watches all of them.
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, delay: 0.16 }}
              className="text-slate-400 text-base md:text-lg leading-relaxed mb-10"
            >
              ResiChain turns a raw disruption signal into a verified alert, a live risk
              score, and a ready-to-approve response playbook — automatically, in minutes.
            </motion.p>

            <motion.button
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, delay: 0.24 }}
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.98 }}
              onClick={() => navigate(primaryCta.to)}
              className="bg-signal text-chart-navy font-medium text-sm px-6 py-3 rounded-lg"
            >
              {primaryCta.label}
            </motion.button>
          </div>

          <RiskGauges riskState={riskState} />
        </div>
      </section>

      {/* How it works — pops in on scroll */}
      <section className="px-6 md:px-8 py-16 border-t border-chart-hairline/60">
        <div className="max-w-7xl mx-auto">
          <motion.h2 {...popIn()} className="font-serif text-2xl text-white mb-2">
            How it works
          </motion.h2>
          <motion.p {...popIn(0.5)} className="text-slate-500 text-sm mb-10">
            Eight specialized agents, each handling one stage of the pipeline.
          </motion.p>
          <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-8">
            {PHASES.map((p, i) => (
              <motion.div key={p.n} {...popIn(i)} className="flex gap-4">
                <span className="font-mono text-signal text-sm shrink-0 pt-0.5">{p.n}</span>
                <div>
                  <p className="text-white text-sm font-medium mb-1">{p.title}</p>
                  <p className="text-slate-500 text-sm leading-relaxed">{p.body}</p>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Every corridor watched — pops in on scroll */}
      <section className="px-6 md:px-8 py-16 border-t border-chart-hairline/60">
        <div className="max-w-7xl mx-auto">
          <motion.h2 {...popIn()} className="font-serif text-2xl text-white mb-10">
            Every corridor, watched
          </motion.h2>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {CORRIDORS.map(({ key, name, note }, i) => {
              const score = riskState?.corridors?.[key]?.risk_score;
              return (
                <motion.div
                  key={key}
                  {...popIn(i)}
                  whileHover={{ y: -3 }}
                  className="bg-chart-panel border border-chart-hairline rounded-xl p-5 hover:border-signal/40 transition-colors"
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono text-slate-600 text-[11px]">{String(i + 1).padStart(2, "0")}</span>
                    <span className={`w-1.5 h-1.5 rounded-full ${riskToneDot(score)}`} />
                  </div>
                  <p className="text-white text-sm font-medium mb-2">{name}</p>
                  <p className="text-slate-500 text-xs leading-relaxed">{note}</p>
                </motion.div>
              );
            })}
          </div>
        </div>
      </section>

      {/* One view for every role — pops in on scroll */}
      <section className="px-6 md:px-8 py-16 border-t border-chart-hairline/60">
        <div className="max-w-7xl mx-auto">
          <motion.h2 {...popIn()} className="font-serif text-2xl text-white mb-10">
            One view for every role
          </motion.h2>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {ROLE_CARDS.map((card, i) => (
              <motion.button
                key={card.to}
                {...popIn(i)}
                whileHover={{ y: -3 }}
                onClick={() => navigate(card.to)}
                className="text-left bg-chart-panel border border-chart-hairline rounded-xl p-5 hover:border-signal/50 transition-colors group"
              >
                <p className="text-white text-sm font-medium mb-2 group-hover:text-signal transition-colors">
                  {card.label} →
                </p>
                <p className="text-slate-500 text-xs leading-relaxed">{card.body}</p>
              </motion.button>
            ))}
          </div>
        </div>
      </section>

      <footer className="px-6 md:px-8 h-14 border-t border-chart-hairline/60">
        <div className="max-w-7xl mx-auto h-full flex items-center text-slate-600 text-[11px] font-mono">
          RESICHAIN · ET AI HACKATHON 2026
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;