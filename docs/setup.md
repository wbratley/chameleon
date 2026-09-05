# Chameleon — Setup Guide

Step-by-step deployment of the two halves of Chameleon plus a mail client:

- **Part A — the relay** on a public VPS: receives SMTP, encrypts to your public key, queues until picked up
- **Part B — the local receiver** on your home server: pulls from the relay over WebSocket, decrypts, delivers to Maildir, serves the alias web UI and IMAP
- **Part C — a client**: any IMAP mail app, plus creating and burning aliases

```
internet ──SMTP 25──▶ relay (VPS) ──sealed box──▶ SQLite queue
                          │  wss:// (nginx + TLS)      │ secure-delete on ack
                          ▼                            ▼
                     local (home) ──▶ Maildir ──▶ dovecot ──IMAP 143──▶ your client
                          │
                          └── web UI :8080 — create / burn aliases
```

The local server connects **outbound only** — no tunnels and no open inbound
ports on your home network. Mail is encrypted end-to-end: the relay seals each
message to your public key (libsodium sealed box) before it ever touches the
queue, and only the home server holds the private key that can open it.

## Prerequisites

- A domain you control, with the ability to set MX and A records
- A VPS with a public IP — runs the relay (Part A)
- Any always-on machine at home (server/NAS) with Docker Compose — runs the local receiver (Part B)
- Docker **and** the Compose v2 plugin on both machines. On Ubuntu 24.04:
  ```bash
  sudo apt install docker.io docker-compose-v2
  ```
  ⚠️ `docker.io` alone does **not** include Compose — the plugin is the separate
  `docker-compose-v2` package. Verify with `docker compose version` before
  continuing (commands in this guide use the `docker compose` v2 syntax).

  If `docker ps` fails with "permission denied … /var/run/docker.sock", your user
  isn't in the `docker` group:
  ```bash
  sudo usermod -aG docker $USER   # then log out/in (or run: newgrp docker)
  ```
- nginx + certbot on the VPS (TLS termination for the WebSocket)

## Part 0 — Generate the encryption keypair (home server)

Run this **on the home server** — the private key must never touch the VPS:

```bash
git clone https://github.com/wbratley/chameleon.git && cd chameleon
pip install services/local        # provides the `chameleon_local` package + keygen
python -m chameleon_local keygen
```

`keygen` writes the private key to `secrets/private_key` (mode 0600) and prints
a `CHAMELEON_PUBLIC_KEY=...` line. Keep both where they are:

- The printed `CHAMELEON_PUBLIC_KEY` value goes in the **relay** `.env` (step A4).
- The `secrets/private_key` file stays in the repo checkout on the home server —
  the local compose file mounts it as a Docker secret (step B4).

Lost the printed value? It's re-derivable from the private key at any time —
run `python -m chameleon_local publickey` in the same directory. Don't rerun
`keygen` to "fix" it: a fresh pair would orphan any mail already sealed to the
old public key.

The relay refuses to start without `CHAMELEON_PUBLIC_KEY` set, and the local
container fails to start if `secrets/private_key` is missing.

---

## Part A — Set up the relay (VPS)

### A1. DNS and firewall

Point your domain at the VPS. You need two A records — `mail` for inbound SMTP
and `relay` for the WebSocket API (nginx/certbot):

```
@      MX  10  mail.yourdomain.com.
mail   A       <VPS_IP>
relay  A       <VPS_IP>
```

Enable the host firewall. The port list is 25 (SMTP), 80 (certbot challenge +
HTTP→HTTPS redirect), 443 (WSS) — and 1025, for a non-obvious reason: the
port-25 redirect added in A3 rewrites the packet to port 1025 *before* the
firewall sees it, so a rule allowing 25 alone would silently drop all inbound
mail. Exposing 1025 directly is harmless — it reaches the same SMTP service
with no extra privilege; the redirect exists only so the unprivileged
container can serve privileged port 25.

```bash
sudo apt install -y ufw        # present on most Ubuntu images; minimal templates omit it
sudo ufw allow OpenSSH        # BEFORE enable — or you lock yourself out
sudo ufw allow 25/tcp         # belt-and-suspenders if the redirect is ever removed
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 1025/tcp       # what the redirected mail actually arrives on
sudo ufw enable
```

Audit the remaining surface with `sudo ss -tlnp`: expect SSH (22), nginx
(80/443), and the relay (1025) only — the API binds 127.0.0.1 and should
never appear on a public address. Anything else gets explained or removed.

**Keys-only SSH** — port 22 with password login is the one remaining exposure
worth closing (internet bots brute-force it continuously). Order matters:
prove key login works *before* disabling passwords, or you lock yourself out:

