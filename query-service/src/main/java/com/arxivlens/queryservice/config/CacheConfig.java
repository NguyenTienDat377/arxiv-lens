package com.arxivlens.queryservice.config;

import java.time.Duration;
import java.util.List;

import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryResult;
import com.github.benmanes.caffeine.cache.Caffeine;

import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cache.caffeine.CaffeineCacheManager;
import org.springframework.cache.support.SimpleCacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.cache.RedisCacheConfiguration;
import org.springframework.data.redis.cache.RedisCacheManager;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.serializer.JacksonJsonRedisSerializer;
import org.springframework.data.redis.serializer.RedisSerializationContext;

@Configuration
@EnableCaching
public class CacheConfig {

    public static final String QUERIES = "queries";
    public static final String STATS = "stats";

    @Bean
    CacheManager cacheManager(RedisConnectionFactory connectionFactory) {
        CaffeineCacheManager l1 = new CaffeineCacheManager(QUERIES, STATS);
        l1.setCaffeine(
                Caffeine.newBuilder()
                        .maximumSize(500)
                        .recordStats()
                        .expireAfterWrite(Duration.ofMinutes(10)));

        RedisCacheManager l2 = RedisCacheManager.builder(connectionFactory)
                .withCacheConfiguration(QUERIES, redisCache(QueryResult.class))
                .withCacheConfiguration(STATS, redisCache(GraphStats.class))
                .build();
        // Not a bean, so Spring never calls afterPropertiesSet(); without this,
        // getCache() builds each cache from the defaults and ignores the config above.
        l2.initializeCaches();

        SimpleCacheManager manager = new SimpleCacheManager();
        manager.setCaches(List.of(
                new TwoTierCache(l1.getCache(QUERIES), l2.getCache(QUERIES)),
                new TwoTierCache(l1.getCache(STATS), l2.getCache(STATS))));
        return manager;
    }

    private static RedisCacheConfiguration redisCache(Class<?> type) {
        return RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(Duration.ofHours(6))
                .serializeValuesWith(RedisSerializationContext.SerializationPair
                        .fromSerializer(new JacksonJsonRedisSerializer<>(type)));
    }
}
