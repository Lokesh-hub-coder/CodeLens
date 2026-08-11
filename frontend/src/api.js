const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

export function indexRepository({ repoPath, repoId, forceReindex = false }) {
  return request("/repositories/index", {
    method: "POST",
    body: JSON.stringify({
      repo_path: repoPath,
      repo_id: repoId || undefined,
      force_reindex: forceReindex,
    }),
  });
}

export function queryRepository({ repoId, query, tokenBudget }) {
  return request(`/repositories/${encodeURIComponent(repoId)}/query`, {
    method: "POST",
    body: JSON.stringify({
      repo_id: repoId,
      query,
      token_budget: tokenBudget || undefined,
    }),
  });
}

export function repositoryStatus(repoId) {
  return request(`/repositories/${encodeURIComponent(repoId)}/status`);
}