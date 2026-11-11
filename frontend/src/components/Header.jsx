import React from "react";
import { LogoIcon, DatabaseIcon, GitBranchIcon, SparklesIcon } from "./Icons.jsx";

export default function Header({ connectedRepoId, isIndexing, isQuerying }) {
  return (
    <header className="app-header-nav">
      <div className="brand-group">
        <div className="brand-logo-wrapper">
          <LogoIcon size={22} className="brand-logo-icon" />
          <div className="brand-glow" />
        </div>
        <div className="brand-meta">
          <div className="brand-name-row">
            <span className="brand-name">CodeLens</span>
            <span className="brand-badge-ai">AI</span>
            <span className="version-pill">v1.0</span>
          </div>
          <span className="brand-tagline">
            Adaptive Context Optimization &amp; Ranking
          </span>
        </div>
      </div>

      <div className="header-status-group">
        {isIndexing ? (
          <div className="status-indicator-pill indexing">
            <span className="status-dot pulsing-yellow" />
            <span className="status-text">Parsing AST &amp; Graphs…</span>
          </div>
        ) : isQuerying ? (
          <div className="status-indicator-pill querying">
            <span className="status-dot pulsing-teal" />
            <span className="status-text">Running ACOR…</span>
          </div>
        ) : connectedRepoId ? (
          <div className="status-indicator-pill connected">
            <span className="status-dot active-green" />
            <GitBranchIcon size={12} className="status-icon" />
            <span className="status-text">
              Connected: <strong>{connectedRepoId}</strong>
            </span>
          </div>
        ) : (
          <div className="status-indicator-pill idle">
            <span className="status-dot idle-gray" />
            <span className="status-text">No Repository Loaded</span>
          </div>
        )}

        <div className="header-actions">
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noreferrer"
            className="header-link-btn"
            title="Open Interactive FastAPI Documentation"
          >
            <DatabaseIcon size={13} />
            <span>API Docs</span>
          </a>
        </div>
      </div>
    </header>
  );
}

