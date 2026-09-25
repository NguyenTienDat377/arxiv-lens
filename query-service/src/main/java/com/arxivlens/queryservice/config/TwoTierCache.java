package com.arxivlens.queryservice.config;

import java.util.concurrent.Callable;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.Cache;


public class TwoTierCache implements Cache {

    private final Cache l1;
    private final Cache l2;

    private static final Logger log = LoggerFactory.getLogger(TwoTierCache.class);

    public TwoTierCache(Cache l1, Cache l2) {
        this.l1 = l1;
        this.l2 = l2;
    }
    @Override 
    public String getName() {
        return l1.getName();
    }
    @Override 
    public Object getNativeCache() {
        return this;
    }
    @Override
    public ValueWrapper get(Object key) {
        ValueWrapper hit = l1.get(key);
        if (hit != null) return hit;
        try {
            hit = l2.get(key);
        } catch (RuntimeException error) {
            log.warn("L2 get failed for cache {}; treating as a miss: {}", getName(), error.getMessage());
            return null;
        }
        if (hit != null) l1.put(key, hit.get());
        return hit;
    }
    @Override 
    @SuppressWarnings("unchecked")
    public <T> T get (Object key, Class <T> type) {
        ValueWrapper hit = get(key);
        if (hit == null) {
            return null;
        }
        Object value = hit.get();
        if (value != null && type != null && !type.isInstance(value)) {
            throw new IllegalStateException("cached value is not of type " + type.getName());
        }
        return (T) value;
    }
    @Override 
    @SuppressWarnings("unchecked")
    public <T> T get (Object key, Callable<T> valueLoader) {
        ValueWrapper hit = get(key);
        if (hit != null) return (T) hit.get();
        T value;
        try {
            value = valueLoader.call();
        } catch (Exception error) {
            throw new ValueRetrievalException(key, valueLoader, error);
        }
        put(key, value);
        return value;
    }

    @Override  
    public void put(Object key, Object value) {
        onL2("put", () -> l2.put(key, value));
        l1.put(key, value);
    }

    // L2 uses the immediate variants: RedisCache.evict()/clear() may run
    // asynchronously, and an L1 miss in that window re-promotes the stale value.
    @Override
    public void evict(Object key) {
        onL2("evict", () -> l2.evictIfPresent(key));
        l1.evict(key);
    }

    @Override
    public void clear() {
        onL2("clear", () -> l2.invalidate());
        l1.clear();
    }

    private void onL2(String operation, Runnable action) {
        try {
            action.run();
        } catch (RuntimeException error) {
            log.warn("L2 {} failed for cache {}: {}", operation, getName(), error.getMessage());
        }
    }
}
