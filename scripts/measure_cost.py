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
    for child in store.list_children():
        report, meta = await agent.run_match(agent.prepare_match(store, child.id))
        rows.append({"run": f"match {child.display_name}", **meta, "picks": [r.parent_name for r in report.recommendations]})
    for parent in store.list_parents()[:2]:
        _, meta = await agent.run_summary(agent.prepare_summary(store, "parent", parent.id))
        rows.append({"run": f"summary {parent.display_name}", **meta})

    for r in rows:
        print(f"{r['run']:<34} ${r['cost_usd']:.5f}  {r['requests']} req  in {r['input_tokens']:>5} "
              f"(cached {r['cached_input_tokens']:>5})  out {r['output_tokens']:>5}  {r['seconds']:>5}s  {r.get('picks', '')}")
    matches = [r for r in rows if r["run"].startswith("match")]
    print(f"\nAverage match run: ${sum(r['cost_usd'] for r in matches) / len(matches):.5f}, "
          f"{sum(r['seconds'] for r in matches) / len(matches):.1f}s")
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
