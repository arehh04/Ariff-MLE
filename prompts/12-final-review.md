# 12 Adversarial review before submitting

Act as a sceptical reviewer at OneCredit. Read the repo, do not trust the docs. Report (do not fix) with file:line references:
1. Correctness bugs and races (acks, locks, transactions, projection, timestamps, NUL, empty batches, shutdown).
2. Places where DESIGN.md claims something the code or tests do not show.
3. Weak or vacuous tests (assert nothing meaningful; would still pass if the code were broken).
4. Anything violating `AGENTS.md` (secrets, modified provided files, SQL string building, paid APIs).
5. README steps that would fail on a clean machine.
Rank findings by severity. Then wait; I decide what to fix.
