FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
COPY --from=builder /install /usr/local
COPY src/ /app/src/
WORKDIR /app
EXPOSE 1025
USER nobody
CMD ["python", "-m", "chameleon_relay"]
