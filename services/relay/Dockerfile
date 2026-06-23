FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
COPY --from=builder /install /usr/local
COPY src/ /app/src/
RUN groupadd -g 1000 chameleon && useradd -u 1000 -g chameleon -s /sbin/nologin chameleon \
    && mkdir -p /data && chown chameleon:chameleon /data
WORKDIR /app
# 1025: SMTP listener (map to port 25 on host via iptables or network_mode: host)
# 8080: WebSocket API (proxied by nginx with TLS termination)
EXPOSE 1025 8080
USER chameleon
CMD ["python", "-m", "chameleon_relay"]
