import copy
import json
from pathlib import Path

import pytest

from sample_agent.agent import run
from trajgate import cli
from trajgate.grader import check_value, grade
from trajgate.model import GoldenDAG, Step, Trajectory

ROOT = Path(__file__).resolve().parents[1]
TASKS = {p.stem: json.loads(p.read_text()) for p in (ROOT / "scenarios").glob("*.json")}


def golden(t):
    return GoldenDAG.load(ROOT / "golden" / f"{t}.yaml")


def kinds(rep):
    return [d.kind for d in rep.deviations]


@pytest.mark.parametrize("task", sorted(TASKS))
def test_main_branch_agent_passes(task):
    rep = grade(run(TASKS[task], "main"), golden(task))
    assert rep.passed and rep.score == 1.0, rep.deviations


def test_drop_step_is_missing_required():
    tr = run(TASKS["refund_small"])
    tr.steps = [s for s in tr.steps if s.tool != "verify_identity"]
    assert "missing_required" in kinds(grade(tr, golden("refund_small")))


def test_refund_before_verify_is_order_violation():
    tr = run(TASKS["refund_small"])
    i = next(i for i, s in enumerate(tr.steps) if s.tool == "issue_refund")
    tr.steps.insert(1, tr.steps.pop(i))
    rep = grade(tr, golden("refund_small"))
    assert "order_violation" in kinds(rep)


def test_wrong_amount_is_arg_mismatch_via_ref():
    tr = run(TASKS["refund_small"])
    next(s for s in tr.steps if s.tool == "issue_refund").args["amount"] = 999.0
    rep = grade(tr, golden("refund_small"))
    assert kinds(rep)[0] == "arg_mismatch" and "order.result.amount" in rep.deviations[0].message


def test_forbidden_tool_zeroes_score():
    tr = run(TASKS["refund_large"])
    tr.steps.append(Step("issue_refund", {"order_id": "A300", "amount": 900.0}, {}))
    rep = grade(tr, golden("refund_large"))
    assert "forbidden_tool" in kinds(rep) and rep.score == 0.0


def test_unexpected_tool_when_allow_extra_false():
    tr = run(TASKS["refund_mid"])
    tr.steps.insert(2, Step("get_order", {"order_id": "A999"}, {}))
    assert "unexpected_tool" in kinds(grade(tr, golden("refund_mid")))


def test_max_calls_and_max_steps():
    tr = run(TASKS["refund_mid"])
    r = next(s for s in tr.steps if s.tool == "issue_refund")
    tr.steps += [copy.deepcopy(r) for _ in range(3)]
    k = kinds(grade(tr, golden("refund_mid")))
    assert "max_calls" in k and "max_steps" in k


def test_parallel_branches_may_reorder():
    tr = run(TASKS["refund_small"])
    a = next(i for i, s in enumerate(tr.steps) if s.tool == "verify_identity")
    tr.steps[a], tr.steps[a + 1] = tr.steps[a + 1], tr.steps[a]
    assert grade(tr, golden("refund_small")).passed


def test_optional_node_and_matchers():
    g = GoldenDAG.from_dict({"task_id": "t", "nodes": {
        "a": {"tool": "search", "args": {"q": {"regex": "refund.*"}, "k": {"gte": 1, "lte": 10}}},
        "b": {"tool": "log", "optional": True}}})
    assert grade(Trajectory("t", [Step("search", {"q": "refund policy", "k": 5})]), g).passed
    assert not grade(Trajectory("t", [Step("search", {"q": "refund", "k": 50})]), g).passed


def test_check_value_absent_and_missing():
    tr = Trajectory("t", [])
    from trajgate.grader import _MISSING
    assert check_value({"absent": True}, _MISSING, tr, {}) is None
    assert check_value({"any": True}, _MISSING, tr, {}) == "argument missing"


def test_cycle_and_bad_edge_rejected():
    with pytest.raises(ValueError):
        GoldenDAG.from_dict({"task_id": "t", "nodes": {"a": {"tool": "x"}, "b": {"tool": "y"}},
                             "edges": [["a", "b"], ["b", "a"]]})
    with pytest.raises(ValueError):
        GoldenDAG.from_dict({"task_id": "t", "nodes": {"a": {"tool": "x"}}, "edges": [["a", "zz"]]})


# ---------- demo PR scenario: same final answers, different trajectories ----------

def _record(variant, tmp):
    from sample_agent.agent import main
    main(variant, str(tmp), str(ROOT / "scenarios"))
    return tmp


def test_demo_pr_final_answers_identical():
    for t in TASKS.values():
        assert run(t, "main").final_answer == run(t, "pr").final_answer


def test_demo_pr_gate_cli_main_passes(tmp_path):
    runs = _record("main", tmp_path / "runs")
    assert cli.main(["check", "--golden", str(ROOT / "golden"), "--runs", str(runs)]) == 0


def test_demo_pr_gate_cli_pr_fails_with_report(tmp_path):
    runs = _record("pr", tmp_path / "runs")
    rep, js = tmp_path / "r.md", tmp_path / "r.json"
    code = cli.main(["check", "--golden", str(ROOT / "golden"), "--runs", str(runs),
                     "--report", str(rep), "--json", str(js)])
    assert code == 1
    md = rep.read_text()
    assert "FAIL" in md and "verify_identity" in md and "check_refund_policy" in md
    data = json.loads(js.read_text())
    assert sum(not r["passed"] for r in data["reports"]) == 3


def test_cli_flags_ungated_and_missing_runs(tmp_path):
    runs = _record("main", tmp_path / "runs")
    (runs / "refund_small.json").rename(runs / "new_task.json")
    reports, problems = cli.check(ROOT / "golden", runs)
    assert any("refund_small" in p for p in problems) and any("new_task" in p for p in problems)
