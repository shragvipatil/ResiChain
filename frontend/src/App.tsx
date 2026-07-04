/**
 * App.tsx — updated Day 11
 *
 * Added:
 *   - AuthProvider wraps everything (must be outside AppProvider so
 *     Login/logout redirects still have router context)
 *   - Every dashboard route wrapped in ProtectedRoute with its allowed role
 *   - /viewer route added
 *   - Logout is available via the header on each page (Day 11 wiring
 *     inside each page is optional; a shared header is a nice Day 14 polish)
 */

import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AppProvider } from "./context/AppContext";
import { AuthProvider } from "./context/AuthContext";
import ProtectedRoute    from "./components/ProtectedRoute";

import MinistryPage    from "./pages/MinistryPage";
import ProcurementPage from "./pages/ProcurementPage";
import RefineryPage    from "./pages/RefineryPage";
import AdminPage       from "./pages/AdminPage";
import LoginPage       from "./pages/LoginPage";
import PlaybookPage    from "./pages/PlaybookPage";
import ViewerPage      from "./pages/ViewerPage";

const App: React.FC = () => (
  <AuthProvider>
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route path="/ministry" element={
            <ProtectedRoute allow={["MINISTRY_USER"]}>
              <MinistryPage />
            </ProtectedRoute>
          } />

          <Route path="/procurement" element={
            <ProtectedRoute allow={["PROCUREMENT_ANALYST"]}>
              <ProcurementPage />
            </ProtectedRoute>
          } />

          <Route path="/playbook" element={
            <ProtectedRoute allow={["PROCUREMENT_ANALYST", "MINISTRY_USER"]}>
              <PlaybookPage />
            </ProtectedRoute>
          } />

          <Route path="/refinery" element={
            <ProtectedRoute allow={["REFINERY_OPERATOR"]}>
              <RefineryPage />
            </ProtectedRoute>
          } />

          <Route path="/viewer" element={
            <ProtectedRoute allow={["VIEWER"]}>
              <ViewerPage />
            </ProtectedRoute>
          } />

          <Route path="/admin" element={
            <ProtectedRoute allow={["ADMIN"]}>
              <AdminPage />
            </ProtectedRoute>
          } />

          <Route path="/" element={<Navigate to="/login" replace />} />
        </Routes>
      </BrowserRouter>
    </AppProvider>
  </AuthProvider>
);

export default App;