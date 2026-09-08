package com.arxivlens.queryservice.adapter.in.kafka;

import com.arxivlens.queryservice.config.CacheConfig;
import tools.jackson.databind.ObjectMapper;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.Cache;
import org.springframework.cache.CacheManager;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class GraphUpdatedListener {

    public static final String TOPIC = "graph.updated";

    private static final Logger log = LoggerFactory.getLogger(GraphUpdatedListener.class);

    private final CacheManager caches;
    private final ObjectMapper json;

    GraphUpdatedListener(CacheManager caches, ObjectMapper json) {
        this.caches = caches;
        this.json = json;
    }

    @KafkaListener(topics = TOPIC, groupId = "${spring.kafka.consumer.group-id}")
    public void onGraphUpdated(String message) {
        GraphUpdatedEvent event = parse(message);
        evict();

        if (event == null) {
            log.warn("graph.updated was unreadable; evicted caches anyway");
        } else {
            log.info("graph.updated snapshot={} entities=+{} relations=+{}; caches evicted",
                    event.snapshotId(), event.entities(), event.relations());
        }
    }

    private GraphUpdatedEvent parse(String message) {
        try {
            return json.readValue(message, GraphUpdatedEvent.class);
        } catch (Exception error) {
            // Swallowed on purpose: rethrowing would make the broker redeliver a
            // message that can never parse, and the eviction does not need it.
            log.warn("cannot parse graph.updated: {}", error.getMessage());
            return null;
        }
    }

    private void evict() {
        for (String name : new String[] { CacheConfig.QUERIES, CacheConfig.STATS }) {
            Cache cache = caches.getCache(name);
            if (cache != null) {
                cache.clear();
            }
        }
    }
}
