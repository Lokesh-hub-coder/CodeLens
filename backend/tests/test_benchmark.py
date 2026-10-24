from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.core.repo_index import RepoIndex
from app.evaluation.benchmark import (
    ABLATION_CONDITIONS,
    BenchmarkTask,
    _canonicalize_predictions,
    _retrieval_metrics,
    _whole_file_context,
    load_tasks,
    run_benchmark,
    run_retrieval_evaluation,
)
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.llm.providers import LLMClient, LLMResponse
from app.parsing.java_bridge import ClassInfo, MethodInfo
from app.retrieval.tokenizer import count_tokens
from app.retrieval.vector_store import SearchResult


class FakeVectorStore:
    def __init__(self, result: SearchResult) -> None:
        self.result = result

    def search(self, query_vector: np.ndarray, top_k: int) -> list[SearchResult]:
        return [self.result][:top_k]


class FakeEmbeddingModel:
    def encode_one(self, text: str) -> np.ndarray:
        return np.ones((4,), dtype=np.float32)


class CountingLLM(LLMClient):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        self.requests.append((system_prompt, user_prompt))
        return LLMResponse(
            text=json.dumps({
                "bug_locations": ["Sample#target()"],
                "change_impact": ["Sample#target()"],
            }),
            latency_ms=1.0,
            prompt_tokens=count_tokens(system_prompt) + count_tokens(user_prompt),
            completion_tokens=12,
        )


def _repository(tmp_path: Path) -> RepoIndex:
    path = tmp_path / "Sample.java"
    path.write_text(
        "class Sample {\n"
        "    void target() { throw new IllegalStateException(); }\n"
        "}\n",
        encoding="utf-8",
    )
    cls = ClassInfo("Sample", "Sample.java", "Sample", "", 1, 3, [])
    method = MethodInfo(
        "Sample#target()",
        "Sample",
        "target",
        "target()",
        "Sample.java",
        2,
        2,
        [],
    )
    result = SearchResult(
        chunk_id=method.id,
        score=0.9,
        metadata={
            "method_id": method.id,
            "class_id": method.class_id,
            "name": method.name,
            "file_path": method.file_path,
            "start_line": method.start_line,
            "end_line": method.end_line,
        },
    )
    return RepoIndex(
        repo_id="benchmark-test",
        repo_root=tmp_path,
        fingerprint="fixture",
        call_graph=CallGraph.build([method], []),
        dependency_graph=DependencyGraph.build([cls], []),
        symbol_index=SymbolIndex.build([cls], [method]),
        vector_store=FakeVectorStore(result),
        num_files=1,
        num_classes=1,
        num_methods=1,
        num_call_edges=0,
        file_token_counts={"Sample.java": 50},
    )


