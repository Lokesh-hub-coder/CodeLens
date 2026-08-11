"""
Class-level dependency graph: nodes are fully-qualified class ids, directed
edges are "this class imports/depends on that class".

Deliberately kept separate from call_graph.py even though both are
networkx DiGraphs under the hood: the call graph reasons about *behavior*
(which method invokes which), the dependency graph reasons about
*structure* (which class needs which to compile). A candidate can be
call-graph-distant but dependency-adjacent (e.g. a shared config/DTO class
touched by many callers but calling almost nothing itself) -- ACOR treats
those as separate evidence rather than collapsing them into one graph, so
each contributes its own term to the composite score.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from app.parsing.java_bridge import ClassInfo, ImportEdge


@dataclass(frozen=True)
class DependencyGraph:
    graph: nx.DiGraph

    @classmethod
    def build(cls, classes: list[ClassInfo], import_edges: list[ImportEdge]) -> "DependencyGraph":
        g = nx.DiGraph()
        for c in classes:
            g.add_node(c.id, file_path=c.file_path, name=c.name, package=c.package)
        for edge in import_edges:
            if edge.from_class_id in g and edge.to_class_id in g:
                g.add_edge(edge.from_class_id, edge.to_class_id)
        return cls(graph=g)

    def proximity(self, class_id: str, anchor_class_ids: set[str], max_hops: int) -> float:
        """Same linear-decay shape as CallGraph.proximity, at class
        granularity. See that docstring for the rationale."""
        if class_id not in self.graph or not anchor_class_ids:
            return 0.0
        if class_id in anchor_class_ids:
            return 1.0

        # See CallGraph.proximity for why this is one bounded BFS rather
        # than a shortest_path_length() call per anchor.
        undirected = self.graph.to_undirected(as_view=True)
        distances = nx.single_source_shortest_path_length(undirected, class_id, cutoff=max_hops)
        reachable = [distances[a] for a in anchor_class_ids if a in distances]
        if not reachable:
            return 0.0
        return max(0.0, 1.0 - (min(reachable) / max_hops))

    def __len__(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def num_edges(self) -> int:
        return self.graph.number_of_edges()