```bash
# on your workstation:
ls ~/.ssh/id_*.pub || ssh-keygen -t ed25519
ssh-copy-id <user>@<VPS_IP>
ssh -o PasswordAuthentication=no <user>@<VPS_IP> true && echo key login works

# on the VPS — sshd keeps the FIRST value it reads, and Ubuntu cloud images
# ship /etc/ssh/sshd_config.d/50-cloud-init.conf with "PasswordAuthentication yes",
# so this drop-in must sort BEFORE it (00-, not 99-):
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -i passwordauthentication   # must print: passwordauthentication no
```

Keep your existing SSH session open until a fresh terminal has logged in
successfully.

### A2. nginx + TLS

```bash
git clone https://github.com/wbratley/chameleon.git && cd chameleon
cp config/nginx/chameleon.conf /etc/nginx/conf.d/chameleon.conf
# replace every relay.yourdomain.com in that file with your hostname
nano /etc/nginx/conf.d/chameleon.conf
certbot --nginx -d relay.yourdomain.com
systemctl reload nginx
```

The config proxies `/ws` (with WebSocket upgrade headers) and `/health` to
`127.0.0.1:8080`, where the relay's API listens. If you change `CHAMELEON_API_PORT` (e.g. another service already claims 8080), update the `proxy_pass` port in the nginx config to match, and recreate the container (`docker compose up -d`, not `restart` — restart ignores env_file changes). The compose healthcheck follows the configured port automatically.

### A3. Redirect port 25 → 1025

The relay container listens on 1025 to avoid running as root. Redirect port 25
to it (the relay compose file uses host networking):

```bash
iptables -t nat -A PREROUTING -p tcp --dport 25 -j REDIRECT --to-port 1025
# Make persistent (requires iptables-persistent) — save ONLY the nat table:
# a full `iptables-save` would restore filter-table rules too and stomp ufw's
# chains from A1 at boot.
iptables-save -t nat > /etc/iptables/rules.v4
```

### A4. Configure

```bash
cp services/relay/.env.example services/relay/.env
```

Edit `services/relay/.env`:

- `CHAMELEON_MY_DOMAIN=yourdomain.com` — the domain you set the MX record for
- `CHAMELEON_RELAY_HOSTNAME=mail.yourdomain.com` — used in `Received:` headers
- `CHAMELEON_PUBLIC_KEY` — the value `keygen` printed in Part 0 (**required**)
- `CHAMELEON_API_TOKEN` — a strong shared secret, shared with the local server:

  ```bash
  openssl rand -hex 32
  ```

Optional:

- `CHAMELEON_QUEUE_RETAIN_MINUTES` (default 1440 = 24 h) — how long mail
  survives in the queue if the home server is offline before being
  secure-deleted. Raise it to tolerate longer outages, lower it to shrink the
  data-at-rest window.
- `CHAMELEON_TLS_CERT_PATH` / `CHAMELEON_TLS_KEY_PATH` — STARTTLS for inbound
  SMTP. Note: if you enable these you must also mount the cert/key files into
  the container by adding a `volumes:` entry to `docker-compose.relay.yml`.

### A5. Build and start

```bash
docker compose -f docker-compose.relay.yml up -d --build
```

### A6. Verify

```bash
docker compose -f docker-compose.relay.yml ps       # should show (healthy)
curl http://127.0.0.1:8080/health                   # {"status": "ok"}
docker compose -f docker-compose.relay.yml logs relay
```

From outside: `openssl s_client -connect mail.yourdomain.com:25` should show
the SMTP banner.

---

## Part B — Set up the local receiver (home server)

All commands run in the same checkout where you ran `keygen` in Part 0, with
`secrets/private_key` still in place.

### B1. Configure the local service

```bash
cp services/local/.env.example services/local/.env
```

Edit `services/local/.env`:

- `CHAMELEON_RELAY_WS_URL=wss://relay.yourdomain.com/ws`
- `CHAMELEON_RELAY_TOKEN` — the **same value** as `CHAMELEON_API_TOKEN` on the relay
- `CHAMELEON_MY_DOMAIN=yourdomain.com`

The defaults for `MAILDIR_PATH`, `ALIAS_DB_PATH`, `PRIVATE_KEY_PATH` and
`WEB_PORT` match the compose file — leave them unless you know why not.

### B2. Set the IMAP password

Dovecot authenticates every login with one shared password. Create a root
`.env` next to `docker-compose.local.yml`:

```bash
echo "IMAP_PASSWORD=pick-something-strong" > .env
```

### B3. Protect the alias web UI (recommended)

The UI listens on all interfaces for LAN access. Add a shared password to
`services/local/.env`:

```bash
CHAMELEON_WEB_PASSWORD=pick-something-else-strong
```

