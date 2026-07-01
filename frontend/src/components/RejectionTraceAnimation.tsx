import React, { useEffect, useState, useCallback } from "react";
import { ProcurementOption } from "../types";

// ── Card state machine ─────────────────────────────────────────────
type CardState = "evaluating" | "revealed";

interface CardDisplay {
  option: ProcurementOption;
  state: CardState;
}

// ── Props ───────────────────────────────────────────────────────────
interface RejectionTraceAnimationProps {
  options: ProcurementOption[];
  autoPlay?: boolean;
  replayTrigger?: boolean;
}

// ── Status config ───────────────────────────────────────────────────
const STATUS_CONFIG = {
  BLOCKED: {
    border: "border-red-700",
    bg: "bg-red-950/30",
    label: "BLOCKED",
    badge: "bg-red-900/60 text-red-400 border-red-700",
  },
  PARTIAL: {
    border: "border-amber-700",
    bg: "bg-amber-950/20",
    label: "PARTIAL",
    badge: "bg-amber-900/60 text-amber-400 border-amber-700",
  },
  APPROVED: {
    border: "border-green-700",
    bg: "bg-green-950/20",
    label: "APPROVED",
    badge: "bg-green-900/60 text-green-400 border-green-700",
  },
} as const;

// ── Evaluating card ────────────────────────────────────────────────
const EvaluatingCard: React.FC<{ option: ProcurementOption }> = ({ option }) => (
  <div className="border border-slate-600 bg-slate-800/60 rounded-xl p-4 flex items-center gap-3 animate-pulse">
    <div className="w-7 h-7 rounded-full border-2 border-slate-600 border-t-blue-400 animate-spin" />
    <div>
      <p className="text-slate-300 text-sm">
        Evaluating {option.supplier} — {option.crude_grade}
      </p>
      <p className="text-slate-500 text-xs">Running checks…</p>
    </div>
  </div>
);

// ── Revealed card ───────────────────────────────────────────────────
const RevealedCard: React.FC<{ option: ProcurementOption }> = ({ option }) => {
  const cfg = STATUS_CONFIG[option.status as keyof typeof STATUS_CONFIG];

  return (
    <div className={`border ${cfg.border} ${cfg.bg} rounded-xl p-4`}>
      <div className="flex items-start gap-3">
        <div className="w-3 h-3 mt-1 rounded-full bg-slate-400" />

        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-white text-sm font-medium">
              {option.supplier}
            </span>
            <span className="text-slate-500 text-xs">·</span>
            <span className="text-slate-400 text-xs">
              {option.crude_grade}
            </span>

            <span
              className={`ml-auto text-xs px-2 py-0.5 rounded border ${cfg.badge}`}
            >
              {cfg.label}
            </span>
          </div>

          {option.reason && (
            <div className="mt-2 text-xs text-slate-400">
              <span className="font-mono text-slate-300 bg-slate-900 px-2 py-0.5 rounded border border-slate-700 mr-2">
                {option.reason.rule}
              </span>
              {option.reason.value}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ── Main component ──────────────────────────────────────────────────
const RejectionTraceAnimation: React.FC<RejectionTraceAnimationProps> = ({
  options,
  autoPlay = true,
  replayTrigger,
}) => {
  const [cards, setCards] = useState<CardDisplay[]>([]);
  const [playing, setPlaying] = useState(false);
  const [done, setDone] = useState(false);

  const play = useCallback(() => {
    if (playing || !options.length) return;

    setCards([]);
    setDone(false);
    setPlaying(true);

    options.forEach((option, i) => {
      // show evaluating
      setTimeout(() => {
        setCards((prev) => [
          ...prev,
          { option, state: "evaluating" },
        ]);
      }, i * 800);

      // reveal result
      setTimeout(() => {
        setCards((prev) =>
          prev.map((c, idx) =>
            idx === i ? { ...c, state: "revealed" } : c
          )
        );

        if (i === options.length - 1) {
          setTimeout(() => {
            setDone(true);
            setPlaying(false);
          }, 300);
        }
      }, i * 800 + 600);
    });
  }, [options, playing]);

  // auto play on mount
  useEffect(() => {
    if (autoPlay && options.length > 0) {
      const t = setTimeout(() => play(), 300);
      return () => clearTimeout(t);
    }
  }, [autoPlay, options.length, play]);

  // replay trigger
  useEffect(() => {
    if (replayTrigger) play();
  }, [replayTrigger, play]);

  // summary
  const counts = options.reduce<Record<string, number>>((acc, o) => {
    acc[o.status] = (acc[o.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-700 flex justify-between">
        <div>
          <h2 className="text-white text-sm font-medium">
            Procurement Evaluation
          </h2>
          <p className="text-slate-500 text-xs">
            Animated constraint evaluation
          </p>
        </div>

        <button
          onClick={play}
          disabled={playing}
          className="text-xs border border-slate-600 px-3 py-1 rounded-lg text-slate-300 disabled:opacity-40"
        >
          {playing ? "Playing…" : "Replay"}
        </button>
      </div>

      {/* Body */}
      <div className="p-4 space-y-3">
        {cards.length === 0 && !playing && (
          <p className="text-slate-600 text-sm text-center py-6">
            Click Replay to start animation
          </p>
        )}

        {cards.map((c, i) =>
          c.state === "evaluating" ? (
            <EvaluatingCard key={i} option={c.option} />
          ) : (
            <RevealedCard key={i} option={c.option} />
          )
        )}
      </div>

      {/* Footer summary */}
      {done && (
        <div className="px-5 py-3 border-t border-slate-700 text-xs flex gap-4">
          {counts.APPROVED && (
            <span className="text-green-400">
              {counts.APPROVED} approved
            </span>
          )}
          {counts.PARTIAL && (
            <span className="text-amber-400">
              {counts.PARTIAL} partial
            </span>
          )}
          {counts.BLOCKED && (
            <span className="text-red-400">
              {counts.BLOCKED} blocked
            </span>
          )}
        </div>
      )}
    </div>
  );
};

export default RejectionTraceAnimation;