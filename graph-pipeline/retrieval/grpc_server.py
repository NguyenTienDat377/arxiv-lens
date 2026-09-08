import argparse
import sys
from concurrent import futures
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generated"))

import graphrag_pb2 as pb
import graphrag_pb2_grpc as pb_grpc

from pipeline.build_graph import LABELS, _driver
from pipeline.snapshots import list_extracted
from retrieval.graph_rag import QueryIntent, QueryPlan, answer, link, plan, traverse

INTENT_PREFIX = "QUERY_INTENT_"
RELATION_PREFIX = "RELATION_TYPE_"
DEFAULT_MAX_FACTS = 50


def _to_proto_intent(intent: QueryIntent) -> int:
    return pb.QueryIntent.Value(f"{INTENT_PREFIX}{intent}")


def _from_proto_intent(value: int) -> QueryIntent | None:
    name = pb.QueryIntent.Name(value)
    if name == "QUERY_INTENT_UNSPECIFIED":
        return None
    return QueryIntent(name[len(INTENT_PREFIX):])


def _to_proto_relation(predicate: str) -> int:
    return pb.RelationType.Value(f"{RELATION_PREFIX}{predicate}")


class GraphRagService(pb_grpc.GraphRagServiceServicer):
    def Query(self, request, context):
        snapshot_id = request.snapshot_id or list_extracted()[-1]
        intent = _from_proto_intent(request.intent)
        entities = list(request.entities)

        # The planner is only consulted for what the caller did not supply.
        if intent is None or not entities:
            query_plan = plan(request.question)
            intent = intent or query_plan.intent
            entities = entities or list(query_plan.entities)

        resolved = [(name, link(name)) for name in entities]
        linked = [target for _, target in resolved if target]
        unresolved = [name for name, target in resolved if target is None]

        needed = 2 if intent == QueryIntent.PATH_BETWEEN else 1
        if len(linked) < needed:
            return pb.QueryResponse(
                answer="Not in the graph.",
                intent=_to_proto_intent(intent),
                linked_entities=linked,
                unresolved_entities=unresolved,
                status=pb.RESULT_STATUS_ENTITY_NOT_FOUND,
                snapshot_id=snapshot_id,
            )

        facts = traverse(QueryPlan(intent=intent, entities=entities), linked)
        facts = facts[: request.max_facts or DEFAULT_MAX_FACTS]

        if not facts:
            return pb.QueryResponse(
                answer="The graph holds no facts matching that traversal.",
                intent=_to_proto_intent(intent),
                linked_entities=linked,
                unresolved_entities=unresolved,
                status=pb.RESULT_STATUS_NO_FACTS,
                snapshot_id=snapshot_id,
            )

        return pb.QueryResponse(
            answer=answer(request.question, facts),
            facts=[
                pb.Fact(
                    subject=f["subject"],
                    predicate=_to_proto_relation(f["predicate"]),
                    object=f["object"],
                    papers=f["papers"] or [],
                )
                for f in facts
            ],
            intent=_to_proto_intent(intent),
            linked_entities=linked,
            unresolved_entities=unresolved,
            status=pb.RESULT_STATUS_OK,
            snapshot_id=snapshot_id,
        )

    def GetGraphStats(self, request, context):
        with _driver().session() as session:
            by_label = {
                row["label"]: row["n"]
                for row in session.run(
                    """
                    MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
                    RETURN head([l IN labels(n) WHERE l IN $labels]) AS label,
                           count(*) AS n
                    """,
                    labels=list(LABELS.values()),
                )
            }
            by_type = {
                row["type"]: row["n"]
                for row in session.run(
                    """
                    MATCH ()-[r]->() WHERE type(r) <> 'MENTIONED_IN'
                    RETURN type(r) AS type, count(*) AS n
                    """
                )
            }
            papers = session.run("MATCH (p:Paper) RETURN count(p) AS n").single()["n"]

        return pb.GraphStatsResponse(
            snapshot_id=list_extracted()[-1],
            papers=papers,
            entities=sum(by_label.values()),
            relations=sum(by_type.values()),
            entities_by_label=by_label,
            relations_by_type=by_type,
        )


def serve(port: int, workers: int) -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    pb_grpc.add_GraphRagServiceServicer_to_server(GraphRagService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"GraphRagService listening on :{port}")
    server.wait_for_termination()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve GraphRAG over gRPC.")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    serve(args.port, args.workers)


if __name__ == "__main__":
    main()
