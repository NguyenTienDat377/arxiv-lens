import argparse

import arxiv

from .models import Paper, parse_short_id
from .snapshots import dedupe, write_snapshot

DEFAULT_QUERY = 'cat:cs.AI AND (abs:"neuro-symbolic" OR abs:"neurosymbolic")'


def retrieve_papers(query: str = DEFAULT_QUERY, max_results: int = 10) -> list[Paper]:
    client = arxiv.Client()

    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    papers = []

    for result in client.results(search):
        arxiv_id, version = parse_short_id(result.get_short_id())

        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                version=version,
                title=result.title,
                abstract=result.summary,
                authors=[author.name for author in result.authors],
                categories=result.categories,
                published=result.published,
                updated=result.updated,
                doi=result.doi,
                pdf_url=result.pdf_url,
            )
        )

    return dedupe(papers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest arXiv papers into a corpus snapshot.")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--max-results", type=int, default=300)
    args = parser.parse_args()

    papers = retrieve_papers(query=args.query, max_results=args.max_results)
    snapshot_id = write_snapshot(papers, query=args.query)
    print(f"wrote {len(papers)} papers to snapshot {snapshot_id}")


if __name__ == "__main__":
    main()
