/**
 * App.tsx — updated Day 20
 *
 * "/" now serves a real landing page instead of an instant redirect
 * to /login. Everything else is unchanged.
 */

import React from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppProvider } from "./context/AppContext";
import { AuthProvider } from "./context/AuthContext";
import ProtectedRoute    from "./components/ProtectedRoute";

import LandingPage     from "./pages/LandingPage";
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
          <Route path="/" element={<LandingPage />} />
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
        </Routes>
      </BrowserRouter>
    </AppProvider>
  </AuthProvider>
);

export default App;