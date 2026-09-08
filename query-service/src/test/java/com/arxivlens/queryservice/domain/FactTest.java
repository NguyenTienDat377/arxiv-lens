package com.arxivlens.queryservice.domain;

import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class FactTest {

    @Test
    void copiesTheCallersListSoLaterMutationCannotLeakIn() {
        List<String> papers = new ArrayList<>(List.of("2608.11136"));
        Fact fact = new Fact("sLTN", RelationType.EXTENDS, "Logic Tensor Networks", papers);

        papers.add("9999.99999");

        assertThat(fact.papers()).containsExactly("2608.11136");
    }

    @Test
    void treatsNullPapersAsEmpty() {
        Fact fact = new Fact("sLTN", RelationType.EXTENDS, "Logic Tensor Networks", null);

        assertThat(fact.papers()).isEmpty();
    }

    @Test
    void refusesMutationOfTheStoredList() {
        Fact fact = new Fact("sLTN", RelationType.EXTENDS, "LTN", List.of("2608.11136"));

        assertThatThrownBy(() -> fact.papers().add("x"))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void comparesByValue() {
        Fact one = new Fact("sLTN", RelationType.EXTENDS, "LTN", List.of("2608.11136"));
        Fact two = new Fact("sLTN", RelationType.EXTENDS, "LTN", List.of("2608.11136"));

        assertThat(one).isEqualTo(two).hasSameHashCodeAs(two);
    }
}
