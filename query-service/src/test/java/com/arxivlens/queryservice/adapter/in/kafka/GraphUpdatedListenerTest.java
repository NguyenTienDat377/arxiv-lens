package com.arxivlens.queryservice.adapter.in.kafka;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import com.arxivlens.queryservice.application.FakeGraphPort;
import com.arxivlens.queryservice.application.GraphPort;
import com.arxivlens.queryservice.application.QueryUseCase;
import com.arxivlens.queryservice.config.CacheConfig;
import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.QueryIntent;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.cache.Cache;
import org.springframework.cache.CacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.kafka.config.KafkaListenerEndpointRegistry;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.MessageListenerContainer;
import org.springframework.kafka.test.context.EmbeddedKafka;
import org.springframework.kafka.test.utils.ContainerTestUtils;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(properties = {
        "spring.kafka.bootstrap-servers=${spring.embedded.kafka.brokers}",
        "spring.kafka.consumer.group-id=graph-updated-test",
        "spring.kafka.consumer.auto-offset-reset=earliest",
        "spring.kafka.listener.auto-startup=true",
        "spring.kafka.producer.key-serializer=org.apache.kafka.common.serialization.StringSerializer",
        "spring.kafka.producer.value-serializer=org.apache.kafka.common.serialization.StringSerializer",
})
@EmbeddedKafka(partitions = 1, topics = GraphUpdatedListener.TOPIC)
@Import(GraphUpdatedListenerTest.FakePortConfig.class)
class GraphUpdatedListenerTest {

    static class FakePortConfig {
        @Bean
        @Primary
        GraphPort graphPort() {
            return new FakeGraphPort();
        }
    }

    private static final GraphQuery QUERY =
            new GraphQuery("what extends LTN?", QueryIntent.LINEAGE, List.of("LTN"), 0);

    @Autowired
    QueryUseCase useCase;

    @Autowired
    CacheManager caches;

    @Autowired
    KafkaTemplate<String, String> kafka;

    @Autowired
    KafkaListenerEndpointRegistry registry;

    @BeforeEach
    void waitForTheConsumerToJoin() {
        for (MessageListenerContainer container : registry.getListenerContainers()) {
            ContainerTestUtils.waitForAssignment(container, 1);
        }
    }

    @Test
    void anEventEvictsACachedAnswer() {
        useCase.ask(QUERY);
        assertThat(cached()).isNotNull();

        publish("""
                {"event": "graph.updated", "snapshot_id": "2026-08-16T04-37-21Z",
                 "papers": 300, "entities": 1742, "mentions": 4210, "relations": 2117}
                """);

        awaitEviction();
    }

    @Test
    void anEventEvictsCachedStats() {
        useCase.stats();
        assertThat(caches.getCache(CacheConfig.STATS).get(org.springframework.cache.interceptor.SimpleKey.EMPTY))
                .isNotNull();

        publish("""
                {"event": "graph.updated", "snapshot_id": "s1", "entities": 1}
                """);

        await(() -> caches.getCache(CacheConfig.STATS)
                .get(org.springframework.cache.interceptor.SimpleKey.EMPTY) == null);
    }

    @Test
    void anUnreadableMessageStillEvictsAndDoesNotKillTheConsumer() {
        useCase.ask(QUERY);
        assertThat(cached()).isNotNull();

        publish("this is not json");

        awaitEviction();

        // The container survived the poison message, so a later event still lands.
        useCase.ask(QUERY);
        assertThat(cached()).isNotNull();
        publish("""
                {"event": "graph.updated", "snapshot_id": "s2"}
                """);
        awaitEviction();
    }

    private void publish(String payload) {
        kafka.send(GraphUpdatedListener.TOPIC, "key", payload);
    }

    private Cache.ValueWrapper cached() {
        return caches.getCache(CacheConfig.QUERIES).get(QUERY);
    }

    private void awaitEviction() {
        await(() -> cached() == null);
    }

    private void await(java.util.function.BooleanSupplier condition) {
        Instant deadline = Instant.now().plus(Duration.ofSeconds(10));
        while (Instant.now().isBefore(deadline)) {
            if (condition.getAsBoolean()) {
                return;
            }
            Thread.onSpinWait();
        }
        throw new AssertionError("condition was not met within 10s");
    }
}
