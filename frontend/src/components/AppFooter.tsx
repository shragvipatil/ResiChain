/**
 * AppFooter.tsx
 *
 * Shared footer for authenticated pages. Informational only — build
 * identity, connection state, and current time. No decorative content.
 */

import React, { useEffect, useState } from "react";
import { useAppContext } from "../context/AppContext";

const AppFooter: React.FC = () => {
  const { wsConnected } = useAppContext();
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <footer className="mt-auto border-t border-slate-800/80 px-6 md:px-8 h-10 flex items-center justify-between text-[10.5px] text-slate-600">
      <span className="font-mono tracking-wide">
        RESICHAIN <span className="text-slate-700">·</span> ET AI HACKATHON 2026
      </span>
      <div className="flex items-center gap-4 font-mono">
        <span className="flex items-center gap-1.5">
          <span className={`w-1 h-1 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-slate-700"}`} />
          {wsConnected ? "Connected" : "Offline"}
        </span>
        <span className="tabular-nums text-slate-700">
          {now.toLocaleTimeString(undefined, { hour12: false })}
        </span>
      </div>
    </footer>
  );
};

export default AppFooter;
