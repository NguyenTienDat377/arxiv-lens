package com.arxivlens.queryservice.config;

import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;
import org.springframework.cache.Cache;
import org.springframework.cache.concurrent.ConcurrentMapCache;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class TwoTierCacheTest {
    private final ConcurrentMapCache l1 = new ConcurrentMapCache("queries");
    private final ConcurrentMapCache l2 = new ConcurrentMapCache("queries");
    private final TwoTierCache cache = new TwoTierCache(l1, l2);

    @Test
    void anL1HitNeverConsultsL2() {
        l1.put("k", "from-l1");
        l2.put("k", "from-l2");

        assertThat(cache.get("k").get()).isEqualTo("from-l1");
    }

    @Test
    void anL2HitIsReturnedAndPromotedIntoL1() {
        l2.put("k", "from-l2");

        assertThat(cache.get("k").get()).isEqualTo("from-l2");
        assertThat(l1.get("k")).isNotNull();
        assertThat(l1.get("k").get()).isEqualTo("from-l2");
    }

    @Test
    void aMissInBothTiersIsNull() {
        assertThat(cache.get("k")).isNull();
    }

    @Test
    void putWritesBothTiers() {
        cache.put("k", "v");

        assertThat(l1.get("k").get()).isEqualTo("v");
        assertThat(l2.get("k").get()).isEqualTo("v");
    }

    @Test
    void clearEmptiesBothTiers() {
        cache.put("k", "v");

        cache.clear();

        assertThat(l1.get("k")).isNull();
        assertThat(l2.get("k")).isNull();
    }

    @Test
    void evictRemovesTheKeyFromBothTiers() {
        cache.put("k", "v");

        cache.evict("k");

        assertThat(l1.get("k")).isNull();
        assertThat(l2.get("k")).isNull();
    }

    @Test
    void theValueLoaderRunsOnceAndFillsBothTiers() {
        AtomicInteger calls = new AtomicInteger();

        String first = cache.get("k", () -> {
            calls.incrementAndGet();
            return "loaded";
        });
        String second = cache.get("k", () -> {
            calls.incrementAndGet();
            return "loaded";
        });

        assertThat(first).isEqualTo("loaded");
        assertThat(second).isEqualTo("loaded");
        assertThat(calls.get()).isEqualTo(1);
        assertThat(l2.get("k").get()).isEqualTo("loaded");
    }

    // A failing loader is the one error that must not be swallowed: the gRPC
    // call failed, not the cache.
    @Test
    void aFailingValueLoaderPropagates() {
        assertThatThrownBy(() -> cache.get("k", () -> {
            throw new IllegalStateException("graph is down");
        })).isInstanceOf(Cache.ValueRetrievalException.class);
    }

    @Test
    void typedGetReturnsTheValueAndRejectsTheWrongType() {
        cache.put("k", "v");

        assertThat(cache.get("k", String.class)).isEqualTo("v");
        assertThatThrownBy(() -> cache.get("k", Integer.class))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void aBrokenL2DegradesToL1Only() {
        TwoTierCache degraded = new TwoTierCache(l1, new BrokenCache());

        assertThatCode(() -> degraded.put("k", "v")).doesNotThrowAnyException();
        assertThat(degraded.get("k").get()).isEqualTo("v");
    }

    @Test
    void aBrokenL2OnAnL1MissReadsAsAMiss() {
        TwoTierCache degraded = new TwoTierCache(l1, new BrokenCache());

        assertThat(degraded.get("absent")).isNull();
    }

    @Test
    void aBrokenL2StillEvictsAndClearsL1() {
        TwoTierCache degraded = new TwoTierCache(l1, new BrokenCache());
        degraded.put("k", "v");
        degraded.put("j", "w");

        degraded.evict("k");
        assertThat(l1.get("k")).isNull();

        degraded.clear();
        assertThat(l1.get("j")).isNull();
    }

    /** Stands in for Redis being unreachable: every operation throws. */
    private static final class BrokenCache implements Cache {

        @Override
        public String getName() {
            return "queries";
        }

        @Override
        public Object getNativeCache() {
            return this;
        }

        @Override
        public ValueWrapper get(Object key) {
            throw new IllegalStateException("redis is down");
        }

        @Override
        public <T> T get(Object key, Class<T> type) {
            throw new IllegalStateException("redis is down");
        }

        @Override
        public <T> T get(Object key, java.util.concurrent.Callable<T> valueLoader) {
            throw new IllegalStateException("redis is down");
        }

        @Override
        public void put(Object key, Object value) {
            throw new IllegalStateException("redis is down");
        }

        @Override
        public void evict(Object key) {
            throw new IllegalStateException("redis is down");
        }

        @Override
        public void clear() {
            throw new IllegalStateException("redis is down");
        }
    }
}
