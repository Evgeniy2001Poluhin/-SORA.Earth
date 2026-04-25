import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./Shell";
import { ErrorBoundary } from "./ErrorBoundary";
import { DriftPage } from "../features/drift/DriftPage";
import { HomePage } from "@/features/home/HomePage";
import { EvaluatePage } from "@/features/evaluate/EvaluatePage";
import { ComparePage } from "@/features/compare/ComparePage";
export function App() {
  return (
    <ErrorBoundary>
      <Routes>
      <Route element={<Shell/>}>
        <Route path="/" element={<HomePage/>}/>
        <Route path="/evaluate" element={<EvaluatePage/>}/>
        <Route path="/compare" element={<ComparePage/>}/>
        <Route path="*" element={<Navigate to="/" replace/>}/>
      </Route>
        <Route path="/drift" element={<DriftPage/>}/>
      </Routes>
    </ErrorBoundary>
  );
}
