#!/usr/bin/env python3
"""
Exercises a running CodeLens AI backend from the command line -- handy for
quick smoke-testing during development or for a live demo without needing
the frontend up. Uses only the standard library (urllib) so it has zero
dependency on the backend's own virtualenv being active.

Usage:
    python scripts/index_repository.py --repo /path/to/java-repo
    python scripts/index_repository.py --repo /path/to/java-repo \\
        --query "why does processOrder throw a NullPointerException?"

    # Re-use an already-indexed repository:
    python scripts/index_repository.py --repo-id my-service \\
        --query "where is retry logic implemented?" --skip-index
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def post(api_base: str, path: str, payload: dict) -> dict:
    url = f"{api_base.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code} calling {url}:\n{detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(f"Could not reach {url}: {exc.reason}. Is the backend running?", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--repo", help="Path to the Java repository to index")
    parser.add_argument("--repo-id", help="repo_id to use (or that was already indexed with --skip-index)")
    parser.add_argument("--skip-index", action="store_true", help="Assume --repo-id is already indexed")
    parser.add_argument("--query", help="Developer query to ask after indexing")
    parser.add_argument("--token-budget", type=int, default=None)
    args = parser.parse_args()

    if not args.skip_index:
        if not args.repo:
            parser.error("--repo is required unless --skip-index is set")
        print(f"Indexing {args.repo} ...")
        index_result = post(
            args.api,
            "/repositories/index",
            {"repo_path": args.repo, "repo_id": args.repo_id, "force_reindex": True},
        )
        print(json.dumps(index_result, indent=2))
        repo_id = index_result["repo_id"]
    else:
        if not args.repo_id:
            parser.error("--repo-id is required with --skip-index")
        repo_id = args.repo_id

    if args.query:
        print(f"\nQuerying repo_id={repo_id} ...")
        query_result = post(
            args.api,
            f"/repositories/{repo_id}/query",
            {"repo_id": repo_id, "query": args.query, "token_budget": args.token_budget},
        )
        print(f"\nIntent: {query_result['detected_intent']}")
        print(f"Answer:\n{query_result['answer']}\n")
        print(
            f"Context: {query_result['candidates_selected']}/{query_result['candidates_considered']} "
            f"candidates, {query_result['total_context_tokens']}/{query_result['token_budget']} tokens"
        )
        for item in query_result["retrieved_context"]:
            print(
                f"  - {item['symbol_name']} ({item['file_path']}:{item['start_line']}-{item['end_line']}) "
                f"relevance={item['score']['composite']:.2f}"
            )


if __name__ == "__main__":
    main()