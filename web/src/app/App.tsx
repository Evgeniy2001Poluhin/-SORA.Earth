import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./Shell";
import { HomePage } from "@/features/home/HomePage";
import { EvaluatePage } from "@/features/evaluate/EvaluatePage";
import { ComparePage } from "@/features/compare/ComparePage";
export function App() {
  return (
    <Routes>
      <Route element={<Shell/>}>
        <Route path="/" element={<HomePage/>}/>
        <Route path="/evaluate" element={<EvaluatePage/>}/>
        <Route path="/compare" element={<ComparePage/>}/>
        <Route path="*" element={<Navigate to="/" replace/>}/>
      </Route>
    </Routes>
  );
}
