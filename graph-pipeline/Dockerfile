FROM python:3.11-slim AS builder 
ENV HF_HOME=/opt/hf 
WORKDIR /build 
RUN python -m venv /opt/venv 
ENV PATH="/opt/venv/bin:$PATH"

COPY graph-pipeline/requirements.txt .
# CPU torch first, from its own index. The default PyPI wheel for Linux carries
# the CUDA runtime — about 3 GB of GPU libraries no container here can use. Its
# own layer, so editing requirements.txt does not re-download it.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/ proto/
RUN mkdir -p generated && python -m grpc_tools.protoc -I proto \
      --python_out=generated --grpc_python_out=generated proto/graphrag.proto

RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

FROM python:3.11-slim 
ENV HF_HOME=/opt/hf PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf /opt/hf
COPY --from=builder /build/generated ./generated
COPY graph-pipeline/pipeline ./pipeline
COPY graph-pipeline/retrieval ./retrieval
COPY graph-pipeline/eval ./eval

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 50051
CMD ["python", "-m", "retrieval.grpc_server", "--port", "50051"]
