package com.arxivlens.queryservice.adapter.in.kafka;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public record GraphUpdatedEvent(
        String event,
        @JsonProperty("snapshot_id") String snapshotId,
        @JsonProperty("occurred_at") String occurredAt,
        Integer papers,
        Integer entities,
        Integer mentions,
        Integer relations) {
}
