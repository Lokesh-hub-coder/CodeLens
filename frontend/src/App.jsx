import { useState } from "react";
import QueryPanel from "./components/QueryPanel.jsx";
import ContextViewer from "./components/ContextViewer.jsx";

export default function App() {
  const [result, setResult] = useState(null);

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>
          CodeLens<span className="accent">AI</span>
        </h1>
        <span className="tagline">adaptive context optimization &amp; ranking</span>
      </header>

      <QueryPanel onResult={setResult} />
      <ContextViewer result={result} />
    </div>
  );
}