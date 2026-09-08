package com.arxivlens.queryservice.adapter.out.grpc;

import java.util.List;

import com.arxivlens.graphrag.v1.GraphRagServiceGrpc;
import com.arxivlens.graphrag.v1.GraphStatsRequest;
import com.arxivlens.graphrag.v1.GraphStatsResponse;
import com.arxivlens.graphrag.v1.QueryRequest;
import com.arxivlens.graphrag.v1.QueryResponse;
import com.arxivlens.queryservice.application.GraphPort;
import com.arxivlens.queryservice.domain.Fact;
import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryIntent;
import com.arxivlens.queryservice.domain.QueryResult;
import com.arxivlens.queryservice.domain.RelationType;
import com.arxivlens.queryservice.domain.ResultStatus;

import org.springframework.grpc.client.GrpcChannelFactory;
import org.springframework.stereotype.Component;

@Component
public class GraphRagGrpcAdapter implements GraphPort {

    private static final String CHANNEL = "graphrag";
    private static final String INTENT_PREFIX = "QUERY_INTENT_";
    private static final String RELATION_PREFIX = "RELATION_TYPE_";
    private static final String STATUS_PREFIX = "RESULT_STATUS_";

    private final GraphRagServiceGrpc.GraphRagServiceBlockingStub stub;

    GraphRagGrpcAdapter(GrpcChannelFactory channels) {
        this.stub = GraphRagServiceGrpc.newBlockingStub(channels.createChannel(CHANNEL));
    }

    @Override
    public QueryResult query(GraphQuery query) {
        return toDomain(stub.query(toProto(query)));
    }

    @Override
    public GraphStats graphStats() {
        GraphStatsResponse response = stub.getGraphStats(GraphStatsRequest.getDefaultInstance());
        return new GraphStats(
                response.getSnapshotId(),
                response.getPapers(),
                response.getEntities(),
                response.getRelations(),
                response.getEntitiesByLabelMap(),
                response.getRelationsByTypeMap());
    }

    private QueryRequest toProto(GraphQuery query) {
        QueryRequest.Builder builder = QueryRequest.newBuilder()
                .setQuestion(query.question())
                .addAllEntities(query.entities())
                .setMaxFacts(query.maxFacts());
        if (query.intent() != null) {
            builder.setIntent(
                    com.arxivlens.graphrag.v1.QueryIntent.valueOf(INTENT_PREFIX + query.intent().name()));
        }
        return builder.build();
    }

    private QueryResult toDomain(QueryResponse response) {
        List<Fact> facts = response.getFactsList().stream()
                .map(fact -> new Fact(
                        fact.getSubject(),
                        relation(fact.getPredicate()),
                        fact.getObject(),
                        fact.getPapersList()))
                .toList();

        return new QueryResult(
                response.getAnswer(),
                facts,
                intent(response.getIntent()),
                response.getLinkedEntitiesList(),
                response.getUnresolvedEntitiesList(),
                status(response.getStatus()),
                response.getSnapshotId());
    }

    // Mapped by name, never by ordinal: reordering either enum then fails at the
    // mistake instead of silently returning the wrong constant.
    private QueryIntent intent(com.arxivlens.graphrag.v1.QueryIntent value) {
        return value == com.arxivlens.graphrag.v1.QueryIntent.QUERY_INTENT_UNSPECIFIED
                || value == com.arxivlens.graphrag.v1.QueryIntent.UNRECOGNIZED
                        ? null
                        : QueryIntent.valueOf(strip(value.name(), INTENT_PREFIX));
    }

    private RelationType relation(com.arxivlens.graphrag.v1.RelationType value) {
        return value == com.arxivlens.graphrag.v1.RelationType.RELATION_TYPE_UNSPECIFIED
                || value == com.arxivlens.graphrag.v1.RelationType.UNRECOGNIZED
                        ? null
                        : RelationType.valueOf(strip(value.name(), RELATION_PREFIX));
    }

    private ResultStatus status(com.arxivlens.graphrag.v1.ResultStatus value) {
        return value == com.arxivlens.graphrag.v1.ResultStatus.RESULT_STATUS_UNSPECIFIED
                || value == com.arxivlens.graphrag.v1.ResultStatus.UNRECOGNIZED
                        ? null
                        : ResultStatus.valueOf(strip(value.name(), STATUS_PREFIX));
    }

    private String strip(String name, String prefix) {
        return name.substring(prefix.length());
    }
}
