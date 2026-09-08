package com.arxivlens.queryservice.application;

import java.util.List;

import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.QueryIntent;
import com.arxivlens.queryservice.domain.QueryResult;
import com.arxivlens.queryservice.domain.RelationType;
import com.arxivlens.queryservice.domain.ResultStatus;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class QueryUseCaseTest {

    private final FakeGraphPort graph = new FakeGraphPort();
    private final QueryUseCase useCase = new QueryUseCase(graph);

    @Test
    void passesTheQueryThroughAndReturnsWhatThePortGives() {
        QueryResult result = useCase.ask(
                new GraphQuery("What builds on LTN?", QueryIntent.LINEAGE, List.of("LTN"), 0));

        assertThat(result.status()).isEqualTo(ResultStatus.OK);
        assertThat(result.intent()).isEqualTo(QueryIntent.LINEAGE);
        assertThat(result.facts()).singleElement().satisfies(fact -> {
            assertThat(fact.subject()).isEqualTo("sLTN");
            assertThat(fact.predicate()).isEqualTo(RelationType.EXTENDS);
            assertThat(fact.papers()).containsExactly("2608.11136");
        });
    }

    @Test
    void needsNoSpringContextAtAll() {
        assertThat(graph.queryCalls()).isZero();
        useCase.stats();
        assertThat(useCase.stats().papers()).isEqualTo(300);
    }
}
