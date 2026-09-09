import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.canonicalize import _group_key
from retrieval.graph_rag import QueryIntent, QueryPlan, link, plan, traverse

GOLDEN = Path(__file__).with_name("golden.json")

MIN_INTENT_ACCURACY = 0.90
MIN_ENTITY_RECALL = 0.80
MIN_PAPER_RECALL = 0.80
# Averages hide a single broken case, so each case is scored on its own and the
# gate is the share of cases that clear both bars.
MIN_CASE_PASS_RATE = 0.90


@dataclass
class Result:
    question: str
    expected_intent: str
    actual_intent: str | None
    entity_recall: float
    paper_recall: float
    missing_entities: list[str] = field(default_factory=list)
    missing_papers: list[str] = field(default_factory=list)
    facts: int = 0
    expect_empty: bool = False

    @property
    def intent_correct(self) -> bool:
        return self.actual_intent == self.expected_intent

    @property
    def passed(self) -> bool:
        if self.expect_empty:
            return self.facts == 0
        return (
            self.entity_recall >= MIN_ENTITY_RECALL
            and self.paper_recall >= MIN_PAPER_RECALL
        )


def _recall(expected: list[str], found: set[str]) -> tuple[float, list[str]]:
    if not expected:
        return 1.0, []
    missing = [e for e in expected if _group_key(e) not in found]
    return (len(expected) - len(missing)) / len(expected), missing


def evaluate(case: dict, offline: bool) -> Result:
    expected_intent = case["intent"]
    expect_empty = case.get("expect_empty", False)

    if offline:
        actual_intent = expected_intent
        entities = case["entities"]
    else:
        query_plan = plan(case["question"])
        actual_intent = str(query_plan.intent)
        entities = query_plan.entities

    linked = [link(name) for name in entities]
    linked = [name for name in linked if name]
    if not linked:
        return Result(case["question"], expected_intent, actual_intent,
                      1.0 if expect_empty else 0.0, 1.0 if expect_empty else 0.0,
                      [], [], 0, expect_empty)

    needed = 2 if actual_intent == QueryIntent.PATH_BETWEEN else 1
    if len(linked) < needed:
        return Result(case["question"], expected_intent, actual_intent,
                      1.0 if expect_empty else 0.0, 1.0 if expect_empty else 0.0,
                      [], [], 0, expect_empty)

    facts = traverse(
        QueryPlan(intent=QueryIntent(actual_intent), entities=entities), linked
    )

    seen_names = {_group_key(f["subject"]) for f in facts} | {
        _group_key(f["object"]) for f in facts
    }
    seen_papers = {p for f in facts for p in (f["papers"] or [])}

    entity_recall, missing_entities = _recall(case["expect_entities"], seen_names)
    paper_recall, missing_papers = _recall(case["expect_papers"], seen_papers)

    return Result(
        case["question"], expected_intent, actual_intent,
        entity_recall, paper_recall, missing_entities, missing_papers,
        len(facts), expect_empty,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Score retrieval against the golden set.")
    parser.add_argument("--golden", default=str(GOLDEN))
    parser.add_argument("--offline", action="store_true",
                        help="skip the planner and use each case's declared intent")
    args = parser.parse_args()

    cases = json.loads(Path(args.golden).read_text())
    results = [evaluate(case, args.offline) for case in cases]

    print(f" {'intent':<7} {'ent':>5} {'pap':>5} {'facts':>6}  question")
    for r in results:
        mark = "ok" if r.intent_correct else f"->{r.actual_intent}"
        flag = " " if r.passed else "!"
        print(
            f"{flag}{mark:<7} {r.entity_recall:>5.0%} {r.paper_recall:>5.0%} "
            f"{r.facts:>6}  {r.question}"
        )
        for missing in r.missing_entities:
            print(f"{'':22} missing entity {missing!r}")
        for missing in r.missing_papers:
            print(f"{'':22} missing paper  {missing!r}")
        if r.expect_empty and r.facts:
            print(f"{'':22} expected no facts, got {r.facts}")

    n = len(results)
    passed = sum(r.passed for r in results)
    positives = [r for r in results if not r.expect_empty] or results
    intent_accuracy = sum(r.intent_correct for r in results) / n
    entity_recall = sum(r.entity_recall for r in positives) / len(positives)
    paper_recall = sum(r.paper_recall for r in positives) / len(positives)

    print(f"\n{n} cases"
          f"{' (offline: planner not scored)' if args.offline else ''}")
    print(f"  intent accuracy {intent_accuracy:.0%}  (min {MIN_INTENT_ACCURACY:.0%})")
    print(f"  entity recall   {entity_recall:.0%}  (min {MIN_ENTITY_RECALL:.0%})")
    print(f"  paper recall    {paper_recall:.0%}  (min {MIN_PAPER_RECALL:.0%})")
    print(f"  cases passed    {passed}/{n}  (min {MIN_CASE_PASS_RATE:.0%})")

    failures = []
    if not args.offline and intent_accuracy < MIN_INTENT_ACCURACY:
        failures.append("intent accuracy below threshold")
    if entity_recall < MIN_ENTITY_RECALL:
        failures.append("mean entity recall below threshold")
    if paper_recall < MIN_PAPER_RECALL:
        failures.append("mean paper recall below threshold")
    if passed / n < MIN_CASE_PASS_RATE:
        failed = [r.question for r in results if not r.passed]
        failures.append(f"{n - passed} case(s) failed: " + "; ".join(failed))

    if failures:
        print("\nBLOCKED — do not promote:")
        for reason in failures:
            print(f"  ! {reason}")
        sys.exit(1)
    print("\nOK to promote")


if __name__ == "__main__":
    main()
