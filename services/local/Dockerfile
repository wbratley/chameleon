FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
COPY --from=builder /install /usr/local
COPY src/ /app/src/
# uid/gid 5000 must match Dovecot's userdb static args so both containers
# can read/write the shared maildir volume
RUN groupadd -g 5000 vmail && useradd -u 5000 -g vmail -s /sbin/nologin vmail \
    && mkdir -p /srv/mail/chameleon && chown vmail:vmail /srv/mail/chameleon
WORKDIR /app
# No inbound ports — client connects outbound to the relay WebSocket API
USER vmail
CMD ["python", "-m", "chameleon_local"]
