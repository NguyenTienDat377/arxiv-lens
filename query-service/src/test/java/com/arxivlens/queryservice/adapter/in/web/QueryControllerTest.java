package com.arxivlens.queryservice.adapter.in.web;

import java.util.List;
import java.util.Map;

import com.arxivlens.queryservice.application.QueryUseCase;
import com.arxivlens.queryservice.domain.Fact;
import com.arxivlens.queryservice.domain.GraphStats;
import com.arxivlens.queryservice.domain.QueryIntent;
import com.arxivlens.queryservice.domain.QueryResult;
import com.arxivlens.queryservice.domain.RelationType;
import com.arxivlens.queryservice.domain.ResultStatus;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(QueryController.class)
class QueryControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    QueryUseCase queries;

    @Test
    void returnsFactsAndCitations() throws Exception {
        given(queries.ask(any())).willReturn(new QueryResult(
                "sLTN extends LTN.",
                List.of(new Fact("sLTN", RelationType.EXTENDS, "Logic Tensor Networks",
                        List.of("2608.11136"))),
                QueryIntent.LINEAGE, List.of("Logic Tensor Networks"), List.of(),
                ResultStatus.OK, "2026-08-16T04-37-21Z"));

        mvc.perform(post("/api/query").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"What builds on LTN?\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("OK"))
                .andExpect(jsonPath("$.facts[0].predicate").value("EXTENDS"))
                .andExpect(jsonPath("$.facts[0].papers[0]").value("2608.11136"));
    }

    // "Nothing is connected to that" is an answer, not a failure, so it is 200.
    @Test
    void returnsOkWithAStatusWhenTheEntityIsUnknown() throws Exception {
        given(queries.ask(any())).willReturn(new QueryResult(
                "Not in the graph.", List.of(), QueryIntent.WHAT_USES,
                List.of(), List.of("Quantum Blockchain Transformer"),
                ResultStatus.ENTITY_NOT_FOUND, "2026-08-16T04-37-21Z"));

        mvc.perform(post("/api/query").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"What uses the Quantum Blockchain Transformer?\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ENTITY_NOT_FOUND"))
                .andExpect(jsonPath("$.unresolvedEntities[0]").value("Quantum Blockchain Transformer"))
                .andExpect(jsonPath("$.facts").isEmpty());
    }

    @Test
    void rejectsABlankQuestion() throws Exception {
        mvc.perform(post("/api/query").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"   \"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void exposesGraphStats() throws Exception {
        given(queries.stats()).willReturn(new GraphStats(
                "2026-08-16T04-37-21Z", 300, 1742, 1244,
                Map.of("Method", 614), Map.of("EXTENDS", 33)));

        mvc.perform(get("/api/stats"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.papers").value(300))
                .andExpect(jsonPath("$.entitiesByLabel.Method").value(614));
    }
}
