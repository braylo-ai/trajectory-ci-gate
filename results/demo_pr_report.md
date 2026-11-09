## Trajectory gate: FAIL (2/5 tasks)

| Task | Result | Score | Deviations |
|---|---|---|---|
| refund_expired | FAIL | 0.60 | `missing_required` required call `check_refund_policy` never happened<br>`missing_required` required call `verify_identity` never happened |
| refund_large | pass | 1.00 | - |
| refund_mid | pass | 1.00 | - |
| refund_not_delivered | FAIL | 0.60 | `missing_required` required call `check_refund_policy` never happened<br>`missing_required` required call `verify_identity` never happened |
| refund_small | FAIL | 0.67 | `missing_required` required call `check_refund_policy` never happened<br>`missing_required` required call `verify_identity` never happened |
