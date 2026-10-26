"""Shared repository-query pipeline used by the HTTP API and MCP tools."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import networkx as nx
from fastapi import HTTPException
from pydantic import ValidationError

from app.acor.context_builder import build_context
from app.acor.ranker import rank_and_select
from app.acor.scorer import Candidate, ScoredCandidate, score_candidates
from app.api.schemas import (
    EvidenceReference,
    GraphEdgeReference,
    GraphInspectionResponse,
    QueryIntentLabel,
    QueryMetrics,
    QueryRequest,
    QueryResponse,
    RetrievedContextItem,
    StructuredAnalysisAnswer,
)
from app.config import ACORWeights, Settings
from app.core.candidate_pipeline import get_candidates
from app.core.repo_index import RepoIndex
from app.llm.prompts import STRUCTURED_SYSTEM_PROMPT, build_user_prompt
from app.llm.providers import LLMClient, get_llm_client
from app.retrieval.embeddings import EmbeddingModel, get_embedding_model
from app.retrieval.query_understanding import analyze_query
from app.retrieval.tokenizer import count_tokens

logger = logging.getLogger("codelens.query")

_INTENT_WEIGHTS: dict[QueryIntentLabel, ACORWeights] = {
    QueryIntentLabel.ROOT_CAUSE_ANALYSIS: ACORWeights(semantic=0.35, call_graph=0.30, dependency=0.10, symbol=0.25),
    QueryIntentLabel.BUG_EXPLANATION: ACORWeights(semantic=0.40, call_graph=0.25, dependency=0.10, symbol=0.25),
    QueryIntentLabel.FEATURE_LOCATION: ACORWeights(semantic=0.30, call_graph=0.15, dependency=0.15, symbol=0.40),
    QueryIntentLabel.REFACTORING: ACORWeights(semantic=0.45, call_graph=0.15, dependency=0.25, symbol=0.15),
    QueryIntentLabel.GENERAL_QA: ACORWeights(semantic=0.50, call_graph=0.15, dependency=0.15, symbol=0.20),
}


def _render_answer(answer: StructuredAnalysisAnswer) -> str:
    sections = [
        ("Direct Answer", answer.summary),
        ("Root Cause and Causal Mechanism", answer.root_cause),
        ("Causal Chain", "\n".join(
            f"{index}. {step}" for index, step in enumerate(answer.causal_chain, start=1)
        )),
        ("Recommended Fix", answer.suggested_fix),
    ]
    return "\n\n".join(
        f"{label}:\n{content}"
        for label, content in sections
        if content
    )


def inspect_repository_graphs(
    repo_id: str,
    repo_index: RepoIndex,
    symbol: str | None = None,
    max_hops: int = 2,
    limit: int = 20,
) -> GraphInspectionResponse:
    """Return bounded, read-only graph metadata and edges around an exact symbol."""
    matching_locations = []
    resolved_symbol = None
    if symbol:
        if "#" in symbol:
            method_name = symbol.split("#", 1)[1].split("(", 1)[0]
            matching_locations = [
                location
                for location in repo_index.symbol_index.exact_lookup(method_name)
                if location.symbol_id == symbol
            ]
        elif "(" in symbol:
            method_name = symbol.split("(", 1)[0]
            signature = symbol[symbol.index("("):]
            matching_locations = [
                location
                for location in repo_index.symbol_index.exact_lookup(method_name)
                if location.symbol_id.endswith(f"#{method_name}{signature}")
            ]
        elif "." in symbol:
            class_name = symbol.rsplit(".", 1)[-1]
            matching_locations = [
                location
                for location in repo_index.symbol_index.exact_lookup(class_name)
                if location.symbol_id == symbol
            ]
        else:
            matching_locations = repo_index.symbol_index.exact_lookup(symbol)
        if matching_locations:
            resolved_symbol = matching_locations[0].symbol_id

    method_seeds = {
        location.symbol_id
        for location in matching_locations
        if location.symbol_id in repo_index.call_graph.graph
    }
    class_seeds = {
        location.symbol_id
        for location in matching_locations
        if location.symbol_id in repo_index.dependency_graph.graph
    }
    class_seeds.update(
        location.symbol_id.split("#", 1)[0]
        for location in matching_locations
        if "#" in location.symbol_id
        and location.symbol_id.split("#", 1)[0] in repo_index.dependency_graph.graph
    )

    call_nodes = set(method_seeds)
    for method_id in method_seeds:
        call_nodes.update(
            repo_index.call_graph.neighbors_within_hops(method_id, max_hops=max_hops)
        )
    dependency_nodes = set(class_seeds)
    dependency_graph = repo_index.dependency_graph.graph
    if class_seeds:
        undirected = dependency_graph.to_undirected(as_view=True)
        for class_id in class_seeds:
            dependency_nodes.update(
                nx.single_source_shortest_path_length(
                    undirected, class_id, cutoff=max_hops
                ).keys()
            )

    call_edges = [
        GraphEdgeReference(source=source, target=target)
        for source, target in repo_index.call_graph.graph.edges
        if source in call_nodes and target in call_nodes
    ][:limit]
    dependency_edges = [
        GraphEdgeReference(source=source, target=target)
        for source, target in dependency_graph.edges
        if source in dependency_nodes and target in dependency_nodes
    ][:limit]

    return GraphInspectionResponse(
        repo_id=repo_id,
        call_graph_nodes=len(repo_index.call_graph),
        call_graph_edges=repo_index.call_graph.num_edges,
        dependency_graph_nodes=len(repo_index.dependency_graph),
        dependency_graph_edges=repo_index.dependency_graph.num_edges,
        symbol_index_entries=len(repo_index.symbol_index),
        resolved_symbol=resolved_symbol,
        call_graph_edges_near_symbol=call_edges,
        dependency_edges_near_symbol=dependency_edges,
    )


def execute_repository_query(
    repo_id: str,
    request: QueryRequest,
    settings: Settings,
    repo_index: RepoIndex,
    *,
    embedding_model_factory: Callable[[Settings], EmbeddingModel] = get_embedding_model,
    candidate_factory: Callable[..., list[Candidate]] = get_candidates,
    scorer: Callable[..., list[ScoredCandidate]] = score_candidates,
    context_builder: Callable[..., str] = build_context,
    llm_client_factory: Callable[[Settings], LLMClient] = get_llm_client,
) -> QueryResponse:
    """Run retrieval and synthesis once, keeping API and MCP behavior aligned."""
    query = analyze_query(request.query)
    token_budget = request.token_budget or settings.default_token_budget
    retrieval_start = time.perf_counter()

    embedding_model = embedding_model_factory(settings)
    candidates = candidate_factory(
        query=query,
        repo_index=repo_index,
        embedding_model=embedding_model,
        settings=settings,
        changed_symbol=request.changed_symbol,
    )
    touched_files = {candidate.file_path for candidate in candidates}
    naive_baseline_tokens = sum(
        repo_index.file_token_counts.get(path, 0) for path in touched_files
    )
    if naive_baseline_tokens == 0:
        naive_baseline_tokens = sum(candidate.token_count for candidate in candidates)

    weights = (
        _INTENT_WEIGHTS.get(query.intent, settings.acor_weights)
        if settings.acor_use_intent_weights
        else settings.acor_weights
    )
    scored = scorer(
        candidates,
        query,
        repo_index.call_graph,
        repo_index.dependency_graph,
        repo_index.symbol_index,
        weights,
        max_hops=settings.graph_max_hops,
    )
    rank_result = rank_and_select(scored, token_budget, settings)
    context_text = context_builder(rank_result.selected, repo_index.repo_root)
    retrieval_ms = (time.perf_counter() - retrieval_start) * 1000
    actual_context_tokens = count_tokens(context_text) if context_text else 0
    if actual_context_tokens > token_budget:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "context_budget_exceeded",
                "message": "Formatted retrieved context exceeded the requested token budget.",
            },
        )

    user_prompt = build_user_prompt(request.query, query.intent, context_text)
    llm_start = time.perf_counter()
    try:
        llm_response = llm_client_factory(settings).generate(
            STRUCTURED_SYSTEM_PROMPT,
            user_prompt,
        )
    except Exception as exc:
        logger.exception("LLM provider failed for repository query")
        status_code = 429 if getattr(exc, "status_code", None) == 429 else 502
        error_code = "llm_rate_limited" if status_code == 429 else "llm_provider_error"
        message = (
            "The configured LLM provider rate limit was reached."
            if status_code == 429
            else "The configured LLM provider request failed."
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": error_code, "message": message},
        ) from exc
    llm_elapsed_ms = (time.perf_counter() - llm_start) * 1000

    structured_answer: StructuredAnalysisAnswer | None
    output_limitation: str | None
    try:
        structured_answer = StructuredAnalysisAnswer.model_validate_json(llm_response.text)
        output_limitation = None
    except ValidationError:
        structured_answer = None
        output_limitation = (
            "Provider output was not valid JSON matching the structured-answer schema; "
            "the raw answer is preserved and structured fields are unavailable."
        )

    selected = rank_result.selected
    retrieved_context = [
        RetrievedContextItem(
            method_id=candidate.method_id,
            symbol_name=candidate.name,
            file_path=candidate.file_path,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            snippet=candidate.snippet,
            token_count=candidate.token_count,
            score=candidate.score,
        )
        for candidate in selected
    ]
    evidence = [
        EvidenceReference(
            rank=index,
            file_path=candidate.file_path,
            method_id=candidate.method_id,
            symbol_name=candidate.name,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            snippet=candidate.snippet,
            score=candidate.score,
        )
        for index, candidate in enumerate(selected, start=1)
    ]
    if structured_answer is not None:
        structured_answer.related_methods = [
            candidate.method_id for candidate in selected
        ]
        legacy_answer = _render_answer(structured_answer)
    else:
        legacy_answer = llm_response.text

    provider_latency_ms = getattr(llm_response, "latency_ms", llm_elapsed_ms)
    prompt_tokens = getattr(llm_response, "prompt_tokens", None)
    completion_tokens = getattr(llm_response, "completion_tokens", None)
    total_tokens = getattr(llm_response, "total_tokens", None)
    estimated_prompt_tokens = count_tokens(STRUCTURED_SYSTEM_PROMPT) + count_tokens(user_prompt)
    return QueryResponse(
        repo_id=repo_id,
        query=request.query,
        detected_intent=query.intent,
        answer=legacy_answer,
        retrieved_context=retrieved_context,
        candidates_considered=len(candidates),
        candidates_selected=len(selected),
        total_context_tokens=actual_context_tokens,
        token_budget=token_budget,
        naive_baseline_tokens=naive_baseline_tokens,
        retrieval_latency_ms=retrieval_ms,
        llm_latency_ms=llm_elapsed_ms,
        llm_prompt_tokens=prompt_tokens,
        llm_completion_tokens=completion_tokens,
        llm_total_tokens=total_tokens,
        structured_answer=structured_answer,
        evidence=evidence,
        metrics=QueryMetrics(
            retrieval_latency_ms=retrieval_ms,
            provider_latency_ms=provider_latency_ms,
            estimated_context_tokens=actual_context_tokens,
            estimated_prompt_tokens=estimated_prompt_tokens,
            token_budget=token_budget,
            actual_provider_input_tokens=prompt_tokens,
            actual_provider_output_tokens=completion_tokens,
            actual_provider_total_tokens=total_tokens,
            structured_output_validated=structured_answer is not None,
            structured_output_limitation=output_limitation,
        ),
    )
