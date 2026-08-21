import { useState } from "react";
import { indexRepository, queryRepository } from "../api.js";

export default function QueryPanel({ onResult }) {
  const [repoPath, setRepoPath] = useState("");
  const [repoId, setRepoId] = useState("");
  const [indexing, setIndexing] = useState(false);
  const [indexStatus, setIndexStatus] = useState(null);
  const [indexError, setIndexError] = useState(null);

  const [query, setQuery] = useState("");
  const [tokenBudget, setTokenBudget] = useState(8000);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState(null);

  async function handleIndex(event) {
    event.preventDefault();
    setIndexing(true);
    setIndexError(null);
    try {
      const result = await indexRepository({ repoPath, repoId: repoId || undefined });
      setRepoId(result.repo_id);
      setIndexStatus(result);
    } catch (err) {
      setIndexError(err.message);
    } finally {
      setIndexing(false);
    }
  }

  async function handleAsk(event) {
    event.preventDefault();
    if (!repoId) {
      setAskError("Index a repository first -- there's no repo_id to query yet.");
      return;
    }
    setAsking(true);
    setAskError(null);
    try {
      const result = await queryRepository({ repoId, query, tokenBudget: Number(tokenBudget) });
      onResult(result);
    } catch (err) {
      setAskError(err.message);
    } finally {
      setAsking(false);
    }
  }

  return (
    <>
      <section className="panel">
        <div className="panel-label">1 · Index a repository</div>
        <form onSubmit={handleIndex}>
          <div className="field-row">
            <div className="field">
              <label htmlFor="repoPath">Repository path (on the backend host)</label>
              <input
                id="repoPath"
                type="text"
                placeholder="/path/to/your/java-repo"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="repoId">repo_id (optional -- auto-generated if blank)</label>
              <input
                id="repoId"
                type="text"
                placeholder="my-service"
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={indexing}>
              {indexing ? "Indexing…" : "Index"}
            </button>
          </div>
        </form>
        {indexStatus && (
          <p className="status-line">
            Indexed {indexStatus.repo_id} — {indexStatus.num_files} files, {indexStatus.num_classes}{" "}
            classes, {indexStatus.num_methods} methods, {indexStatus.num_call_edges} call edges (
            {indexStatus.index_time_seconds.toFixed(1)}s)
          </p>
        )}
        {indexError && <p className="status-line error">{indexError}</p>}
      </section>

      <section className="panel">
        <div className="panel-label">2 · Ask about the code</div>
        <form onSubmit={handleAsk}>
          <div className="field">
            <label htmlFor="query">Developer query</label>
            <textarea
              id="query"
              placeholder="Why does processOrder() throw a NullPointerException when the cart is empty?"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              required
            />
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="tokenBudget">Token budget for retrieved context</label>
              <input
                id="tokenBudget"
                type="text"
                inputMode="numeric"
                value={tokenBudget}
                onChange={(e) => setTokenBudget(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </div>
            <button className="btn btn-primary" type="submit" disabled={asking}>
              {asking ? "Thinking…" : "Ask CodeLens"}
            </button>
          </div>
        </form>
        {askError && <p className="status-line error">{askError}</p>}
      </section>
    </>
  );
}