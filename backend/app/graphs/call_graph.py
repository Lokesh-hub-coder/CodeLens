"""
Method-level call graph: nodes are method ids ("com.foo.Bar#doThing(int)"),
directed edges are "caller calls callee".

This is one of the three structural signals ACOR combines (the other two
are the dependency graph and the symbol index) -- see docs/architecture.md
for how the three line up with the synopsis' own architecture diagram.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from app.parsing.java_bridge import CallEdge, MethodInfo


@dataclass(frozen=True)
class CallGraph:
    graph: nx.DiGraph

    @classmethod
    def build(cls, methods: list[MethodInfo], call_edges: list[CallEdge]) -> "CallGraph":
        g = nx.DiGraph()
        for m in methods:
            g.add_node(m.id, class_id=m.class_id, file_path=m.file_path, name=m.name)
        for edge in call_edges:
            # Callees outside the parsed set (JDK/library calls JavaParser's
            # symbol solver can't resolve to repo source) are skipped rather
            # than added as dangling nodes -- they carry no retrievable
            # source and would only pollute proximity calculations.
            if edge.caller_id in g and edge.callee_id in g:
                g.add_edge(edge.caller_id, edge.callee_id)
        return cls(graph=g)

    def contains(self, method_id: str) -> bool:
        return method_id in self.graph

    def proximity(self, method_id: str, anchor_ids: set[str], max_hops: int) -> float:
        """1.0 if method_id *is* an anchor, decaying linearly to 0.0 at
        max_hops away (undirected shortest path -- a caller of an anchor is
        just as structurally relevant as something the anchor calls), and
        0.0 if unreachable within max_hops or either node is unknown.

        `anchor_ids` are typically the symbols query_understanding.py
        extracted directly from the developer's query (e.g. a stack-trace
        method name), so this rewards candidates that sit *near* what the
        developer actually named, not just anything semantically similar.
        """
        if method_id not in self.graph or not anchor_ids:
            return 0.0
        if method_id in anchor_ids:
            return 1.0

        # Single BFS from method_id (bounded to max_hops) rather than one
        # shortest_path_length() call per anchor: cheaper when there are
        # several anchors, and networkx's top-level shortest_path_length()
        # dispatcher does not actually accept a `cutoff` kwarg -- only the
        # single-source variant does.
        undirected = self.graph.to_undirected(as_view=True)
        distances = nx.single_source_shortest_path_length(undirected, method_id, cutoff=max_hops)
        reachable = [distances[a] for a in anchor_ids if a in distances]
        if not reachable:
            return 0.0
        return max(0.0, 1.0 - (min(reachable) / max_hops))

    def neighbors_within_hops(self, method_id: str, hops: int) -> set[str]:
        """Undirected k-hop neighborhood -- used when the ranker wants to
        pull in short signature-only stubs of a selected method's direct
        callers/callees for coherence (see docs/architecture.md, 'Extending
        the ranker')."""
        if method_id not in self.graph:
            return set()
        undirected = self.graph.to_undirected(as_view=True)
        lengths = nx.single_source_shortest_path_length(undirected, method_id, cutoff=hops)
        return {node for node in lengths if node != method_id}

    def __len__(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def num_edges(self) -> int:
        return self.graph.number_of_edges()