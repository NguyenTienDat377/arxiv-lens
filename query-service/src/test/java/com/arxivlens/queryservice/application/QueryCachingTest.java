package com.arxivlens.queryservice.application;

import java.util.List;

import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.QueryIntent;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
@Import(QueryCachingTest.FakePortConfig.class)
class QueryCachingTest {

    static class FakePortConfig {
        // The real adapter is a @Component too, so the fake has to win.
        @Bean
        @Primary
        FakeGraphPort graphPort() {
            return new FakeGraphPort();
        }
    }

    @Autowired
    QueryUseCase useCase;

    @Autowired
    FakeGraphPort graph;

    // The Spring context, and so the cache and the counter, are shared across
    // methods, so each test measures a delta rather than an absolute count.
    @Test
    void repeatingTheSameQuestionDoesNotHitTheGraphTwice() {
        GraphQuery query = new GraphQuery(
                "repeated question", QueryIntent.LINEAGE, List.of("LTN"), 0);
        int before = graph.queryCalls();

        useCase.ask(query);
        useCase.ask(query);
        useCase.ask(query);

        assertThat(graph.queryCalls() - before).isEqualTo(1);
    }

    @Test
    void aDifferentQuestionIsADifferentCacheKey() {
        int before = graph.queryCalls();

        useCase.ask(new GraphQuery("first", QueryIntent.LINEAGE, List.of("LTN"), 0));
        useCase.ask(new GraphQuery("second", QueryIntent.LINEAGE, List.of("LTN"), 0));

        assertThat(graph.queryCalls() - before).isEqualTo(2);
    }
}
