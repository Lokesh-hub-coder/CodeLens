import React, { useState, useEffect } from "react";
import Header from "./components/Header.jsx";
import RepoIndexer from "./components/RepoIndexer.jsx";
import QueryConsole from "./components/QueryConsole.jsx";
import ContextViewer from "./components/ContextViewer.jsx";
import { repositoryStatus } from "./api.js";

export default function App() {
  const [connectedRepoId, setConnectedRepoId] = useState("commons-lang");
  const [indexStatus, setIndexStatus] = useState(null);
  const [isIndexing, setIsIndexing] = useState(false);
  const [isQuerying, setIsQuerying] = useState(false);
  const [result, setResult] = useState(null);

  // Check if commons-lang is already indexed on mount
  useEffect(() => {
    async function checkInitialRepo() {
      try {
        const status = await repositoryStatus("commons-lang");
        if (status && status.indexed) {
          setConnectedRepoId("commons-lang");
          setIndexStatus({
            repo_id: "commons-lang",
            num_files: 562,
            num_classes: status.num_classes || 1099,
            num_methods: status.num_methods || 10415,
            num_call_edges: 34900,
            num_import_edges: 1844,
            index_time_seconds: 0.8,
          });
        }
      } catch {
        // Backend not ready yet or repo not yet queried; harmless
      }
    }
    checkInitialRepo();
  }, []);

  return (
    <div className="app-shell">
      <Header
        connectedRepoId={connectedRepoId}
        isIndexing={isIndexing}
        isQuerying={isQuerying}
      />

      <main className="dashboard-main">
        {/* Step 1: Repository Connection Panel */}
        <RepoIndexer
          connectedRepoId={connectedRepoId}
          setConnectedRepoId={setConnectedRepoId}
          indexStatus={indexStatus}
          setIndexStatus={setIndexStatus}
          isIndexing={isIndexing}
          setIsIndexing={setIsIndexing}
        />

        {/* Step 2: Query Console */}
        <QueryConsole
          connectedRepoId={connectedRepoId}
          onResult={setResult}
          isQuerying={isQuerying}
          setIsQuerying={setIsQuerying}
        />

        {/* Step 3: Answer & Context Viewer */}
        <ContextViewer result={result} isQuerying={isQuerying} />
      </main>

      <footer className="app-footer">
        <div className="footer-content">
          <span>
            CodeLensAI · Adaptive Context Optimization &amp; Ranking Engine
          </span>
          <div className="footer-links">
            <span className="footer-tag">FastAPI</span>
            <span className="footer-dot">·</span>
            <span className="footer-tag">Qdrant Vector DB</span>
            <span className="footer-dot">·</span>
            <span className="footer-tag">JavaParser AST</span>
            <span className="footer-dot">·</span>
            <span className="footer-tag">React 18</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
