import React, { useState } from "react";
import {
  FileCodeIcon,
  CodeIcon,
  CopyIcon,
  CheckIcon,
  ChevronDownIcon,
  LayersIcon,
} from "./Icons.jsx";
import { CodeBlock } from "./MarkdownRenderer.jsx";

export default function RetrievedContextList({ items }) {
  const [collapsedMap, setCollapsedMap] = useState({});
  const [searchFilter, setSearchFilter] = useState("");

  if (!items || items.length === 0) return null;

  const toggleCollapse = (key) => {
    setCollapsedMap((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  const filteredItems = items.filter((item) => {
    if (!searchFilter.trim()) return true;
    const q = searchFilter.toLowerCase();
    return (
      item.symbol_name.toLowerCase().includes(q) ||
      item.file_path.toLowerCase().includes(q)
    );
  });

  return (
    <section className="dashboard-card context-section">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon-badge context-badge">
            <LayersIcon size={16} />
          </div>
          <div>
            <h2 className="card-title">Retrieved Context Evidence</h2>
            <p className="card-subtitle">
              Ranked code chunks selected by ACOR within token budget
            </p>
          </div>
        </div>

        <div className="context-filter-wrapper">
          <input
            type="text"
            className="context-search-input"
            placeholder="Filter snippets by symbol or path…"
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
          />
          <span className="count-pill">{filteredItems.length} chunks</span>
        </div>
      </div>

      <div className="context-items-list">
        {filteredItems.map((item, index) => {
          const itemKey = `${item.file_path}:${item.start_line}`;
          const isCollapsed = !!collapsedMap[itemKey];

          return (
            <div key={itemKey} className="context-chunk-card">
              <div
                className="context-chunk-header"
                onClick={() => toggleCollapse(itemKey)}
              >
                <div className="chunk-identity">
                  <span className="chunk-rank-num">#{index + 1}</span>
                  <FileCodeIcon size={14} className="chunk-file-icon" />
                  <span className="chunk-symbol">{item.symbol_name}</span>
                  <span className="chunk-path-line">
                    {item.file_path}:L{item.start_line}–L{item.end_line}
                  </span>
                  {item.token_count !== undefined && (
                    <span className="chunk-tokens-pill">
                      {item.token_count} tokens
                    </span>
                  )}
                </div>

                <div className="chunk-meta-right">
                  {/* ACOR Signal Breakdown Pills */}
                  {item.score && (
                    <div className="scores-pill-group">
                      <span
                        className="score-pill composite"
                        title="ACOR Composite Score"
                      >
                        Score: {item.score.composite?.toFixed(2)}
                      </span>
                      <span
                        className="score-pill semantic"
                        title="Semantic Embedding Similarity"
                      >
                        Sem: {item.score.semantic?.toFixed(2)}
                      </span>
                      <span
                        className="score-pill call"
                        title="Call Graph Proximity"
                      >
                        Call: {item.score.call_graph?.toFixed(2)}
                      </span>
                      <span
                        className="score-pill dep"
                        title="Dependency Graph Proximity"
                      >
                        Dep: {item.score.dependency?.toFixed(2)}
                      </span>
                      <span
                        className="score-pill sym"
                        title="Symbol Match"
                      >
                        Sym: {item.score.symbol?.toFixed(2)}
                      </span>
                    </div>
                  )}

                  <button
                    type="button"
                    className={`chunk-collapse-btn ${isCollapsed ? "collapsed" : ""}`}
                    aria-label="Toggle snippet visibility"
                  >
                    <ChevronDownIcon size={14} />
                  </button>
                </div>
              </div>

              {!isCollapsed && (
                <div className="context-chunk-body">
                  <CodeBlock
                    code={item.snippet}
                    language="java"
                    title={`${item.file_path} (lines ${item.start_line}-${item.end_line})`}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

