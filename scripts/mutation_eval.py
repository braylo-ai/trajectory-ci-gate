"""Mutation study (SYNTHETIC mutations of the sample agent's real trajectories).

For each passing main-branch trajectory, generate every single-edit mutation:
  drop      remove one step
  swap      swap two adjacent steps
  corrupt   change the first argument of one step
  dup       duplicate one step
The final answer is left unchanged, so a final-answer-only check passes
every mutant by construction. We report how many the trajectory gate flags,
and list which mutants it accepts (spec-permitted reorders, etc.).
Also grades the `pr` regression variant end to end.
"""
import copy
import json
from collections import Counter
from pathlib import Path

from sample_agent.agent import run
from trajgate.grader import grade
from trajgate.model import GoldenDAG, Step

ROOT = Path(__file__).resolve().parents[1]


def mutants(tr):
    n = len(tr.steps)
    for i in range(n):
        m = copy.deepcopy(tr); del m.steps[i]; yield "drop", f"drop {tr.steps[i].tool}", m
    for i in range(n - 1):
        m = copy.deepcopy(tr); m.steps[i], m.steps[i + 1] = m.steps[i + 1], m.steps[i]
        yield "swap", f"swap {tr.steps[i].tool}<->{tr.steps[i+1].tool}", m
    for i in range(n):
        m = copy.deepcopy(tr); k = next(iter(m.steps[i].args)); m.steps[i].args[k] = "WRONG"
        yield "corrupt", f"corrupt {tr.steps[i].tool}.{k}", m
    for i in range(n):
        m = copy.deepcopy(tr); m.steps.insert(i + 1, copy.deepcopy(m.steps[i]))
        yield "dup", f"dup {tr.steps[i].tool}", m


def main():
    tasks = [json.loads(p.read_text()) for p in sorted((ROOT / "scenarios").glob("*.json"))]
    total, caught, accepted = Counter(), Counter(), []
    base_pass = 0
    pr = {}
    for t in tasks:
        g = GoldenDAG.load(ROOT / "golden" / f"{t['task_id']}.yaml")
        tr = run(t, "main")
        base_pass += grade(tr, g).passed
        for kind, desc, m in mutants(tr):
            total[kind] += 1
            if not grade(m, g).passed:
                caught[kind] += 1
            else:
                accepted.append(f"{t['task_id']}: {desc}")
        trp = run(t, "pr")
        rep = grade(trp, g)
        pr[t["task_id"]] = {"passed": rep.passed, "final_answer_same": trp.final_answer == tr.final_answer,
                            "deviations": [d.kind for d in rep.deviations]}
    out = {
        "tasks": len(tasks), "main_branch_pass": base_pass,
        "mutants_total": sum(total.values()), "mutants_caught": sum(caught.values()),
        "by_kind": {k: f"{caught[k]}/{total[k]}" for k in total},
        "final_answer_only_caught": 0, "final_answer_only_note": "final answer unchanged in every mutant by construction",
        "accepted_mutants": accepted,
        "pr_variant": pr,
        "pr_variant_tasks_failed": sum(not v["passed"] for v in pr.values()),
        "pr_variant_final_answers_identical": sum(v["final_answer_same"] for v in pr.values()),
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "mutation_eval.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "accepted_mutants"}, indent=2))
    print("accepted:", *accepted, sep="\n  ")


if __name__ == "__main__":
    main()
