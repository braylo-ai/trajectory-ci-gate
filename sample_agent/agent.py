"""Sample refund-support agent (deterministic, rule-based policy; no LLM).

Tools hit an in-memory fake backend. `variant="pr"` simulates a regression a
PR might introduce: a "fast path" that skips identity verification and the
policy check for small orders. Its final answers are IDENTICAL to main's, so
only a trajectory-level gate catches it.
"""
from __future__ import annotations

import json
from pathlib import Path

from trajgate.model import Step, Trajectory

ORDERS = {
    "A100": {"customer": "C1", "amount": 40.0, "days": 5, "status": "delivered"},
    "A200": {"customer": "C2", "amount": 250.0, "days": 12, "status": "delivered"},
    "A300": {"customer": "C3", "amount": 900.0, "days": 3, "status": "delivered"},
    "A400": {"customer": "C1", "amount": 25.0, "days": 45, "status": "delivered"},
    "A500": {"customer": "C4", "amount": 60.0, "days": 2, "status": "shipped"},
}
CUSTOMERS = {"C1": "dana@example.test", "C2": "eli@example.test", "C3": "ren@example.test", "C4": "kai@example.test"}


class Tools:
    def __init__(self):
        self.log: list[Step] = []

    def call(self, tool, **args):
        result = getattr(self, "_" + tool)(**args)
        self.log.append(Step(tool, args, result))
        return result

    def _get_order(self, order_id):
        o = ORDERS[order_id]
        return {"order_id": order_id, **o}

    def _lookup_customer(self, customer_id):
        return {"customer_id": customer_id, "email": CUSTOMERS[customer_id]}

    def _verify_identity(self, customer_id, method):
        return {"verified": True}

    def _check_refund_policy(self, order_id, amount):
        o = ORDERS[order_id]
        ok = o["days"] <= 30 and o["status"] == "delivered"
        return {"eligible": ok, "needs_approval": amount > 500}

    def _issue_refund(self, order_id, amount):
        return {"refund_id": f"R-{order_id}", "amount": amount}

    def _escalate_to_human(self, order_id, reason):
        return {"ticket": f"T-{order_id}"}

    def _send_email(self, to, template):
        return {"sent": True}


def run(task: dict, variant: str = "main") -> Trajectory:
    t = Tools()
    oid = task["order_id"]
    order = t.call("get_order", order_id=oid)
    cust = t.call("lookup_customer", customer_id=order["customer"])
    fast = variant == "pr" and order["amount"] < 100
    if not fast:
        t.call("verify_identity", customer_id=cust["customer_id"], method="email_otp")
        pol = t.call("check_refund_policy", order_id=oid, amount=order["amount"])
    else:  # regression: trusts a local heuristic instead of the policy tool
        pol = {"eligible": order["days"] <= 30 and order["status"] == "delivered", "needs_approval": False}
    if not pol["eligible"]:
        t.call("send_email", to=cust["email"], template="refund_denied")
        answer = f"Order {oid} is not eligible for a refund."
    elif pol["needs_approval"]:
        t.call("escalate_to_human", order_id=oid, reason="amount_over_500")
        t.call("send_email", to=cust["email"], template="refund_pending_review")
        answer = f"Refund for order {oid} sent for manager review."
    else:
        r = t.call("issue_refund", order_id=oid, amount=order["amount"])
        t.call("send_email", to=cust["email"], template="refund_issued")
        answer = f"Refund {r['refund_id']} of ${order['amount']:.2f} issued for order {oid}."
    return Trajectory(task["task_id"], t.log, answer)


def main(variant="main", out="runs", tasks_dir="scenarios"):
    Path(out).mkdir(parents=True, exist_ok=True)
    for p in sorted(Path(tasks_dir).glob("*.json")):
        task = json.loads(p.read_text())
        tr = run(task, variant)
        (Path(out) / f"{task['task_id']}.json").write_text(json.dumps(tr.to_dict(), indent=2))


if __name__ == "__main__":
    import sys
    main(*sys.argv[1:])
