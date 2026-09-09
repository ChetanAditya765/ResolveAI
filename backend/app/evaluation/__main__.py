"""Run the offline scenario catalog and save a reviewable trace report."""

import argparse
import json
from pathlib import Path

from app.core.config import Settings
from app.evaluation.contracts import CATALOG_VERSION, EVALUATOR_VERSION, ExpectedOutcome
from app.evaluation.harness import run_scenario
from app.evaluation.scenarios import SCENARIOS
from app.evaluation.scoring import score_trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", help="Scenario ID; repeat to select cases.")
    parser.add_argument(
        "--output", type=Path, help="Optional JSON report with full execution traces."
    )
    args = parser.parse_args()
    selected = set(args.scenario or [item.id for item in SCENARIOS])
    unknown = selected - {item.id for item in SCENARIOS}
    if unknown:
        parser.error("Unknown scenario IDs: " + ", ".join(sorted(unknown)))
    settings = Settings(llm_provider="demo", embedding_provider="local_hash")
    results = []
    for scenario in SCENARIOS:
        if scenario.id not in selected:
            continue
        trace = run_scenario(scenario, settings)
        expected = ExpectedOutcome.model_validate(scenario.model_dump(mode="json"))
        score = score_trace(trace, expected)
        print(f"{'PASS' if score.passed else 'FAIL'} {scenario.id}")
        for assertion in score.assertions:
            if assertion.passed is False:
                print(f"  {assertion.name}: {assertion.summary}")
        results.append(
            {
                "scenario_id": scenario.id,
                "expected": expected.model_dump(mode="json"),
                "score": score.model_dump(mode="json"),
                "trace": trace.model_dump(mode="json"),
            }
        )
    report = {
        "catalog_version": CATALOG_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "total": len(results),
        "passed": sum(item["score"]["passed"] for item in results),
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{report['passed']}/{report['total']} scenarios passed. Providers: demo / local_hash.")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
