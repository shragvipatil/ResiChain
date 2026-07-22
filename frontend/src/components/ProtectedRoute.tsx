/**
 * ProtectedRoute.tsx — Day 11 deliverable (Person C)
 *
 * Wraps a route element. Blocks access unless:
 *   1. User is authenticated (redirects to /login otherwise)
 *   2. User's role is in the allowed list, if one is given
 *      (redirects to their own default dashboard otherwise —
 *       does NOT show a generic "unauthorized" page, per the
 *       plan: each role has one home, viewing another role's
 *       page just bounces you back to your own)
 *
 * Usage:
 *   <Route path="/ministry" element={
 *     <ProtectedRoute allow={["MINISTRY_USER", "ADMIN"]}>
 *       <MinistryPage />
 *     </ProtectedRoute>
 *   } />
 */

import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { UserRole } from "../types";

export const ROLE_HOME: Record<UserRole, string> = {
  MINISTRY_USER:        "/ministry",
  PROCUREMENT_ANALYST:  "/procurement",
  REFINERY_OPERATOR:    "/refinery",
  VIEWER:                "/viewer",
  ADMIN:                 "/admin",
};

interface ProtectedRouteProps {
  children: React.ReactNode;
  allow?:   UserRole[];   // if omitted, any authenticated user may view
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children, allow }) => {
  const { user, loading, isAuthenticated } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen bg-chart-navy flex items-center justify-center">
        <svg className="w-6 h-6 animate-spin text-slate-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
        </svg>
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  const roleAllowed = !allow || allow.includes(user.role) || user.role === "ADMIN";

  if (!roleAllowed) {
    return <Navigate to={ROLE_HOME[user.role]} replace />;
  }

  return <>{children}</>;
};

export default ProtectedRoute;