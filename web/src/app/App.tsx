import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./Shell";
import { ErrorBoundary } from "./ErrorBoundary";
import { HomePage } from "@/features/home/HomePage";
import { LoginPage } from "@/features/auth/LoginPage";

const EvaluatePage    = lazy(() => import("@/features/evaluate/EvaluatePage").then(m => ({ default: m.EvaluatePage })));
const ComparePage     = lazy(() => import("@/features/compare/ComparePage").then(m => ({ default: m.ComparePage })));
const DriftPage       = lazy(() => import("@/features/drift/DriftPage").then(m => ({ default: m.DriftPage })));
const ExplainPage     = lazy(() => import("@/features/explain/ExplainPage").then(m => ({ default: m.ExplainPage })));
const CopilotPage     = lazy(() => import("@/features/copilot/CopilotPage").then(m => ({ default: m.CopilotPage })));
const CalibrationPage = lazy(() => import("@/features/calibration/CalibrationPage").then(m => ({ default: m.CalibrationPage })));
const MlopsHealthPage = lazy(() => import("@/features/mlops/MlopsHealthPage").then(m => ({ default: m.MlopsHealthPage })));
const HistoryPage     = lazy(() => import("../features/history/HistoryPage"));
const CompliancePage  = lazy(() => import("@/features/compliance/CompliancePage"));
const MapPage         = lazy(() => import("@/features/map/MapPage"));
const RegionDetail    = lazy(() => import("../features/region/RegionDetail"));

function PageLoader() {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "center",
      minHeight: 480, color: "var(--muted)", fontSize: 13,
      fontFamily: "var(--f-mono)", letterSpacing: "0.08em"
    }}>
      loading...
    </div>
  );
}

export function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/evaluate" element={<EvaluatePage />} />
            <Route path="/compare" element={<ComparePage />} />
            <Route path="/drift" element={<DriftPage />} />
            <Route path="/explain" element={<ExplainPage />} />
            <Route path="/copilot" element={<CopilotPage />} />
            <Route path="/calibration" element={<CalibrationPage />} />
            <Route path="/mlops" element={<MlopsHealthPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/compliance" element={<CompliancePage />} />
            <Route path="/map" element={<MapPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
