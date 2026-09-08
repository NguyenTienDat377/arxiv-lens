package com.arxivlens.queryservice.domain;

import java.util.List;

public record Fact(String subject, RelationType predicate, String object, List<String> papers) {
    public Fact {
        papers = papers == null ? List.of() : List.copyOf(papers);
    }
}
