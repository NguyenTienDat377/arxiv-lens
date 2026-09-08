package com.arxivlens.queryservice.adapter.in.web;

import com.arxivlens.queryservice.application.QueryUseCase;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryResult;

import jakarta.validation.Valid;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class QueryController {

    private final QueryUseCase queries;

    QueryController(QueryUseCase queries) {
        this.queries = queries;
    }

    // 200 even when nothing was found: the query succeeded, and "not in the
    // graph" is an answer. The status field carries the distinction.
    @PostMapping("/query")
    public QueryResult query(@Valid @RequestBody AskRequest request) {
        return queries.ask(request.toDomain());
    }

    @GetMapping("/stats")
    public GraphStats stats() {
        return queries.stats();
    }
}
