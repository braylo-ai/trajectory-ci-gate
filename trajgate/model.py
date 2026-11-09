"""Data model: a recorded trajectory and a golden DAG spec."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Step:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None


@dataclass
class Trajectory:
    task_id: str
    steps: list[Step]
    final_answer: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Trajectory":
        return cls(
            d["task_id"],
            [Step(s["tool"], s.get("args", {}), s.get("result")) for s in d["steps"]],
            d.get("final_answer", ""),
        )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "final_answer": self.final_answer,
            "steps": [
                {"tool": s.tool, "args": s.args, "result": s.result} for s in self.steps
            ],
        }

    @classmethod
    def load(cls, path: str | Path) -> "Trajectory":
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass
class Node:
    id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)  # key -> matcher
    optional: bool = False


@dataclass
class GoldenDAG:
    """Golden spec.

    nodes:      required (or optional) tool calls, each with argument matchers
    edges:      (a, b) means node a's call must happen before node b's call
    forbidden:  tools that must never be called
    allow_extra: tolerate tool calls that match no node (default True)
    max_calls:  per-tool ceiling (loops / retry storms)
    max_steps:  global step budget
    """

    task_id: str
    nodes: dict[str, Node]
    edges: list[tuple[str, str]] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    allow_extra: bool = True
    max_calls: dict[str, int] = field(default_factory=dict)
    max_steps: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "GoldenDAG":
        nodes = {
            nid: Node(
                nid, n["tool"], n.get("args", {}) or {}, bool(n.get("optional", False))
            )
            for nid, n in d["nodes"].items()
        }
        edges = [tuple(e) for e in d.get("edges", [])]
        for a, b in edges:
            if a not in nodes or b not in nodes:
                raise ValueError(f"edge references unknown node: {a}->{b}")
        g = cls(
            d["task_id"],
            nodes,
            edges,
            list(d.get("forbidden", [])),
            bool(d.get("allow_extra", True)),
            dict(d.get("max_calls", {})),
            d.get("max_steps"),
        )
        g.topo_order()  # raises on cycles
        return g

    @classmethod
    def load(cls, path: str | Path) -> "GoldenDAG":
        return cls.from_dict(yaml.safe_load(Path(path).read_text()))

    def preds(self, nid: str) -> list[str]:
        return [a for a, b in self.edges if b == nid]

    def topo_order(self) -> list[str]:
        indeg = {n: 0 for n in self.nodes}
        for _, b in self.edges:
            indeg[b] += 1
        order, ready = [], [n for n in self.nodes if indeg[n] == 0]
        while ready:
            n = ready.pop(0)
            order.append(n)
            for a, b in self.edges:
                if a == n:
                    indeg[b] -= 1
                    if indeg[b] == 0:
                        ready.append(b)
        if len(order) != len(self.nodes):
            raise ValueError("golden graph has a cycle")
        return order
