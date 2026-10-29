import React from "react";
import ReactDOM from "react-dom/client";
import WorkbenchPage from "./pages/WorkbenchPage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkbenchPage />
  </React.StrictMode>,
);
