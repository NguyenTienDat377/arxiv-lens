package com.arxivlens.queryservice.domain;

import java.util.List;

public record QueryResult(String answer, List<Fact> facts, QueryIntent intent,
                   List<String> linkedEntities, List<String> unresolvedEntities,
                   ResultStatus status, String snapshotId) {
    public QueryResult {
        facts = facts == null ? List.of() : List.copyOf(facts);
        linkedEntities = linkedEntities == null ? List.of() : List.copyOf(linkedEntities);
        unresolvedEntities = unresolvedEntities == null ? List.of() : List.copyOf(unresolvedEntities);
    }
    
}
