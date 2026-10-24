"""CodeLens tools for IDEs that support the Model Context Protocol."""
from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from app.api.schemas import (
    GraphInspectionResponse,
    MCPQueryResult,
    QueryRequest,
    QueryResponse,
)
from app.config import get_settings
from app.api.routes import _get_repo_index
from app.core.candidate_pipeline import get_candidates
from app.core.query_service import execute_repository_query, inspect_repository_graphs
from app.acor.context_builder import build_context
from app.acor.scorer import score_candidates
from app.llm.providers import get_llm_client
from app.retrieval.embeddings import get_embedding_model

mcp_server = MCPServer(name="codelens-mcp-server", version="0.1.0")
logger = logging.getLogger("codelens.mcp")


def _error_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message", "CodeLens request failed."))
    return str(detail)


def _summary(response: QueryResponse) -> str:
    lines = [
        f"CodeLens analysis ({response.detected_intent.value})",
        (
            f"Retrieved {response.candidates_selected} snippets using "
            f"{response.total_context_tokens}/{response.token_budget} context tokens."
        ),
        "Retrieved locations:",
    ]
    lines.extend(
        f"- {item.symbol_name} ({item.file_path}:{item.start_line}-{item.end_line})"
        for item in response.retrieved_context[:5]
    )
    lines.extend(["", "Analysis:", response.answer])
    return "\n".join(lines)


def _run_query(
    repo_id: str,
    query: str,
    token_budget: int,
    changed_symbol: str | None = None,
) -> MCPQueryResult:
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ToolError("repo_id must be a non-empty string")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("query must be a non-empty string")
    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget <= 0:
        raise ToolError("token_budget must be a positive integer")
    try:
        request = QueryRequest(
            query=query,
            token_budget=token_budget,
            changed_symbol=changed_symbol,
        )
        settings = get_settings()
        repo_index = _get_repo_index(repo_id, settings)
        response = execute_repository_query(
            repo_id,
            request,
            settings,
            repo_index,
            embedding_model_factory=get_embedding_model,
            candidate_factory=get_candidates,
            scorer=score_candidates,
            context_builder=build_context,
            llm_client_factory=get_llm_client,
        )
    except HTTPException as exc:
        logger.warning("MCP query failed with HTTP status %s", exc.status_code)
        raise ToolError(_error_message(exc)) from exc
    except ValidationError as exc:
        raise ToolError("The repository query parameters are invalid.") from exc
    return MCPQueryResult(summary=_summary(response), response=response)


@mcp_server.tool(
    name="codelens_root_cause",
    description="Analyze an indexed repository bug and return structured evidence and a likely root cause.",
    structured_output=True,
)
async def codelens_root_cause(
    repo_id: str,
    query: str,
    token_budget: int = 4000,
) -> MCPQueryResult:
    return _run_query(repo_id, query, token_budget)


@mcp_server.tool(
    name="codelens_change_impact",
    description="Find callers and files impacted by changing a method in an indexed repository.",
    structured_output=True,
)
async def codelens_change_impact(
    repo_id: str,
    query: str,
    changed_symbol: str,
    token_budget: int = 4000,
) -> MCPQueryResult:
    if not isinstance(changed_symbol, str) or not changed_symbol.strip():
        raise ToolError("changed_symbol must be a non-empty string")
    return _run_query(repo_id, query, token_budget, changed_symbol)


@mcp_server.tool(
    name="codelens_graph_inspect",
    description="Read bounded graph metadata and nearby call/dependency edges for an indexed repository.",
    structured_output=True,
)
async def codelens_graph_inspect(
    repo_id: str,
    symbol: str = "",
    max_hops: int = 2,
    limit: int = 20,
) -> GraphInspectionResponse:
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ToolError("repo_id must be a non-empty string")
    if not isinstance(symbol, str):
        raise ToolError("symbol must be a string")
    if (
        isinstance(max_hops, bool)
        or not isinstance(max_hops, int)
        or not 0 <= max_hops <= 5
    ):
        raise ToolError("max_hops must be an integer from 0 through 5")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ToolError("limit must be an integer from 1 through 100")
    try:
        settings = get_settings()
        repo_index = _get_repo_index(repo_id, settings)
    except HTTPException as exc:
        raise ToolError(_error_message(exc)) from exc
    return inspect_repository_graphs(
        repo_id,
        repo_index,
        symbol or None,
        max_hops=max_hops,
        limit=limit,
    )


async def main() -> None:
    await mcp_server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
