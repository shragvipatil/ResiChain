import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AppProvider } from "./context/AppContext";
import MinistryPage    from "./pages/MinistryPage";
import ProcurementPage from "./pages/ProcurementPage";
import RefineryPage    from "./pages/RefineryPage";
import AdminPage       from "./pages/AdminPage";
import LoginPage       from "./pages/LoginPage";
import PlaybookPage    from "./pages/PlaybookPage";

const App: React.FC = () => (
  <AppProvider>
    <BrowserRouter>
      <Routes>
        <Route path="/login"      element={<LoginPage />} />
        <Route path="/ministry"   element={<MinistryPage />} />
        <Route path="/procurement"element={<ProcurementPage />} />
        <Route path="/playbook"   element={<PlaybookPage />} />
        <Route path="/refinery"   element={<RefineryPage />} />
        <Route path="/admin"      element={<AdminPage />} />
        <Route path="/"           element={<Navigate to="/ministry" replace />} />
      </Routes>
    </BrowserRouter>
  </AppProvider>
);

export default App;