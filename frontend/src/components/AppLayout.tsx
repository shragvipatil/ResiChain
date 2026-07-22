/**
 * AppLayout.tsx
 *
 * Wraps every authenticated page with the shared AppHeader + AppFooter.
 * Pages keep their own body content; this only replaces the inline
 * header block each page used to duplicate.
 *
 * Usage:
 *   <AppLayout>
 *     <YourPageContent />
 *   </AppLayout>
 */

import React from "react";
import AppHeader from "./AppHeader";
import AppFooter from "./AppFooter";

interface AppLayoutProps {
  children: React.ReactNode;
  showRiskStrip?: boolean;
}

const AppLayout: React.FC<AppLayoutProps> = ({ children, showRiskStrip = true }) => (
  <div className="min-h-screen bg-chart-navy flex flex-col">
    <AppHeader showRiskStrip={showRiskStrip} />
    <main className="flex-1 p-8">{children}</main>
    <AppFooter />
  </div>
);

export default AppLayout;