"""The optimization harness — the measurement side of the testbed.

Deliberately outside ``src/water_assistant_agent``: nothing the production image
ships imports this package (``plan.md`` §3). What lives here is the rollout
(:mod:`harness.run_case`), the answer contract the evaluation runs under
(:mod:`harness.contract`), and the invariants a case is held to before it is run
(:mod:`harness.assertions`).
"""
