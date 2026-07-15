import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import App from "./App";
import AppErrorBoundary from "./AppErrorBoundary";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>
);
