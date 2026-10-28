import { lazy, Suspense, type ReactNode } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./Shell";
import { ErrorBoundary } from "./ErrorBoundary";
import { HomePage } from "@/features/home/HomePage";
import { LoginPage } from "@/features/auth/LoginPage";
import { useAuth } from "@/store/auth";
import { canOpen } from "./access";

const EvaluatePage    = lazy(() => import("@/features/evaluate/EvaluatePage").then(m => ({ default: m.EvaluatePage })));
const ComparePage     = lazy(() => import("@/features/compare/ComparePage").then(m => ({ default: m.ComparePage })));
const DriftPage       = lazy(() => import("@/features/drift/DriftPage").then(m => ({ default: m.DriftPage })));
const ExplainPage     = lazy(() => import("@/features/explain/ExplainPage").then(m => ({ default: m.ExplainPage })));
const CopilotPage     = lazy(() => import("@/features/copilot/CopilotPage").then(m => ({ default: m.CopilotPage })));
const CalibrationPage = lazy(() => import("@/features/calibration/CalibrationPage").then(m => ({ default: m.CalibrationPage })));
const MlopsHealthPage = lazy(() => import("@/features/mlops/MlopsHealthPage").then(m => ({ default: m.MlopsHealthPage })));
const HistoryPage     = lazy(() => import("../features/history/HistoryPage"));
const WebhooksPage    = lazy(() => import("../features/webhooks/WebhooksPage"));
const StatusPage      = lazy(() => import("../features/status/StatusPage"));
const CompliancePage  = lazy(() => import("@/features/compliance/CompliancePage"));
const MapPage         = lazy(() => import("@/features/map/MapPage"));
const ForecastPage    = lazy(() => import("@/features/forecast/ForecastPage"));
const ForecastComparePage = lazy(() => import("@/features/forecast/ForecastComparePage"));
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

/**
 * The route guard: the same rule as the menu (./access.ts), applied before the
 * page's component is rendered -- so a refused page neither mounts nor requests.
 */
function Guarded({ path, children }: { path: string; children: ReactNode }) {
  const user = useAuth((s) => s.user);
  if (!canOpen(path, user)) {
    return (
      <div className="card-body" role="alert" style={{ padding: 32, maxWidth: 560, margin: "0 auto" }}>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Not available</div>
        <h1 className="display" style={{ fontSize: 28, margin: "0 0 12px" }}>Not available for your role</h1>
        <p style={{ color: "var(--muted)" }}>This page is not available to the {user?.role} role.</p>
      </div>
    );
  }
  return <>{children}</>;
}

export function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<Guarded path="/"><HomePage /></Guarded>} />
            <Route path="/evaluate" element={<Guarded path="/evaluate"><EvaluatePage /></Guarded>} />
            <Route path="/compare" element={<Guarded path="/compare"><ComparePage /></Guarded>} />
            <Route path="/drift" element={<Guarded path="/drift"><DriftPage /></Guarded>} />
            <Route path="/explain" element={<Guarded path="/explain"><ExplainPage /></Guarded>} />
            <Route path="/copilot" element={<Guarded path="/copilot"><CopilotPage /></Guarded>} />
            <Route path="/calibration" element={<Guarded path="/calibration"><CalibrationPage /></Guarded>} />
            <Route path="/mlops" element={<Guarded path="/mlops"><MlopsHealthPage /></Guarded>} />
            <Route path="/history" element={<Guarded path="/history"><HistoryPage /></Guarded>} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/compliance" element={<Guarded path="/compliance"><CompliancePage /></Guarded>} />
            <Route path="/map" element={<Guarded path="/map"><MapPage /></Guarded>} />
            <Route path="/forecast" element={<Guarded path="/forecast"><ForecastPage /></Guarded>} />
            <Route path="/forecast/compare" element={<Guarded path="/forecast/compare"><ForecastComparePage /></Guarded>} />
            <Route path="/webhooks" element={<Guarded path="/webhooks"><WebhooksPage /></Guarded>} />
            <Route path="/status" element={<Guarded path="/status"><StatusPage /></Guarded>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
          <Route path="/region/:code" element={<Guarded path="/region/:code"><RegionDetail /></Guarded>} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
