package com.arxivlens.queryservice.config;

import com.arxivlens.queryservice.QueryServiceApplication;
import com.arxivlens.queryservice.application.FakeGraphPort;
import com.arxivlens.queryservice.application.QueryUseCase;

import org.junit.jupiter.api.Test;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers
class RedisRestartTest {

    @Container
    static GenericContainer<?> redis =
            new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Test
    void aSecondAppInstanceIsServedFromRedisNotTheGraph() {
        try (ConfigurableApplicationContext first = start()) {
            first.getBean(QueryUseCase.class).stats();
            assertThat(first.getBean(FakeGraphPort.class).statsCalls()).isEqualTo(1);
        }

        try (ConfigurableApplicationContext second = start()) {
            second.getBean(QueryUseCase.class).stats();
            assertThat(second.getBean(FakeGraphPort.class).statsCalls()).isZero();
        }
    }

    private ConfigurableApplicationContext start() {
        // Arguments, not .properties(): those are defaults and lose to application.yml.
        return new SpringApplicationBuilder(QueryServiceApplication.class, FakePortConfig.class)
                .run(
                        "--server.port=0",
                        "--spring.kafka.listener.auto-startup=false",
                        "--spring.data.redis.url=redis://" + redis.getHost() + ":" + redis.getMappedPort(6379));
    }

    static class FakePortConfig {
        @Bean
        @Primary
        FakeGraphPort graphPort() {
            return new FakeGraphPort();
        }
    }
}

