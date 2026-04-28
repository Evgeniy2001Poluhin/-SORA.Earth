import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./Shell";
import { ErrorBoundary } from "./ErrorBoundary";
import { DriftPage } from "../features/drift/DriftPage";
import { HomePage } from "@/features/home/HomePage";
import { EvaluatePage } from "@/features/evaluate/EvaluatePage";
import { ComparePage } from "@/features/compare/ComparePage";
import { ExplainPage } from "@/features/explain/ExplainPage";
import { CalibrationPage } from "@/features/calibration/CalibrationPage";
import { MlopsHealthPage } from "@/features/mlops/MlopsHealthPage";
import { LoginPage } from "@/features/auth/LoginPage";
export function App() {
  return (
    <ErrorBoundary>
      <Routes>
      <Route element={<Shell/>}>
        <Route path="/" element={<HomePage/>}/>
        <Route path="/evaluate" element={<EvaluatePage/>}/>
        <Route path="/compare" element={<ComparePage/>}/>
        <Route path="/drift" element={<DriftPage/>}/>
        <Route path="/explain" element={<ExplainPage/>}/>
        <Route path="/calibration" element={<CalibrationPage/>}/>
        <Route path="/mlops" element={<MlopsHealthPage/>}/>
        <Route path="/login" element={<LoginPage/>}/>
        <Route path="*" element={<Navigate to="/" replace/>}/>
      </Route>
      </Routes>
    </ErrorBoundary>
  );
}