Browsers prompt for it (HTTP Basic auth — any username works), and a companion
app can present the same `Authorization` header. If unset, the UI starts
**without** authentication (a startup warning is logged) — only acceptable on
a fully trusted network. Mutating requests (create/burn) additionally require
an `Origin` header matching the UI's host, so a malicious webpage cannot forge
cross-site form posts even with cached Basic credentials. Non-browser clients
send no `Origin` and pass with valid credentials, so scripting works, e.g.
`curl -u me:$CHAMELEON_WEB_PASSWORD -d service=Netflix http://server:8080/aliases`.

### B4. Build and start

```bash
docker compose -f docker-compose.local.yml up -d --build
```

This starts:

- `chameleon-local` — connects outbound to the relay and delivers to Maildir
- `dovecot` — serves the Maildir over IMAP on port 143

### B5. Verify

```bash
docker compose -f docker-compose.local.yml logs local
# look for: connected url=wss://relay.yourdomain.com/ws
```

Open `http://<home-server>:8080/` — you should see the alias UI (and the
browser's password prompt if you set one).

---

## Part C — Set up a client

### C1. Add the IMAP account

In any mail client (Thunderbird, Apple Mail, mutt, …):

| Setting     | Value                           |
|-------------|---------------------------------|
| Protocol    | IMAP                            |
| Server      | your home server's IP/hostname  |
| Port        | 143                             |
| Security    | None (plaintext — trusted LAN)  |
| Username    | any string (e.g. `me`)          |
| Password    | the `IMAP_PASSWORD` from B2     |

There is no per-user mail store — username is arbitrary and everyone who has
the password reads the same inbox. Traffic is plaintext, so only connect from
a network you trust (or add a cert to Dovecot first; see `config/dovecot/conf.d/10-ssl.conf`).

### C2. Create your first alias

In the web UI (`http://<home-server>:8080/`), enter a service name like
`netflix` and submit. You'll get an address like
`netflix-k3jx@yourdomain.com` — copy it and use it as your email address when
signing up for that service.

Or from a script:

```bash
curl -u me:$CHAMELEON_WEB_PASSWORD -d service=netflix http://<home-server>:8080/aliases
```

### C3. Send a test email

From any machine with `swaks`:

```bash
swaks --to test@yourdomain.com --server mail.yourdomain.com
```

Or raw SMTP:

```
telnet mail.yourdomain.com 25
EHLO test
MAIL FROM:<tester@example.com>
RCPT TO:<test@yourdomain.com>
DATA
Subject: Test

Hello Chameleon.
.
QUIT
```

The message should appear in your IMAP inbox within seconds. If the home
server was offline when it arrived, it is delivered the moment it reconnects.

### C4. Burn an alias

When an alias starts attracting spam, hit **Burn** next to it in the web UI.
Mail to a burned alias is silently dropped at delivery time — enforced from
the envelope recipients carried inside the encrypted payload, so it cannot be
spoofed by message headers. Burn is permanent.

---

## Troubleshooting

**No mail arriving at relay** — check `docker compose -f docker-compose.relay.yml logs relay`. Confirm port 25 reaches the container: `openssl s_client -connect mail.yourdomain.com:25` (or `telnet localhost 1025` from the VPS). Verify the MX record points at `mail.<domain>` and that your VPS provider doesn't block port 25.

**Relay unhealthy** — `docker compose -f docker-compose.relay.yml logs relay`: it refuses to start without `CHAMELEON_PUBLIC_KEY`. Check `curl http://127.0.0.1:8080/health` and that nginx proxies `/ws` with the upgrade headers intact.

**Local server not connecting** — check `docker compose -f docker-compose.local.yml logs local`. Verify `CHAMELEON_RELAY_WS_URL` and `CHAMELEON_RELAY_TOKEN` match the relay. Test the WebSocket endpoint directly: `curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Authorization: Bearer <token>" https://relay.yourdomain.com/ws`.

**Mail queued but not delivered** — messages are removed from the queue only after the local server acks delivery, so a non-empty queue means it isn't consuming:

```bash
docker exec -it <relay-container> sqlite3 /data/queue.db "SELECT id, received_at FROM messages"
```

Check the token matches and the WebSocket connection is established (B5).

**IMAP login failing** — the password your client sends must match `IMAP_PASSWORD` in the root `.env` (Dovecot reads it as `DOVECOT_PASS`). Username can be anything.

**Permission errors on Maildir** — both `chameleon-local` and `dovecot` run as uid/gid 5000 (`vmail`), enforced by `user: "5000:5000"` in the compose file and Dovecot's static userdb. If the volume was created with wrong permissions, run `docker compose -f docker-compose.local.yml down -v` and recreate (⚠️ this deletes stored mail).
