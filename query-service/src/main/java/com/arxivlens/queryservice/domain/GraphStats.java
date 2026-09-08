package com.arxivlens.queryservice.domain;

import java.util.Map;

public record GraphStats(String snapshotId, int papers, int entities, int relations,
                  Map<String,Integer> entitiesByLabel, Map<String,Integer> relationsByType) {
    public GraphStats {
        entitiesByLabel = entitiesByLabel == null ? Map.of() : Map.copyOf(entitiesByLabel);
        relationsByType = relationsByType == null ? Map.of() : Map.copyOf(relationsByType);
    }
    
}
