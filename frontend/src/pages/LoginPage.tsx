/**
 * LoginPage.tsx — Day 11 deliverable (Person C)
 *
 * Two-step login flow:
 *   Step 1: email + password
 *   Step 2 (MINISTRY_USER / ADMIN only): 6-digit TOTP code
 *
 * On success, redirects to the role's default dashboard:
 *   MINISTRY_USER        -> /ministry
 *   PROCUREMENT_ANALYST  -> /procurement
 *   REFINERY_OPERATOR    -> /refinery
 *   VIEWER                -> /viewer
 *   ADMIN                 -> /admin
 *
 * Demo accounts (mock mode, password "demo123", TOTP "123456"):
 *   ministry@resichain.gov.in       (needs TOTP)
 *   procurement@resichain.gov.in
 *   refinery@resichain.gov.in
 *   viewer@resichain.gov.in
 *   admin@resichain.gov.in          (needs TOTP)
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { UserRole } from "../types";

const ROLE_HOME: Record<UserRole, string> = {
  MINISTRY_USER:        "/ministry",
  PROCUREMENT_ANALYST:  "/procurement",
  REFINERY_OPERATOR:    "/refinery",
  VIEWER:                "/viewer",
  ADMIN:                 "/admin",
};

const DEMO_ACCOUNTS: { email: string; role: string }[] = [
  { email: "ministry@resichain.gov.in",     role: "Ministry (TOTP required)" },
  { email: "procurement@resichain.gov.in",  role: "Procurement Analyst" },
  { email: "refinery@resichain.gov.in",     role: "Refinery Operator" },
  { email: "viewer@resichain.gov.in",       role: "Viewer" },
  { email: "admin@resichain.gov.in",        role: "Admin (TOTP required)" },
];

const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const { login } = useAuth();

  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needsTotp, setNeedsTotp] = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await login({
        email,
        password,
        totp_code: needsTotp ? totpCode : undefined,
      });

      if (res.requires_totp) {
        setNeedsTotp(true);
        setSubmitting(false);
        return;
      }

      navigate(ROLE_HOME[res.user.role] ?? "/ministry", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      setSubmitting(false);
    }
  };

  const fillDemo = (demoEmail: string) => {
    setEmail(demoEmail);
    setPassword("demo123");
    setNeedsTotp(false);
    setTotpCode("");
    setError(null);
  };

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="bg-slate-800 rounded-2xl border border-slate-700 p-8">
          <div className="mb-6">
            <h1 className="text-xl font-medium text-white">ResiChain AI</h1>
            <p className="text-slate-500 text-sm mt-1">Energy Supply Chain Resilience</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {!needsTotp ? (
              <>
                <div>
                  <label className="text-slate-400 text-xs block mb-1.5">Email</label>
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@resichain.gov.in"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5
                      text-white text-sm placeholder:text-slate-600
                      focus:outline-none focus:border-blue-600"
                  />
                </div>
                <div>
                  <label className="text-slate-400 text-xs block mb-1.5">Password</label>
                  <input
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5
                      text-white text-sm placeholder:text-slate-600
                      focus:outline-none focus:border-blue-600"
                  />
                </div>
              </>
            ) : (
              <div>
                <label className="text-slate-400 text-xs block mb-1.5">
                  Authenticator Code
                </label>
                <input
                  type="text"
                  required
                  autoFocus
                  maxLength={6}
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="123456"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5
                    text-white text-lg tracking-[0.4em] text-center placeholder:text-slate-700
                    focus:outline-none focus:border-blue-600"
                />
                <p className="text-slate-600 text-xs mt-2">
                  Enter the 6-digit code from your authenticator app.{" "}
                  <button
                    type="button"
                    onClick={() => { setNeedsTotp(false); setTotpCode(""); }}
                    className="text-blue-400 hover:underline"
                  >
                    Use a different account
                  </button>
                </p>
              </div>
            )}

            {error && (
              <p className="text-red-400 text-xs bg-red-900/20 border border-red-800 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50
                text-white text-sm font-medium rounded-lg py-2.5 transition-colors
                flex items-center justify-center gap-2"
            >
              {submitting && (
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
              )}
              {needsTotp ? "Verify" : "Sign In"}
            </button>
          </form>
        </div>

        {/* Demo account shortcuts — mock mode only */}
        <div className="mt-4 bg-slate-800/50 border border-slate-700/50 rounded-xl p-4">
          <p className="text-slate-500 text-xs mb-2">Demo accounts (password: demo123)</p>
          <div className="space-y-1">
            {DEMO_ACCOUNTS.map((acc) => (
              <button
                key={acc.email}
                onClick={() => fillDemo(acc.email)}
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-slate-700/50 transition-colors
                  flex items-center justify-between group"
              >
                <span className="text-slate-400 text-xs group-hover:text-slate-200">{acc.email}</span>
                <span className="text-slate-600 text-xs">{acc.role}</span>
              </button>
            ))}
          </div>
          <p className="text-slate-700 text-xs mt-2">TOTP code for demo accounts: 123456</p>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;