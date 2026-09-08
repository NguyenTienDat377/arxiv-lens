package com.arxivlens.queryservice.config;

import java.time.Duration;

import com.github.benmanes.caffeine.cache.Caffeine;

import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cache.caffeine.CaffeineCacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableCaching
public class CacheConfig {

    public static final String QUERIES = "queries";
    public static final String STATS = "stats";

    @Bean
    CaffeineCacheManager cacheManager() {
        CaffeineCacheManager manager = new CaffeineCacheManager(QUERIES, STATS);
        manager.setCaffeine(
                Caffeine.newBuilder()
                        .maximumSize(500)
                        .expireAfterWrite(Duration.ofHours(6)));
        return manager;
    }
}