def test_task_loader_requires_explicit_ground_truth(tmp_path):
    task_file = tmp_path / "tasks.json"
    task_file.write_text(
        json.dumps([{
            "id": "bug-1",
            "type": "bug_localization",
            "query": "why does target throw?",
            "bug_gold_ids": ["Sample#target()"],
        }]),
        encoding="utf-8",
    )

    assert load_tasks(task_file)[0].bug_gold_ids == ("Sample#target()",)
    task_file.write_text(
        json.dumps([{
            "id": "bug-1",
            "type": "bug_localization",
            "query": "why does target throw?",
        }]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bug_gold_ids"):
        load_tasks(task_file)


def test_commons_lang_defects4j_task_labels_are_loadable():
    task_file = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "commons-lang-defects4j.json"
    )

    tasks = load_tasks(task_file)

    assert len(tasks) == 5
    assert len({task.repo_id for task in tasks}) == 5
    assert all(task.bug_gold_ids for task in tasks)


def test_benchmark_runs_same_tasks_and_model_for_baselines_and_ablations(tmp_path):
    settings = Settings(default_token_budget=1000)
    tasks = [
        BenchmarkTask(
            "bug-1",
            "bug_localization",
            "why does target throw?",
            ("Sample#target()",),
            (),
        ),
        BenchmarkTask(
            "impact-1",
            "change_impact",
            "what calls target?",
            (),
            ("Sample#target()",),
            "target",
        ),
    ]
    llm = CountingLLM()
    report = run_benchmark(
        tasks=tasks,
        repo_index=_repository(tmp_path),
        embedding_model=FakeEmbeddingModel(),
        settings=settings,
        llm_client=llm,
        include_ablations=True,
    )

    assert report["task_count"] == 2
    assert len(report["results"]) == 2 * (3 + len(ABLATION_CONDITIONS))
    assert len(llm.requests) == len(report["results"])
    assert {row["task_id"] for row in report["results"]} == {"bug-1", "impact-1"}
    assert all(row["actual_input_tokens"] is not None for row in report["results"])
    assert all(row["error_type"] is None for row in report["results"])
    bug_rows = [row for row in report["results"] if row["task_type"] == "bug_localization"]
    assert all(row["metrics"]["bug_localization_hit"] for row in bug_rows)
    impact_rows = [row for row in report["results"] if row["task_type"] == "change_impact"]
    assert all(row["metrics"]["model_precision"] == 1 for row in impact_rows)
    assert all(row["metrics"]["model_recall"] == 1 for row in impact_rows)
    assert all(row["metrics"]["change_impact_retrieval_recall"] == 1 for row in impact_rows)
    assert report["summary"]["whole_file"]["mean_actual_token_reduction_vs_whole_file"] == 0
    assert report["token_count_note"].startswith("actual_input_tokens comes only")


def test_whole_file_retrieval_obeys_shared_budget_and_ranks_relevant_file(tmp_path):
    repo_index = _repository(tmp_path)
    (tmp_path / "Noise.java").write_text(
        "class Noise {\n"
        + "\n".join("    void unrelated() { int value = 42; }" for _ in range(100))
        + "\n}\n",
        encoding="utf-8",
    )
    settings = Settings(default_token_budget=120)
    task = BenchmarkTask(
        "bug-1",
        "bug_localization",
        "Why does Sample target throw IllegalStateException?",
        ("Sample#target()",),
        (),
    )

    context, retrieved_ids, candidate_count = _whole_file_context(
        task,
        repo_index,
        settings,
    )

    from app.evaluation.benchmark import EVALUATION_SYSTEM_PROMPT, _prompt

    assert (
        count_tokens(EVALUATION_SYSTEM_PROMPT)
        + count_tokens(_prompt(task, context))
        <= settings.default_token_budget
    )
    assert "Sample.java" in context
    assert "Noise.java" not in context
    assert retrieved_ids == ["Sample#target()"]
    assert candidate_count == 2


def test_retrieval_metrics_report_precision_recall_and_rank():
    metrics = _retrieval_metrics(
        ["Wrong#candidate()", "Gold#target()"],
        ("Gold#target()",),
        "bug_localization",
    )

    assert metrics == {
        "bug_localization_retrieval_precision": 0.5,
        "bug_localization_retrieval_recall": 1.0,
        "bug_localization_retrieval_f1": pytest.approx(2 / 3),
        "bug_localization_retrieval_hit": True,
        "bug_localization_retrieval_mrr": 0.5,
        "bug_localization_retrieval_recall_at_1": 0.0,
        "bug_localization_retrieval_recall_at_5": 1.0,
        "bug_localization_retrieval_recall_at_10": 1.0,
    }


def test_prediction_canonicalization_requires_unique_retrieved_id():
    gold = "org.apache.commons.lang3.LocaleUtils#toLocale(String)"
    assert _canonicalize_predictions(
        ["LocaleUtils#toLocale(String)"],
        [gold],
    ) == [gold]
    assert _canonicalize_predictions(
        ["LocaleUtils#toLocale(String)"],
        [
            gold,
            "org.example.LocaleUtils#toLocale(String)",
        ],
    ) == ["LocaleUtils#toLocale(String)"]


def test_retrieval_only_mode_reports_gold_metrics_without_provider_usage(tmp_path):
    settings = Settings(default_token_budget=120)
    task = BenchmarkTask(
        "bug-1",
        "bug_localization",
        "why does target throw?",
        ("Sample#target()",),
        (),
    )

    report = run_retrieval_evaluation(
        tasks=[task],
        repo_index=_repository(tmp_path),
        embedding_model=FakeEmbeddingModel(),
        settings=settings,
        include_ablations=True,
    )

    assert report["execution_mode"] == "retrieval_only_no_model_requests"
    assert len(report["results"]) == 3 + len(ABLATION_CONDITIONS)
    assert all(row["actual_input_tokens"] is None for row in report["results"])
    assert all(row["metrics"]["bug_localization_retrieval_recall"] == 1 for row in report["results"])
    assert all(row["estimated_input_tokens"] <= settings.default_token_budget for row in report["results"])
    assert all(row["error_type"] is None for row in report["results"])
