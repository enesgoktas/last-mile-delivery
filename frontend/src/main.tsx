import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./i18n";
import * as Sentry from "@sentry/react";
import App from "./App";
import { AuthProvider } from "./state/AuthContext";
import { ToastProvider } from "./state/ToastContext";
import { FeaturesProvider } from "./hooks/useFeatures";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";

const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN;
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.VITE_ENVIRONMENT ?? "development",
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.1,
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <AuthProvider>
        <FeaturesProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </FeaturesProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
