/**
 * api/client.ts — updated Day 11
 *
 * Auth changed from localStorage token to httpOnly cookie:
 *   - withCredentials: true tells the browser to send/receive cookies
 *     cross-origin (localhost:3000 -> localhost:8000).
 *   - No Authorization header is set manually — the httpOnly cookie
 *     is attached automatically by the browser on every request.
 *   - This is deliberately more secure than localStorage: an XSS attack
 *     cannot read an httpOnly cookie via JavaScript.
 *
 * Backend requirement (Day 11, Person A):
 *   POST /api/auth/login must respond with:
 *     Set-Cookie: access_token=<jwt>; HttpOnly; SameSite=Lax; Path=/
 *   and the FastAPI CORS middleware must have allow_credentials=True
 *   (already true in main.py) plus an explicit origin (already set,
 *   not "*", which is required for credentialed requests to work).
 */

import axios from "axios";

export const USE_MOCK = false;

// Real backend auth endpoints (/api/auth/login, /api/auth/me, etc.) are not
// built yet — confirmed via live 404 on /api/auth/login. Keep auth on mock
// login independently of the main USE_MOCK flag so role-based routing still
// works while every other endpoint hits the real running backend.
export const AUTH_USE_MOCK = true;

// REFINERY_USE_MOCK: true — backend routes /refinery/* confirmed 404.
// These routes were not built by Person A/B within the project timeline.
// RefineryPage correctly falls back to representative mock data so the
// dashboard renders meaningfully during the demo.
// Flip to false only after Person A adds the corresponding FastAPI routers.
export const REFINERY_USE_MOCK = true;

// ADMIN_USE_MOCK: true — /admin/system-health confirmed 404 on real backend.
// AdminPage renders representative mock health data during the demo.
// Flip to false only after Person A adds the /admin/system-health FastAPI route.
export const ADMIN_USE_MOCK = true;

export const apiClient = axios.create({
  baseURL: "http://localhost:8000/api",
  headers: { "Content-Type": "application/json" },
  withCredentials: true,   // send/receive httpOnly cookies cross-origin
});

// No request interceptor needed anymore — the browser attaches the
// httpOnly cookie automatically. Manually setting Authorization here
// would be redundant and the cookie can't be read from JS anyway.

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Cookie is invalid/expired/blacklisted — server has already
      // rejected it. Just redirect; there is no client-side token to clear.
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);