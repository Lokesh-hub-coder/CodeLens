"""CodeLens Model Context Protocol (MCP) Server.

Enables CodeLens to run directly inside any IDE supporting MCP
(e.g., Cursor, Claude Desktop, Antigravity, VS Code MCP extensions).

Exposes two standard tools to the IDE:
1. codelens_root_cause: Given a bug report or error, retrieves relevant code
   context at minimum token budget and provides explanation + localization.
2. codelens_change_impact: Given a proposed change, runs directed reverse BFS
   to compute the exact impacted callers and blast radius.
"""
from __future__ import annotations

import asyncio
from typing import Any
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from app.config import get_settings
from app.api.routes import _get_repo_index, _INTENT_WEIGHTS
from app.retrieval.query_understanding import analyze_query
from app.retrieval.embeddings import get_embedding_model
from app.retrieval.tokenizer import count_tokens
from app.core.candidate_pipeline import get_candidates
from app.acor.scorer import score_candidates
from app.acor.ranker import rank_and_select
from app.acor.context_builder import build_context
from app.llm.providers import get_llm_client
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt

mcp_server = Server("codelens-mcp-server")


@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="codelens_root_cause",
            description=(
                "Analyze a bug or error in a Java repository using graph-aware ACOR retrieval. "
                "Retrieves minimum required code tokens and pinpoints exact method/call-edges."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {
                        "type": "string",
                        "description": "Identifier of the indexed repository (e.g. 'commons_lang')",
                    },
                    "query": {
                        "type": "string",
                        "description": "Developer query or error stack trace (e.g. 'why does createNumber throw NumberFormatException on null?')",
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Maximum token budget for context (default: 4000)",
                        "default": 4000,
                    },
                },
                "required": ["repo_id", "query"],
            },
        ),
        types.Tool(
            name="codelens_change_impact",
            description=(
                "Determine what callers and files will break if a method is modified. "
                "Performs directed reverse BFS traversal over the call graph."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_id": {
                        "type": "string",
                        "description": "Identifier of the indexed repository",
                    },
                    "query": {
                        "type": "string",
                        "description": "Explanation of the planned change",
                    },
                    "changed_symbol": {
                        "type": "string",
                        "description": "Name of the method being modified (e.g. 'createNumber')",
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Maximum token budget (default: 4000)",
                        "default": 4000,
                    },
                },
                "required": ["repo_id", "query", "changed_symbol"],
            },
        ),
    ]


@mcp_server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    if not arguments:
        arguments = {}

    settings = get_settings()
    repo_id = arguments.get("repo_id", "default")
    query_str = arguments.get("query", "")
    token_budget = arguments.get("token_budget", 4000)
    changed_symbol = arguments.get("changed_symbol")

    try:
        repo_index = _get_repo_index(repo_id, settings)
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error loading repository '{repo_id}': {e}")]

    query = analyze_query(query_str)
    embedding_model = get_embedding_model(settings)

    candidates = get_candidates(
        query=query,
        repo_index=repo_index,
        embedding_model=embedding_model,
        settings=settings,
        changed_symbol=changed_symbol,
    )

    touched_files = {c.file_path for c in candidates}
    naive_baseline = sum(repo_index.file_token_counts.get(f, 0) for f in touched_files) or 1

    weights = _INTENT_WEIGHTS.get(query.intent, settings.acor_weights)
    scored = score_candidates(
        candidates, query,
        repo_index.call_graph,
        repo_index.dependency_graph,
        repo_index.symbol_index,
        weights,
        max_hops=settings.graph_max_hops,
    )

    rank_result = rank_and_select(scored, token_budget, settings)
    context_text = build_context(rank_result.selected, repo_index.repo_root)
    actual_tokens = count_tokens(context_text) if context_text else rank_result.total_tokens

    llm_client = get_llm_client(settings)
    try:
        llm_response = llm_client.generate(
            SYSTEM_PROMPT,
            build_user_prompt(query_str, query.intent, context_text),
        )
        answer = llm_response.text
    except Exception as exc:
        answer = f"Error generating answer from LLM: {exc}"

    token_reduction_pct = max(0.0, 100.0 * (1.0 - (actual_tokens / naive_baseline)))

    result_summary = (
        f"### CodeLens Analysis (Intent: {query.intent.value})\n\n"
        f"**Token Efficiency:** Selected {len(rank_result.selected)} snippets ({actual_tokens} tokens) "
        f"vs Naive {naive_baseline} tokens (**{token_reduction_pct:.1f}% reduction**).\n\n"
        f"#### Retrieved Top Locations:\n"
    )
    for c in rank_result.selected[:5]:
        result_summary += f"- `{c.name}` ({c.file_path}:{c.start_line}-{c.end_line}) | score: {c.score.composite:.2f}\n"

    result_summary += f"\n#### Explanation & Diagnosis:\n{answer}\n"

    return [types.TextContent(type="text", text=result_summary)]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
