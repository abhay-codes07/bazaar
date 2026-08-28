import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

const saved = (() => {
  const q = new URLSearchParams(window.location.search).get("theme");
  if (q === "dark" || q === "light") return q;
  try {
    return localStorage.getItem("bazaar-theme");
  } catch {
    return null;
  }
})();
if (saved === "dark" || (!saved && window.matchMedia?.("(prefers-color-scheme: dark)").matches)) {
  document.documentElement.setAttribute("data-theme", "dark");
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
