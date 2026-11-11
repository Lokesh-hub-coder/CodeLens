import React, { useState } from "react";
import {
  SparklesIcon,
  CopyIcon,
  CheckIcon,
  GitBranchIcon,
  LayersIcon,
  AlertCircleIcon,
  TerminalIcon,
} from "./Icons.jsx";
import MarkdownRenderer from "./MarkdownRenderer.jsx";
import BudgetMeter from "./BudgetMeter.jsx";

const INTENT_METADATA = {
  root_cause_analysis: {
    label: "Root Cause Analysis",
    className: "intent-rca",
    description: "Traced caller-callee call graph paths to diagnose bug root causes",
  },
  bug_explanation: {
    label: "Bug Explanation",
    className: "intent-bug",
    description: "Examining error propagation and exception semantics",
  },
  feature_location: {
    label: "Feature Location",
    className: "intent-feature",
    description: "Symbol search + neural semantic embedding matching",
  },
  refactoring: {
    label: "Change Impact Analysis",
    className: "intent-refactor",
    description: "Reverse call graph traversal for downstream caller dependencies",
  },
  general_qa: {
    label: "General Repository Q&A",
    className: "intent-general",
    description: "High-level architectural reasoning and repository semantics",
  },
};

export default function AnswerView({ result }) {
  const [copied, setCopied] = useState(false);

  if (!result) return null;

  const handleCopyAnswer = () => {
    if (!result.answer) return;
    navigator.clipboard.writeText(result.answer);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const intentMeta =
    INTENT_METADATA[result.detected_intent] || {
      label: result.detected_intent || "Query Answer",
      className: "intent-default",
      description: "CodeLens contextual synthesis",
    };

  const structured = result.structured_answer;

  return (
    <section className="dashboard-card answer-card">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon-badge answer-badge">
            <SparklesIcon size={16} />
          </div>
          <div>
            <div className="answer-heading-row">
              <h2 className="card-title">CodeLens Intelligence</h2>
              <span className={`intent-badge ${intentMeta.className}`}>
                <span className="intent-dot" />
                {intentMeta.label}
              </span>
            </div>
            <p className="card-subtitle">{intentMeta.description}</p>
          </div>
        </div>

        <button
          type="button"
          className="header-action-btn"
          onClick={handleCopyAnswer}
          title="Copy response markdown"
        >
          {copied ? (
            <>
              <CheckIcon size={13} className="text-teal" />
              <span>Copied Answer</span>
            </>
          ) : (
            <>
              <CopyIcon size={13} />
              <span>Copy Response</span>
            </>
          )}
        </button>
      </div>

      {/* Budget meter showing token efficiency */}
      <BudgetMeter
        used={result.total_context_tokens}
        budget={result.token_budget}
        naiveBaselineTokens={result.naive_baseline_tokens}
        candidatesSelected={result.candidates_selected}
        candidatesConsidered={result.candidates_considered}
        retrievalLatencyMs={result.retrieval_latency_ms}
        llmLatencyMs={result.llm_latency_ms}
      />

      {/* Structured Answer Cards (if provided by backend model) */}
      {structured && (
        <div className="structured-insights-grid">
          {structured.summary && (
            <div className="insight-card summary">
              <div className="insight-title">Executive Summary</div>
              <div className="insight-body">{structured.summary}</div>
            </div>
          )}

          {structured.root_cause && (
            <div className="insight-card root-cause">
              <div className="insight-title">Identified Root Cause</div>
              <div className="insight-body">{structured.root_cause}</div>
            </div>
          )}

          {structured.causal_chain && structured.causal_chain.length > 0 && (
            <div className="insight-card causal-chain">
              <div className="insight-title">Causal Call Chain</div>
              <ol className="chain-list">
                {structured.causal_chain.map((step, idx) => (
                  <li key={idx}>{step}</li>
                ))}
              </ol>
            </div>
          )}

          {structured.suggested_fix && (
            <div className="insight-card fix">
              <div className="insight-title">Suggested Resolution</div>
              <div className="insight-body">{structured.suggested_fix}</div>
            </div>
          )}
        </div>
      )}

      {/* Main Markdown Answer Content */}
      <div className="answer-prose-container">
        <MarkdownRenderer content={result.answer} />
      </div>
    </section>
  );
}

