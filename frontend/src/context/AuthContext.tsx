/**
 * AuthContext.tsx — Day 11 deliverable (Person C)
 *
 * Tracks the currently logged-in user. Does NOT store any token —
 * the access token lives only in an httpOnly cookie the browser
 * manages automatically. This context just tracks "who is the user
 * according to the server" by calling GET /api/auth/me on load.
 *
 * Usage:
 *   const { user, login, logout, loading } = useAuth();
 */

import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { User, LoginRequest, LoginResponse } from "../types";
import { login as loginRequest, logout as logoutRequest, getCurrentUser } from "../api/endpoints";

interface AuthContextType {
  user:        User | null;
  loading:     boolean;         // true while checking session on initial load
  login:       (body: LoginRequest) => Promise<LoginResponse>;
  logout:      () => Promise<void>;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser]       = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // On mount, check if a valid session already exists (cookie still valid)
  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (body: LoginRequest): Promise<LoginResponse> => {
    const res = await loginRequest(body);
    // Only set user if login is fully complete (not mid-TOTP-challenge)
    if (!res.requires_totp) {
      setUser(res.user);
    }
    return res;
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{
      user, loading, login, logout,
      isAuthenticated: user !== null,
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
};