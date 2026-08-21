"""Does a client-supplied transaction timeout actually stop a pathological read?

docs/fragilities.md #15 answered this once and got it wrong for a reason worth
keeping: the probe called ``execute_query(query, params, timeout=2.0)``, and the
driver merges its ``**kwargs`` into the CYPHER PARAMETERS -- so the timeout
arrived as an unused ``$timeout`` and the read ran unbounded. The conclusion
drawn ("the server setting is the only control") was reached from a call that
never carried a timeout at all.

``Neo4jClient.run_read`` now sends it inside a ``Query`` object, and the
container sets ``db.transaction.timeout``. This re-runs the measurement:

    uv run python -m scripts.probe_read_timeout

It runs three reads against the live graph and prints what happened to each:

  1. a cheap read with a generous timeout -- the control; it must succeed;
  2. a deliberately expensive cartesian read with a SHORT client timeout --
     the question: does the client hint bite before the server's ceiling?
  3. the same expensive read with NO client timeout -- what the server's own
     db.transaction.timeout does on its own.

Nothing is written, and the read-only path is the one under test, so this is
safe against any environment you can point it at.
"""

import asyncio
import time
from typing import Any

from neo4j.exceptions import Neo4jError

from graph.neo4j_client import Neo4jClient

#: Work the planner cannot answer without doing it.
#:
#: The first thing tried here was a three-way cartesian product over the 84k
#: intersections, and it came back in 0.05 s with 595,608,748,359,353 -- the
#: planner multiplies the counts rather than walking the rows. A timeout probe
#: measures nothing against a query the database never actually runs, which is
#: worth writing down: it is the same class of mistake as the timeout that was
#: never sent. Half a billion iterations with a predicate on each is not
#: optimisable, and takes minutes if nothing stops it.
EXPENSIVE = """
UNWIND range(1, 500000000) AS x
WITH x WHERE x % 7 = 0
RETURN count(x) AS multiples
"""


async def _timed(
    client: Neo4jClient, query: str, timeout_s: float | None
) -> tuple[str, float, Any]:
    started = time.perf_counter()
    try:
        rows = await client.run_read(query, timeout_s=timeout_s)
    except Neo4jError as error:
        return (error.code or type(error).__name__), time.perf_counter() - started, None
    except Exception as error:  # noqa: BLE001 - reporting, not handling
        return type(error).__name__, time.perf_counter() - started, None
    return "ok", time.perf_counter() - started, rows


async def main() -> None:
    async with Neo4jClient() as client:
        server = await client.run_read(
            "SHOW SETTINGS YIELD name, value "
            "WHERE name = 'db.transaction.timeout' RETURN value"
        )
        configured = server[0]["value"] if server else "unknown"
        print(f"server db.transaction.timeout = {configured}")

        counts = await client.run_read("MATCH (i:Intersection) RETURN count(i) AS n")
        print(f"intersections in the graph = {counts[0]['n']:,}\n")

        for label, query, timeout_s in (
            ("control: cheap read, 10 s client timeout", "RETURN 1 AS ok", 10.0),
            ("expensive read, 2 s CLIENT timeout", EXPENSIVE, 2.0),
            ("expensive read, no client timeout (server only)", EXPENSIVE, None),
        ):
            outcome, seconds, rows = await _timed(client, query, timeout_s)
            print(f"{label}\n    -> {outcome} after {seconds:.2f} s  rows={rows}")


if __name__ == "__main__":
    asyncio.run(main())
