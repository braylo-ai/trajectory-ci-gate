"""Grade a trajectory against a golden DAG.

Matching: walk the DAG in topological order and bind each node to the earliest
unused step that (a) calls the node's tool, (b) satisfies its argument
matchers, and (c) comes after every predecessor's bound step. Argument
matchers can reference earlier bound steps (`ref: lookup.result.order_id`), so
the gate checks data flow, not just tool names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .model import GoldenDAG, Trajectory

_MISSING = object()


@dataclass
class Deviation:
    kind: str  # missing_required | order_violation | arg_mismatch | forbidden_tool | unexpected_tool | max_calls | max_steps
    node: str | None
    message: str
    step: int | None = None


@dataclass
class Report:
    task_id: str
    passed: bool
    score: float  # matched required nodes / required nodes, 0 if any hard violation
    bindings: dict[str, int] = field(default_factory=dict)
    deviations: list[Deviation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "score": round(self.score, 4),
            "bindings": self.bindings,
            "deviations": [d.__dict__ for d in self.deviations],
        }


def _get_path(obj: Any, path: list[str]) -> Any:
    for p in path:
        if isinstance(obj, dict) and p in obj:
            obj = obj[p]
        elif isinstance(obj, list) and p.isdigit() and int(p) < len(obj):
            obj = obj[int(p)]
        else:
            return _MISSING
    return obj


def _resolve_ref(ref: str, traj: Trajectory, bindings: dict[str, int]) -> Any:
    node, kind, *path = ref.split(".")
    if node not in bindings:
        return _MISSING
    step = traj.steps[bindings[node]]
    base = (
        step.result if kind == "result" else step.args if kind == "args" else _MISSING
    )
    return _get_path(base, path) if base is not _MISSING else _MISSING


def check_value(
    matcher: Any, value: Any, traj: Trajectory, bindings: dict[str, int]
) -> str | None:
    """Return None if `value` satisfies `matcher`, else a reason string."""
    if not isinstance(matcher, dict):
        return None if value == matcher else f"expected {matcher!r}, got {value!r}"
    if matcher.get("absent"):
        return None if value is _MISSING else f"must be absent, got {value!r}"
    if value is _MISSING:
        return "argument missing"
    if matcher.get("any"):
        return None
    if "equals" in matcher and value != matcher["equals"]:
        return f"expected {matcher['equals']!r}, got {value!r}"
    if "in" in matcher and value not in matcher["in"]:
        return f"{value!r} not in {matcher['in']!r}"
    if "regex" in matcher and not (
        isinstance(value, str) and re.fullmatch(matcher["regex"], value)
    ):
        return f"{value!r} does not match /{matcher['regex']}/"
    if "gte" in matcher and not (
        isinstance(value, (int, float)) and value >= matcher["gte"]
    ):
        return f"{value!r} < {matcher['gte']}"
    if "lte" in matcher and not (
        isinstance(value, (int, float)) and value <= matcher["lte"]
    ):
        return f"{value!r} > {matcher['lte']}"
    if "ref" in matcher:
        want = _resolve_ref(matcher["ref"], traj, bindings)
        if want is _MISSING:
            return f"reference {matcher['ref']} unresolved"
        if value != want:
            return f"expected value from {matcher['ref']} ({want!r}), got {value!r}"
    return None


def _arg_problems(node, step, traj, bindings) -> list[str]:
    probs = []
    for key, m in node.args.items():
        v = _get_path(step.args, key.split("."))
        why = check_value(m, v, traj, bindings)
        if why:
            probs.append(f"{key}: {why}")
    return probs


def grade(traj: Trajectory, dag: GoldenDAG) -> Report:
    devs: list[Deviation] = []
    bindings: dict[str, int] = {}
    used: set[int] = set()

    for nid in dag.topo_order():
        node = dag.nodes[nid]
        after = max((bindings[p] for p in dag.preds(nid) if p in bindings), default=-1)
        chosen, closest_problem, early = None, None, None
        for i, step in enumerate(traj.steps):
            if i in used or step.tool != node.tool:
                continue
            probs = _arg_problems(node, step, traj, bindings)
            if probs:
                closest_problem = closest_problem or (i, probs)
                continue
            if i <= after:
                early = early if early is not None else i
                continue
            chosen = i
            break
        if chosen is not None:
            bindings[nid] = chosen
            used.add(chosen)
            continue
        if node.optional:
            continue
        if early is not None:
            pred_desc = ", ".join(p for p in dag.preds(nid))
            devs.append(
                Deviation(
                    "order_violation",
                    nid,
                    f"`{node.tool}` (step {early}) ran before required predecessor(s): {pred_desc}",
                    early,
                )
            )
        elif closest_problem is not None:
            i, probs = closest_problem
            devs.append(
                Deviation(
                    "arg_mismatch",
                    nid,
                    f"`{node.tool}` at step {i}: " + "; ".join(probs),
                    i,
                )
            )
        else:
            devs.append(
                Deviation(
                    "missing_required",
                    nid,
                    f"required call `{node.tool}` never happened",
                )
            )

    for i, step in enumerate(traj.steps):
        if step.tool in dag.forbidden:
            devs.append(
                Deviation(
                    "forbidden_tool",
                    None,
                    f"forbidden tool `{step.tool}` called at step {i}",
                    i,
                )
            )
        elif not dag.allow_extra and i not in used:
            devs.append(
                Deviation(
                    "unexpected_tool",
                    None,
                    f"unexpected call `{step.tool}` at step {i}",
                    i,
                )
            )
    for tool, cap in dag.max_calls.items():
        n = sum(1 for s in traj.steps if s.tool == tool)
        if n > cap:
            devs.append(
                Deviation("max_calls", None, f"`{tool}` called {n} times (max {cap})")
            )
    if dag.max_steps is not None and len(traj.steps) > dag.max_steps:
        devs.append(
            Deviation(
                "max_steps", None, f"{len(traj.steps)} steps (max {dag.max_steps})"
            )
        )

    required = [n for n in dag.nodes.values() if not n.optional]
    matched = sum(1 for n in required if n.id in bindings)
    score = matched / len(required) if required else 1.0
    hard = [
        d
        for d in devs
        if d.kind not in ("missing_required", "order_violation", "arg_mismatch")
    ]
    if hard:
        score = 0.0
    return Report(
        traj.task_id, passed=not devs, score=score, bindings=bindings, deviations=devs
    )
