package com.arxivlens.queryservice.adapter.out.grpc;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import com.arxivlens.graphrag.v1.GraphRagServiceGrpc;
import com.arxivlens.graphrag.v1.GraphStatsRequest;
import com.arxivlens.graphrag.v1.GraphStatsResponse;
import com.arxivlens.graphrag.v1.QueryRequest;
import com.arxivlens.graphrag.v1.QueryResponse;
import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryIntent;
import com.arxivlens.queryservice.domain.QueryResult;
import com.arxivlens.queryservice.domain.RelationType;
import com.arxivlens.queryservice.domain.ResultStatus;

import io.grpc.ManagedChannel;
import io.grpc.Server;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.stub.StreamObserver;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.springframework.grpc.client.ChannelBuilderOptions;
import org.springframework.grpc.client.GrpcChannelFactory;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Runs against a real gRPC server over the in-process transport, so the
 * serialization and both enum mappings are genuinely exercised.
 */
class GraphRagGrpcAdapterTest {

    private static final String SERVER = "adapter-test";

    private final AtomicReference<QueryRequest> received = new AtomicReference<>();
    private final AtomicReference<QueryResponse> reply = new AtomicReference<>();

    private Server server;
    private ManagedChannel channel;
    private GraphRagGrpcAdapter adapter;

    @BeforeEach
    void startServer() throws Exception {
        server = InProcessServerBuilder.forName(SERVER).directExecutor()
                .addService(new GraphRagServiceGrpc.GraphRagServiceImplBase() {
                    @Override
                    public void query(QueryRequest request,
                            StreamObserver<QueryResponse> observer) {
                        received.set(request);
                        observer.onNext(reply.get() == null
                                ? QueryResponse.getDefaultInstance()
                                : reply.get());
                        observer.onCompleted();
                    }

                    @Override
                    public void getGraphStats(GraphStatsRequest request,
                            StreamObserver<GraphStatsResponse> observer) {
                        observer.onNext(GraphStatsResponse.newBuilder()
                                .setSnapshotId("2026-08-16T04-37-21Z")
                                .setPapers(300)
                                .setEntities(1742)
                                .setRelations(1244)
                                .putEntitiesByLabel("Method", 614)
                                .putRelationsByType("EXTENDS", 33)
                                .build());
                        observer.onCompleted();
                    }
                })
                .build()
                .start();

        channel = InProcessChannelBuilder.forName(SERVER).directExecutor().build();
        adapter = new GraphRagGrpcAdapter(new GrpcChannelFactory() {
            @Override
            public boolean supports(String target) {
                return true;
            }

            @Override
            public ManagedChannel createChannel(String target, ChannelBuilderOptions options) {
                return channel;
            }
        });
    }

    @AfterEach
    void stopServer() {
        channel.shutdownNow();
        server.shutdownNow();
    }

    // The regression test for a real bug: a domain enum whose name drifts from
    // the proto fails here rather than in production, and only on that value.
    @ParameterizedTest
    @EnumSource(RelationType.class)
    void everyRelationTypeSurvivesTheRoundTrip(RelationType type) {
        reply.set(QueryResponse.newBuilder()
                .addFacts(com.arxivlens.graphrag.v1.Fact.newBuilder()
                        .setSubject("a")
                        .setPredicate(com.arxivlens.graphrag.v1.RelationType
                                .valueOf("RELATION_TYPE_" + type.name()))
                        .setObject("b")
                        .addPapers("2608.11136"))
                .build());

        QueryResult result = adapter.query(
                new GraphQuery("q", null, List.of("a"), 0));

        assertThat(result.facts()).singleElement().satisfies(fact -> {
            assertThat(fact.predicate()).isEqualTo(type);
            assertThat(fact.papers()).containsExactly("2608.11136");
        });
    }

    @ParameterizedTest
    @EnumSource(QueryIntent.class)
    void everyIntentReachesTheServerPrefixed(QueryIntent intent) {
        reply.set(QueryResponse.getDefaultInstance());

        adapter.query(new GraphQuery("q", intent, List.of("a"), 5));

        assertThat(received.get().getIntent().name())
                .isEqualTo("QUERY_INTENT_" + intent.name());
        assertThat(received.get().getMaxFacts()).isEqualTo(5);
        assertThat(received.get().getEntitiesList()).containsExactly("a");
    }

    @Test
    void mapsStatusAndUnresolvedEntities() {
        reply.set(QueryResponse.newBuilder()
                .setAnswer("Not in the graph.")
                .setStatus(com.arxivlens.graphrag.v1.ResultStatus.RESULT_STATUS_ENTITY_NOT_FOUND)
                .setIntent(com.arxivlens.graphrag.v1.QueryIntent.QUERY_INTENT_WHAT_USES)
                .addUnresolvedEntities("Quantum Blockchain Transformer")
                .build());

        QueryResult result = adapter.query(new GraphQuery("q", null, List.of("x"), 0));

        assertThat(result.status()).isEqualTo(ResultStatus.ENTITY_NOT_FOUND);
        assertThat(result.intent()).isEqualTo(QueryIntent.WHAT_USES);
        assertThat(result.unresolvedEntities()).containsExactly("Quantum Blockchain Transformer");
        assertThat(result.facts()).isEmpty();
    }

    // UNSPECIFIED means "the field was never set" and has no domain equivalent.
    @Test
    void mapsUnspecifiedToNullRatherThanInventingAValue() {
        reply.set(QueryResponse.getDefaultInstance());

        QueryResult result = adapter.query(new GraphQuery("q", null, List.of("a"), 0));

        assertThat(result.intent()).isNull();
        assertThat(result.status()).isNull();
    }

    @Test
    void translatesGraphStats() {
        GraphStats stats = adapter.graphStats();

        assertThat(stats.papers()).isEqualTo(300);
        assertThat(stats.entitiesByLabel()).containsEntry("Method", 614);
        assertThat(stats.relationsByType()).containsEntry("EXTENDS", 33);
    }
}
