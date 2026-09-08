package com.arxivlens.queryservice.application;

import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import com.arxivlens.queryservice.domain.Fact;
import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryIntent;
import com.arxivlens.queryservice.domain.QueryResult;
import com.arxivlens.queryservice.domain.RelationType;
import com.arxivlens.queryservice.domain.ResultStatus;

/**
 * The point of GraphPort: the application can be exercised with no gRPC, no
 * Python service and no Neo4j.
 */
public class FakeGraphPort implements GraphPort {

    private final AtomicInteger queryCalls = new AtomicInteger();

    @Override
    public QueryResult query(GraphQuery query) {
        queryCalls.incrementAndGet();
        return new QueryResult(
                "sLTN extends Logic Tensor Networks.",
                List.of(new Fact("sLTN", RelationType.EXTENDS, "Logic Tensor Networks",
                        List.of("2608.11136"))),
                QueryIntent.LINEAGE,
                List.of("Logic Tensor Networks"),
                List.of(),
                ResultStatus.OK,
                "2026-08-16T04-37-21Z");
    }

    @Override
    public GraphStats graphStats() {
        return new GraphStats("2026-08-16T04-37-21Z", 300, 1742, 1244,
                Map.of("Method", 614), Map.of("EXTENDS", 33));
    }

    public int queryCalls() {
        return queryCalls.get();
    }
}
