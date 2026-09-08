package com.arxivlens.queryservice.adapter.in.web;

import io.grpc.StatusRuntimeException;

import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
class GrpcExceptionHandler {

    @ExceptionHandler(StatusRuntimeException.class)
    ProblemDetail unavailable(StatusRuntimeException exception) {
        ProblemDetail detail = ProblemDetail.forStatus(HttpStatus.SERVICE_UNAVAILABLE);
        detail.setTitle("Graph service unavailable");
        detail.setDetail(exception.getStatus().getCode().name());
        return detail;
    }
}
