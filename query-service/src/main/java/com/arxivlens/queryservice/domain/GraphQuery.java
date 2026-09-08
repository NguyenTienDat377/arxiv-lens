package com.arxivlens.queryservice.domain;

import java.util.List;

public record GraphQuery(String question, QueryIntent intent, List<String> entities, int maxFacts) {
    public GraphQuery {
        entities = entities == null ? List.of() : List.copyOf(entities);
    }
    
}
