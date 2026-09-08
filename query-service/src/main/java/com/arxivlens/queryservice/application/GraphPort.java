package com.arxivlens.queryservice.application;

import com.arxivlens.queryservice.domain.GraphQuery;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryResult;

public interface GraphPort {
    QueryResult query(GraphQuery query);
    GraphStats graphStats();
}
