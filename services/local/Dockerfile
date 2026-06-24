FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
COPY --from=builder /install /usr/local
# uid/gid 5000 must match Dovecot's userdb static args so both containers
# can read/write the shared maildir volume
RUN groupadd -g 5000 vmail && useradd -u 5000 -g vmail -s /sbin/nologin vmail \
    && mkdir -p /srv/mail/chameleon /srv/data \
    && chown vmail:vmail /srv/mail/chameleon /srv/data
EXPOSE 8080
USER vmail
CMD ["python", "-m", "chameleon_local"]
