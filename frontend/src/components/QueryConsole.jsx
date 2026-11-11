import React, { useState } from "react";
import {
  SparklesIcon,
  TerminalIcon,
  CpuIcon,
  ZapIcon,
  AlertCircleIcon,
  ArrowRightIcon,
} from "./Icons.jsx";
import { queryRepository } from "../api.js";

const QUERY_PRESETS = [
  "What methods call containsAny?",
  "Why does parseAmount fail?",
  "find the implementation of isAsciiPrintable",
  "Trace dependencies of StringUtils",
];

const BUDGET_PRESETS = [2000, 4000, 8000, 16000];

export default function QueryConsole({
  connectedRepoId,
  onResult,
  isQuerying,
  setIsQuerying,
}) {
  const [query, setQuery] = useState("");
  const [tokenBudget, setTokenBudget] = useState(8000);
  const [queryError, setQueryError] = useState(null);

  async function handleSubmit(event) {
    if (event) event.preventDefault();

    if (!connectedRepoId) {
      setQueryError("Please connect or index a repository first before querying.");
      return;
    }
    if (!query.trim()) {
      return;
    }

    setIsQuerying(true);
    setQueryError(null);

    try {
      const result = await queryRepository({
        repoId: connectedRepoId,
        query: query.trim(),
        tokenBudget: Number(tokenBudget) || 8000,
      });
      onResult(result);
    } catch (err) {
      setQueryError(err.message || "Failed to execute query.");
    } finally {
      setIsQuerying(false);
    }
  }

  function handleKeyDown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <section className="dashboard-card query-card">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon-badge query-badge">
            <SparklesIcon size={16} />
          </div>
          <div>
            <h2 className="card-title">Ask Your Codebase</h2>
            <p className="card-subtitle">
              Graph-aware neural retrieval with token-budgeted ACOR knapsack ranking
            </p>
          </div>
        </div>

        <div className="card-header-meta">
          <span className="kbd-shortcut-pill">
            <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to run
          </span>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="query-form">
        <div className="query-input-container">
          <textarea
            id="devQuery"
            className="query-textarea"
            placeholder="Ask anything about your repository... (e.g., trace a bug, locate a feature, or inspect call chains)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            required
          />

          <div className="query-suggestions-row">
            <span className="suggestions-label">Suggestions:</span>
            <div className="suggestions-list">
              {QUERY_PRESETS.map((preset, idx) => (
                <button
                  type="button"
                  key={idx}
                  className="suggestion-chip"
                  onClick={() => setQuery(preset)}
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="query-controls-footer">
          <div className="budget-control-group">
            <div className="budget-label-row">
              <span className="budget-label">
                <CpuIcon size={13} />
                <span>Token Budget:</span>
                <strong className="budget-val">
                  {Number(tokenBudget).toLocaleString()} tokens
                </strong>
              </span>
              <div className="budget-presets">
                {BUDGET_PRESETS.map((b) => (
                  <button
                    type="button"
                    key={b}
                    className={`budget-pill ${tokenBudget === b ? "active" : ""}`}
                    onClick={() => setTokenBudget(b)}
                  >
                    {b >= 1000 ? `${b / 1000}k` : b}
                  </button>
                ))}
              </div>
            </div>

            <div className="budget-slider-wrapper">
              <input
                type="range"
                min="1000"
                max="24000"
                step="500"
                value={tokenBudget}
                onChange={(e) => setTokenBudget(Number(e.target.value))}
                className="budget-slider"
              />
            </div>
          </div>

          <div className="query-actions">
            <button
              type="submit"
              className="btn btn-primary ask-btn"
              disabled={isQuerying || !query.trim()}
            >
              {isQuerying ? (
                <>
                  <span className="btn-spinner" />
                  <span>Synthesizing Context…</span>
                </>
              ) : (
                <>
                  <ZapIcon size={15} />
                  <span>Ask CodeLens</span>
                  <ArrowRightIcon size={14} />
                </>
              )}
            </button>
          </div>
        </div>
      </form>

      {queryError && (
        <div className="banner banner-error mt-4">
          <AlertCircleIcon size={16} />
          <div className="banner-text">
            <strong>Query Error:</strong> {queryError}
          </div>
        </div>
      )}
    </section>
  );
}

