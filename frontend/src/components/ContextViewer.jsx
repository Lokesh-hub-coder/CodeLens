import React from "react";
import { SparklesIcon, LayersIcon, TerminalIcon, GitBranchIcon } from "./Icons.jsx";
import AnswerView from "./AnswerView.jsx";
import RetrievedContextList from "./RetrievedContextList.jsx";

export default function ContextViewer({ result, isQuerying }) {
  if (isQuerying) {
    return (
      <section className="dashboard-card empty-state-card querying-state">
        <div className="empty-state-glow" />
        <div className="state-spinner-large" />
        <h3 className="empty-state-title">Traversing Repository Graph…</h3>
        <p className="empty-state-desc">
          Resolving semantic embeddings, computing ACOR composite scores across call
          and dependency graphs, and packing context under token budget.
        </p>
      </section>
    );
  }

  if (!result) {
    return (
      <section className="dashboard-card empty-state-card">
        <div className="empty-state-icon-box">
          <SparklesIcon size={24} className="text-teal" />
        </div>
        <h3 className="empty-state-title">Ready for Repository Queries</h3>
        <p className="empty-state-desc">
          Index a Java repository above, then submit an architectural, bug-root-cause,
          or call-chain question to inspect ACOR context ranking and AI synthesis.
        </p>

        <div className="workflow-steps-row">
          <div className="workflow-step">
            <span className="step-num">01</span>
            <span className="step-label">AST &amp; Call Graph Parsing</span>
          </div>
          <div className="workflow-step-divider">→</div>
          <div className="workflow-step">
            <span className="step-num">02</span>
            <span className="step-label">Dense FastEmbed Vectors</span>
          </div>
          <div className="workflow-step-divider">→</div>
          <div className="workflow-step">
            <span className="step-num">03</span>
            <span className="step-label">ACOR Knapsack Packing</span>
          </div>
        </div>
      </section>
    );
  }

  return (
    <div className="results-wrapper">
      <AnswerView result={result} />
      <RetrievedContextList items={result.retrieved_context} />
    </div>
  );
}
