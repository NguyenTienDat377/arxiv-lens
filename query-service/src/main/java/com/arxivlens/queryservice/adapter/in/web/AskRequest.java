package com.arxivlens.queryservice.adapter.in.web;

import java.util.List;

import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.QueryIntent;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.PositiveOrZero;

// maxFacts is boxed because an absent JSON field cannot be mapped onto a
// primitive in a record: there is no default to fall back to.
public record AskRequest(
        @NotBlank String question,
        QueryIntent intent,
        List<String> entities,
        @PositiveOrZero Integer maxFacts) {

    public GraphQuery toDomain() {
        return new GraphQuery(question, intent, entities, maxFacts == null ? 0 : maxFacts);
    }
}
