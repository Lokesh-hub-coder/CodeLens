const INTENT_LABELS = {
  bug_explanation: "Bug explanation",
  root_cause_analysis: "Root cause analysis",
  feature_location: "Feature location",
  refactoring: "Refactoring",
  general_qa: "General Q&A",
};

function BudgetMeter({ used, budget, naiveBaselineTokens }) {
  const pct = Math.min(100, (used / budget) * 100);
  const fillClass = pct > 95 ? "danger" : pct > 80 ? "warn" : "";
  const savings =
    naiveBaselineTokens && naiveBaselineTokens > used
      ? Math.round((1 - used / naiveBaselineTokens) * 100)
      : null;

  return (
    <div className="budget-meter">
      <div className="budget-meter-header">
        <span className="used">{used.toLocaleString()} tokens used</span>
        <span className="budget">budget: {budget.toLocaleString()}</span>
      </div>
      <div className="budget-meter-track">
        <div className={`budget-meter-fill ${fillClass}`} style={{ width: `${pct}%` }} />
      </div>
      {savings !== null && savings > 0 && (
        <div className="budget-meter-savings">
          ~{savings}% fewer tokens than sending every candidate file whole (
          {naiveBaselineTokens.toLocaleString()} tokens)
        </div>
      )}
    </div>
  );
}

export default function ContextViewer({ result }) {
  if (!result) {
    return (
      <section className="panel">
        <p className="empty-state">Index a repository and ask a question to see ACOR's picks here.</p>
      </section>
    );
  }

  return (
    <>
      <section className="panel">
        <div className="panel-label">Answer</div>
        <span className="intent-badge">{INTENT_LABELS[result.detected_intent] || result.detected_intent}</span>
        <div className="answer-block">{result.answer}</div>
        <BudgetMeter
          used={result.total_context_tokens}
          budget={result.token_budget}
          naiveBaselineTokens={result.naive_baseline_tokens}
        />
        <p className="status-line">
          {result.candidates_selected} of {result.candidates_considered} candidates selected · retrieval{" "}
          {result.retrieval_latency_ms.toFixed(0)}ms · LLM {result.llm_latency_ms.toFixed(0)}ms
        </p>
      </section>

      <section className="panel">
        <div className="panel-label">Retrieved context ({result.retrieved_context.length} chunks)</div>
        {result.retrieved_context.map((item) => (
          <div className="context-item" key={`${item.file_path}:${item.start_line}`}>
            <div className="context-item-header">
              <span>
                <span className="symbol-name">{item.symbol_name}</span> · {item.file_path}:{item.start_line}-
                {item.end_line}
              </span>
              <span className="score">relevance {item.score.composite.toFixed(2)}</span>
            </div>
            <pre>{item.snippet}</pre>
          </div>
        ))}
      </section>
    </>
  );
}