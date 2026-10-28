import React from "react";
import ReactDOM from "react-dom/client";
import { Providers } from "./app/providers";
import { App } from "./app/App";
import "./design/tokens.css";
import "./design/base.css";

import { auth as _client } from "@/api/client";
import { useAuth as _useAuth } from "@/store/auth";
const _devKey = import.meta.env.VITE_DEV_API_KEY as string | undefined;
if (_devKey) _client.setApiKey(_devKey);
// The one bootstrap check of a stored token. SessionBoundary shows a waiting
// screen until it settles, so nothing protected is requested before it.
void _useAuth.getState().hydrate();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Providers><App /></Providers>
  </React.StrictMode>
);
