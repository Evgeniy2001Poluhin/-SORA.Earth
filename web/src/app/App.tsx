import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./Shell";
import { HomePage } from "@/features/home/HomePage";
import { EvaluatePage } from "@/features/evaluate/EvaluatePage";
import { ComparePage } from "@/features/compare/ComparePage";
import { ExplainPage } from "@/features/explain/ExplainPage";
import { RankingPage } from "@/features/ranking/RankingPage";
export function App() {
  return (
    <Routes>
      <Route element={<Shell/>}>
        <Route path="/" element={<HomePage/>}/>
        <Route path="/evaluate" element={<EvaluatePage/>}/>
        <Route path="/compare" element={<ComparePage/>}/>
        <Route path="/explain" element={<ExplainPage/>}/>
        <Route path="/ranking" element={<RankingPage/>}/>
        <Route path="*" element={<Navigate to="/" replace/>}/>
      </Route>
    </Routes>
  );
}
