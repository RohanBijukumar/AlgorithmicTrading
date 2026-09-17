ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-hosted.txt ./
RUN pip install --require-hashes -r requirements-hosted.txt
COPY pyproject.toml README.md ./
COPY algotrading ./algotrading
RUN pip install --no-deps . && \
    useradd --uid 10001 --create-home researcher && \
    mkdir -p /var/lib/algotrading && \
    chown 10001:10001 /var/lib/algotrading && chmod 700 /var/lib/algotrading
USER 10001:10001
CMD ["uvicorn", "algotrading.hosted:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--no-server-header", "--limit-concurrency", "32", "--timeout-keep-alive", "5"]
