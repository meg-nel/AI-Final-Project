"""Measure real cost, latency, and picks of the AI agents on the demo caseload.

Makes live model calls (about one cent in total). Usage:
    .venv/Scripts/python scripts/measure_cost.py [output.json]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import agent  # noqa: E402
from backend.seed import seed  # noqa: E402
from backend.storage import Store  # noqa: E402


async def main() -> None:
    store = Store(":memory:")
    seed(store)
    rows = []
    failures = []
    for child in store.list_children():
        try:
            report, meta = await agent.run_match(agent.prepare_match(store, child.id))
        except Exception as e:  # a timeout or rejected answer is a result too: record it and keep measuring
            failures.append(f"match {child.display_name}: {type(e).__name__}")
            continue
        rows.append({"run": f"match {child.display_name}", **meta, "picks": [r.parent_name for r in report.recommendations]})
    for parent in store.list_parents()[:2]:
        try:
            _, meta = await agent.run_summary(agent.prepare_summary(store, "parent", parent.id))
        except Exception as e:
            failures.append(f"summary {parent.display_name}: {type(e).__name__}")
            continue
        rows.append({"run": f"summary {parent.display_name}", **meta})

    for failure in failures:
        print(f"FAILED {failure}")
    for r in rows:
        print(f"{r['run']:<34} ${r['cost_usd']:.5f}  {r['requests']} req  in {r['input_tokens']:>5} "
              f"(cached {r['cached_input_tokens']:>5})  out {r['output_tokens']:>5}  {r['seconds']:>5}s  {r.get('picks', '')}")
    matches = [r for r in rows if r["run"].startswith("match")]
    print(f"\nAverage match run: ${sum(r['cost_usd'] for r in matches) / len(matches):.5f}, "
          f"{sum(r['seconds'] for r in matches) / len(matches):.1f}s")
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps({"runs": rows, "failures": failures}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
