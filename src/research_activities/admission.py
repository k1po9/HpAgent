"""Research-specific parent admission; the Harness owns only shared admission."""

import json

from research_adapters.web import discovery_query
from research_domain.models import SourceStrategy


def admit_research(uow, run, inputs):
    budget = uow.execute(
        "SELECT limits,used,reserved FROM run_budgets WHERE run_id=%s", (run["run_id"],)
    ).fetchone()
    used = budget["used"].get("research_iterations", 0)
    limit = budget["limits"].get("research_iterations", 0)
    remaining = max(0, limit - used - budget["reserved"].get("research_iterations", 0))
    if not remaining:
        queries = uow.execute(
            "SELECT DISTINCT query FROM research_iterations WHERE run_id=%s LIMIT 12",
            (run["run_id"],),
        ).fetchall()
        raise ValueError(
            f"Research stopped: this Run used {used}/{limit} research rounds; remaining=0. "
            "No search started. Do not call Research again or create a Run to bypass the limit. Queries: "
            + json.dumps([row["query"] for row in queries], ensure_ascii=False)[:250]
        )
    strategy = SourceStrategy.from_dict(inputs.get("source_strategy"))
    query = discovery_query(inputs["objective"], strategy)
    if uow.execute(
        "SELECT 1 FROM research_iterations i JOIN research_plans p USING(research_id) "
        "WHERE i.run_id=%s AND i.query=%s AND p.plan->'source_strategy'=%s::jsonb LIMIT 1",
        (run["run_id"], query, json.dumps(strategy.to_dict())),
    ).fetchone():
        raise ValueError(
            "Research stopped: no new effective query or source strategy. "
            "Reuse the prior result and report its gaps; do not repeat Research in this Run."
        )
