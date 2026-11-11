import React from "react";
import { CpuIcon, ZapIcon, ClockIcon, LayersIcon } from "./Icons.jsx";

export default function BudgetMeter({
  used,
  budget,
  naiveBaselineTokens,
  candidatesSelected,
  candidatesConsidered,
  retrievalLatencyMs,
  llmLatencyMs,
}) {
  const safeBudget = budget || 8000;
  const pct = Math.min(100, (used / safeBudget) * 100);
  const fillClass = pct > 90 ? "danger" : pct > 75 ? "warn" : "normal";

  const savings =
    naiveBaselineTokens && naiveBaselineTokens > used
      ? Math.round((1 - used / naiveBaselineTokens) * 100)
      : null;

  return (
    <div className="budget-meter-card">
      <div className="meter-header">
        <div className="meter-metric">
          <CpuIcon size={14} className="meter-icon" />
          <span className="meter-title">Context Budget Utilization</span>
        </div>
        <div className="meter-counts">
          <span className="used-tokens">{used.toLocaleString()}</span>
          <span className="budget-divider">/</span>
          <span className="max-budget">{safeBudget.toLocaleString()} tokens</span>
          <span className="pct-badge">({pct.toFixed(0)}%)</span>
        </div>
      </div>

      <div className="meter-track-shell">
        <div
          className={`meter-track-fill ${fillClass}`}
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>

      <div className="meter-footer">
        {savings !== null && savings > 0 ? (
          <div className="savings-callout">
            <ZapIcon size={13} className="text-teal" />
            <span>
              ACOR packed context with <strong>~{savings}% fewer tokens</strong>{" "}
              than sending whole files ({naiveBaselineTokens.toLocaleString()}{" "}
              baseline tokens)
            </span>
          </div>
        ) : (
          <div className="savings-callout-neutral">
            <span>Optimal greedy knapsack context packing active</span>
          </div>
        )}

        <div className="meter-pills-row">
          {candidatesSelected !== undefined && (
            <span className="perf-pill">
              <LayersIcon size={12} />
              <span>
                {candidatesSelected} of {candidatesConsidered} candidates
              </span>
            </span>
          )}
          {retrievalLatencyMs !== undefined && (
            <span className="perf-pill">
              <ClockIcon size={12} />
              <span>Retrieval: {retrievalLatencyMs.toFixed(0)}ms</span>
            </span>
          )}
          {llmLatencyMs !== undefined && (
            <span className="perf-pill">
              <ZapIcon size={12} />
              <span>Synthesis: {llmLatencyMs.toFixed(0)}ms</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

