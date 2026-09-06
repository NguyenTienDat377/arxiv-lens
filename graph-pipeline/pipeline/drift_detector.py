import argparse
import sys
from dataclasses import dataclass, field

from .canonicalize import canonicalize
from .extract_entities import validate
from .models import ExtractionRecord
from .snapshots import list_extracted, load_extractions

MAX_CHANGED_SHARE = 0.05
MAX_LOST_EDGE_SHARE = 0.10
MAX_VIOLATION_RATE_INCREASE = 0.005


@dataclass
class DriftReport:
    previous: str
    current: str
    shared: int = 0
    papers_added: list[str] = field(default_factory=list)
    papers_removed: list[str] = field(default_factory=list)
    papers_changed: list[str] = field(default_factory=list)
    edges_lost: list[str] = field(default_factory=list)
    edges_gained: list[str] = field(default_factory=list)
    entities_lost: set[str] = field(default_factory=set)
    entities_gained: set[str] = field(default_factory=set)
    edges_shared_before: int = 0
    violations_previous: int = 0
    violations_current: int = 0
    relations_previous: int = 0
    relations_current: int = 0

    @property
    def changed_share(self) -> float:
        return len(self.papers_changed) / self.shared if self.shared else 0.0

    @property
    def lost_edge_share(self) -> float:
        return (
            len(self.edges_lost) / self.edges_shared_before
            if self.edges_shared_before
            else 0.0
        )

    @property
    def violation_rate_increase(self) -> float:
        before = self.violations_previous / (self.relations_previous or 1)
        after = self.violations_current / (self.relations_current or 1)
        return after - before

    def failures(self) -> list[str]:
        reasons = []
        if self.changed_share > MAX_CHANGED_SHARE:
            reasons.append(
                f"{len(self.papers_changed)}/{self.shared} unchanged papers "
                f"extracted differently ({self.changed_share:.1%} > "
                f"{MAX_CHANGED_SHARE:.0%})"
            )
        if self.lost_edge_share > MAX_LOST_EDGE_SHARE:
            reasons.append(
                f"{len(self.edges_lost)}/{self.edges_shared_before} edges lost among "
                f"shared papers ({self.lost_edge_share:.1%} > {MAX_LOST_EDGE_SHARE:.0%})"
            )
        if self.violation_rate_increase > MAX_VIOLATION_RATE_INCREASE:
            reasons.append(
                f"ontology violation rate rose {self.violation_rate_increase:+.2%} "
                f"({self.violations_previous}/{self.relations_previous} -> "
                f"{self.violations_current}/{self.relations_current})"
            )
        return reasons


def _edges(record: ExtractionRecord) -> set[tuple[str, str, str]]:
    return {
        (r.subject, str(r.predicate), r.object) for r in record.extraction.relations
    }


def _entities(record: ExtractionRecord) -> set[tuple[str, str]]:
    return {(e.name, str(e.type)) for e in record.extraction.entities}


def compare(
    previous: list[ExtractionRecord],
    current: list[ExtractionRecord],
    previous_id: str = "previous",
    current_id: str = "current",
) -> DriftReport:
    merged, _, _, _ = canonicalize(previous + current)
    old = {r.arxiv_id: r for r in merged[: len(previous)]}
    new = {r.arxiv_id: r for r in merged[len(previous) :]}

    report = DriftReport(previous=previous_id, current=current_id)
    report.papers_added = sorted(new.keys() - old.keys())
    report.papers_removed = sorted(old.keys() - new.keys())

    for arxiv_id in sorted(old.keys() & new.keys()):
        before, after = old[arxiv_id], new[arxiv_id]
        if before.version != after.version:
            continue  # a genuine revision, not unexplained churn

        report.shared += 1
        report.edges_shared_before += len(_edges(before))
        lost, gained = _edges(before) - _edges(after), _edges(after) - _edges(before)
        entities_lost = _entities(before) - _entities(after)
        entities_gained = _entities(after) - _entities(before)

        if lost or gained or entities_lost or entities_gained:
            report.papers_changed.append(arxiv_id)
        report.edges_lost += [f"{arxiv_id}  {s} --{p}--> {o}" for s, p, o in sorted(lost)]
        report.edges_gained += [f"{arxiv_id}  {s} --{p}--> {o}" for s, p, o in sorted(gained)]
        report.entities_lost |= {f"{n} ({t})" for n, t in entities_lost}
        report.entities_gained |= {f"{n} ({t})" for n, t in entities_gained}

    report.violations_previous = sum(len(validate(r.extraction)) for r in previous)
    report.violations_current = sum(len(validate(r.extraction)) for r in current)
    report.relations_previous = sum(len(r.extraction.relations) for r in previous)
    report.relations_current = sum(len(r.extraction.relations) for r in current)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gate a snapshot for promotion by comparing it to the previous one."
    )
    parser.add_argument("--previous", default=None)
    parser.add_argument("--current", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    snapshots = list_extracted()
    current_id = args.current or snapshots[-1]
    previous_id = args.previous or (
        snapshots[snapshots.index(current_id) - 1]
        if snapshots.index(current_id) > 0
        else None
    )
    if previous_id is None:
        print(f"{current_id} is the first extracted snapshot; nothing to compare")
        return

    report = compare(
        load_extractions(previous_id),
        load_extractions(current_id),
        previous_id,
        current_id,
    )

    print(f"{report.previous} -> {report.current}")
    print(f"  papers added    {len(report.papers_added)}")
    print(f"  papers removed  {len(report.papers_removed)}")
    print(f"  papers shared   {report.shared}")
    print(f"  of those, changed {len(report.papers_changed)} ({report.changed_share:.1%})")
    print(f"  edges lost/gained {len(report.edges_lost)}/{len(report.edges_gained)}")
    print(
        f"  violation rate  {report.violations_previous}/{report.relations_previous} -> "
        f"{report.violations_current}/{report.relations_current} "
        f"({report.violation_rate_increase:+.2%})"
    )

    if args.verbose:
        for line in report.edges_lost[:20]:
            print(f"    - {line}")
        for line in report.edges_gained[:20]:
            print(f"    + {line}")

    failures = report.failures()
    if failures:
        print("\nBLOCKED — do not promote this snapshot:")
        for reason in failures:
            print(f"  ! {reason}")
        sys.exit(1)

    print("\nOK to promote")


if __name__ == "__main__":
    main()
