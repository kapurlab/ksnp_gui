import React, { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";

const params = new URLSearchParams(window.location.search);
const view = params.get("view");

const TreeStandalone = lazy(() => import("./TreeStandalone.jsx"));

const Fallback = () => (
  <div style={{ padding: "1rem", fontFamily: "system-ui" }}>Loading viewer…</div>
);

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) { console.error("Standalone viewer error:", err, info); }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: "1rem", fontFamily: "system-ui", color: "#c0392b" }}>
          <strong>Tree viewer failed to load</strong>
          <pre style={{ whiteSpace: "pre-wrap" }}>{String(this.state.err.message || this.state.err)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

const root = createRoot(document.getElementById("root"));
if (view === "tree") {
  root.render(
    <ErrorBoundary>
      <Suspense fallback={<Fallback />}>
        <TreeStandalone />
      </Suspense>
    </ErrorBoundary>
  );
} else {
  root.render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}
