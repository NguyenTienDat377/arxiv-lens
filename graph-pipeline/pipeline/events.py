import functools
import json
import os
from datetime import datetime, timezone

from confluent_kafka import Producer

TOPIC = "graph.updated"
FLUSH_SECONDS = 10


@functools.cache
def _producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP", "localhost:9094"),
            "client.id": "graph-pipeline",
            # Fail fast: a build must not hang for minutes because Kafka is down.
            "socket.timeout.ms": 5000,
            "message.timeout.ms": 8000,
        }
    )


def _delivery_reporter(outcome: list[bool]):
    # flush() returns 0 once the queue drains, whether the messages were
    # acknowledged or timed out, so the callback is the only honest signal.
    def report(err, msg) -> None:
        if err is not None:
            print(f"  kafka: delivery failed: {err}")
            outcome.append(False)
        else:
            print(f"  kafka: {msg.topic()}[{msg.partition()}] offset {msg.offset()}")
            outcome.append(True)

    return report


def publish_graph_updated(snapshot_id: str, counts: dict[str, int]) -> bool:
    payload = {
        "event": TOPIC,
        "snapshot_id": snapshot_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        **counts,
    }

    # The graph build has already succeeded by the time this runs. Announcing it
    # is best effort: a broker outage must not turn a good build into a failure.
    outcome: list[bool] = []
    try:
        producer = _producer()
        producer.produce(
            TOPIC,
            key=snapshot_id.encode(),
            value=json.dumps(payload).encode(),
            callback=_delivery_reporter(outcome),
        )
        remaining = producer.flush(FLUSH_SECONDS)
    except Exception as error:
        print(f"  kafka: not published ({type(error).__name__}: {error})")
        return False

    if remaining:
        print(f"  kafka: not published ({remaining} message(s) still queued)")
    return bool(outcome) and all(outcome)


def main() -> None:
    published = publish_graph_updated("test", {"papers": 0, "entities": 0, "relations": 0})
    print("published" if published else "not published")


if __name__ == "__main__":
    main()
