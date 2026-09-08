package com.arxivlens.queryservice.application;

import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryResult;

import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;

@Service
public class QueryUseCase {

    private final GraphPort graph;

    QueryUseCase(GraphPort graph) {
        this.graph = graph;
    }

    @Cacheable("queries")
    public QueryResult ask(GraphQuery query) {
        return graph.query(query);
    }

    @Cacheable("stats")
    public GraphStats stats() {
        return graph.graphStats();
    }
}
