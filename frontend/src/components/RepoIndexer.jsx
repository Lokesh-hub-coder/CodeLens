import React, { useState } from "react";
import {
  FolderIcon,
  DatabaseIcon,
  LayersIcon,
  FileCodeIcon,
  CodeIcon,
  GitBranchIcon,
  ClockIcon,
  CheckCircleIcon,
  AlertCircleIcon,
  ArrowRightIcon,
  ZapIcon,
} from "./Icons.jsx";
import { indexRepository } from "../api.js";

export default function RepoIndexer({
  connectedRepoId,
  setConnectedRepoId,
  indexStatus,
  setIndexStatus,
  isIndexing,
  setIsIndexing,
}) {
  const [repoPath, setRepoPath] = useState("C:\\Users\\SHREYASH\\AppData\\Local\\Temp\\commons-lang");
  const [repoId, setRepoId] = useState("commons-lang");
  const [indexError, setIndexError] = useState(null);
  const [forceReindex, setForceReindex] = useState(false);

  const handleQuickPreset = () => {
    setRepoPath("C:\\Users\\SHREYASH\\AppData\\Local\\Temp\\commons-lang");
    setRepoId("commons-lang");
  };

  async function handleSubmit(event) {
    event.preventDefault();
    if (!repoPath.trim()) return;

    setIsIndexing(true);
    setIndexError(null);

    try {
      const result = await indexRepository({
        repoPath: repoPath.trim(),
        repoId: repoId.trim() || undefined,
        forceReindex,
      });
      setConnectedRepoId(result.repo_id);
      setIndexStatus(result);
    } catch (err) {
      setIndexError(err.message || "Failed to index repository.");
    } finally {
      setIsIndexing(false);
    }
  }

  return (
    <section className="dashboard-card repo-card">
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon-badge">
            <FolderIcon size={16} />
          </div>
          <div>
            <h2 className="card-title">Repository Connection</h2>
            <p className="card-subtitle">
              Java AST parsing, symbol resolution, and call graph extraction
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={handleQuickPreset}
          className="preset-chip"
          title="Load default Commons Lang benchmark repo"
        >
          <ZapIcon size={12} />
          <span>Load commons-lang sample</span>
        </button>
      </div>

      <form onSubmit={handleSubmit} className="repo-form">
        <div className="repo-input-grid">
          <div className="input-group flex-2">
            <label htmlFor="repoPath" className="input-label">
              <span>Repository Path (local directory on host)</span>
            </label>
            <div className="input-wrapper">
              <span className="input-icon">
                <FolderIcon size={15} />
              </span>
              <input
                id="repoPath"
                type="text"
                className="text-input"
                placeholder="e.g. C:\path\to\java-repo or /home/user/project"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="input-group flex-1">
            <label htmlFor="repoId" className="input-label">
              <span>Repo ID <span className="label-sub">(optional)</span></span>
            </label>
            <div className="input-wrapper">
              <span className="input-icon">
                <DatabaseIcon size={15} />
              </span>
              <input
                id="repoId"
                type="text"
                className="text-input"
                placeholder="e.g. commons-lang"
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
              />
            </div>
          </div>

          <div className="btn-group">
            <button
              type="submit"
              className="btn btn-primary index-btn"
              disabled={isIndexing}
            >
              {isIndexing ? (
                <>
                  <span className="btn-spinner" />
                  <span>Indexing AST…</span>
                </>
              ) : (
                <>
                  <span>Index Repository</span>
                  <ArrowRightIcon size={14} />
                </>
              )}
            </button>
          </div>
        </div>

        <div className="reindex-toggle-row">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={forceReindex}
              onChange={(e) => setForceReindex(e.target.checked)}
              className="custom-checkbox"
            />
            <span>Force full re-index (bypass disk cache)</span>
          </label>
        </div>
      </form>

      {/* Error State */}
      {indexError && (
        <div className="banner banner-error">
          <AlertCircleIcon size={16} />
          <div className="banner-text">
            <strong>Indexing Failed:</strong> {indexError}
          </div>
        </div>
      )}

      {/* Metric Cards Layout when Indexed */}
      {indexStatus && (
        <div className="repo-metrics-container">
          <div className="metrics-header">
            <div className="metrics-status">
              <CheckCircleIcon size={15} className="text-teal" />
              <span>
                Successfully indexed <strong>{indexStatus.repo_id}</strong>
              </span>
            </div>
            <div className="metrics-timing">
              <ClockIcon size={13} />
              <span>{indexStatus.index_time_seconds.toFixed(2)}s parsing time</span>
            </div>
          </div>

          <div className="metrics-grid">
            <div className="metric-tile">
              <div className="metric-top">
                <FileCodeIcon size={14} className="metric-icon" />
                <span className="metric-label">Files</span>
              </div>
              <div className="metric-value">
                {indexStatus.num_files.toLocaleString()}
              </div>
            </div>

            <div className="metric-tile">
              <div className="metric-top">
                <LayersIcon size={14} className="metric-icon" />
                <span className="metric-label">Classes</span>
              </div>
              <div className="metric-value">
                {indexStatus.num_classes.toLocaleString()}
              </div>
            </div>

            <div className="metric-tile">
              <div className="metric-top">
                <CodeIcon size={14} className="metric-icon" />
                <span className="metric-label">Methods</span>
              </div>
              <div className="metric-value">
                {indexStatus.num_methods.toLocaleString()}
              </div>
            </div>

            <div className="metric-tile">
              <div className="metric-top">
                <GitBranchIcon size={14} className="metric-icon" />
                <span className="metric-label">Call Edges</span>
              </div>
              <div className="metric-value">
                {indexStatus.num_call_edges.toLocaleString()}
              </div>
            </div>

            {indexStatus.num_import_edges !== undefined && (
              <div className="metric-tile">
                <div className="metric-top">
                  <DatabaseIcon size={14} className="metric-icon" />
                  <span className="metric-label">Imports</span>
                </div>
                <div className="metric-value">
                  {indexStatus.num_import_edges.toLocaleString()}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

