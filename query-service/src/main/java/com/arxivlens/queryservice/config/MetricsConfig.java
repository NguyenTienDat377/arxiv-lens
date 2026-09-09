package com.arxivlens.queryservice.config;

import io.micrometer.core.instrument.Meter;
import io.micrometer.core.instrument.config.MeterFilter;
import io.micrometer.core.instrument.distribution.DistributionStatisticConfig;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MetricsConfig {

    /**
     * Publish latency histograms for HTTP requests.
     *
     * <p>Micrometer exports a timer as count/sum/max, and no percentile can be
     * derived from those three numbers. Prometheus needs the bucketed
     * {@code _bucket} series that {@code histogram_quantile()} reads.
     *
     * <p>Spring Boot 3 configured this with
     * {@code management.metrics.distribution.percentiles-histogram}. Boot 4
     * removed those properties, so it is a MeterFilter now.
     */
    @Bean
    MeterFilter httpLatencyHistogram() {
        return new MeterFilter() {
            @Override
            public DistributionStatisticConfig configure(
                    Meter.Id id, DistributionStatisticConfig config) {
                if (id.getName().startsWith("http.server.requests")) {
                    return DistributionStatisticConfig.builder()
                            .percentilesHistogram(true)
                            .build()
                            .merge(config);
                }
                return config;
            }
        };
    }
}
