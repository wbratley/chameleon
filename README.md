# Chameleon

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Self-hosted disposable email aliases. Give every service its own address —
`netflix-k3jx@yourdomain.com`, `paypal-9m2a@yourdomain.com` — receive the mail
in your normal IMAP client, and **burn** an alias the moment it starts
attracting spam. No third-party alias provider, no inbound ports on your home
network.

## How it works

Chameleon splits mail handling across two machines:

- **Relay** — a small, hardened service on a public VPS. It accepts inbound
  SMTP for your domain, **encrypts every message end-to-end to your public
  key** (libsodium sealed box) *before* it touches the disk, and queues the
  ciphertext in SQLite until your home server picks it up.
- **Local** — runs on your home server (behind NAT/firewall is fine). It makes
  a single *outbound* WebSocket connection to the relay, pulls queued messages,
  decrypts them with the private key (which never leaves home), enforces alias
  burns, and delivers to a Maildir. It also serves a lightweight web UI for
  creating and burning aliases.
- **Dovecot** — exposes the Maildir over IMAP so any mail client (Thunderbird,
  Apple Mail, mutt, …) can read the inbox.

```
internet ──SMTP 25──▶ relay (VPS) ──sealed box──▶ SQLite queue
                          │  wss:// (nginx + TLS)      │ secure-delete on ack
                          ▼                            ▼
                     local (home) ──▶ Maildir ──▶ dovecot ──IMAP 143──▶ your client
                          │
                          └── web UI :8080 — create / burn aliases
```

Properties worth knowing:

- **The relay operator (your VPS) can't read your mail.** Messages are sealed
  to a public key generated on the home server; only the home server holds the
  private key. Queued messages are secure-deleted (`PRAGMA secure_delete`)
  once delivered, and swept after a configurable retention window.
- **No tunnels, no port forwarding.** The home server only ever connects
  outbound, so it works behind CGNAT too.
- **Offline-tolerant.** If the home server is down, mail waits in the relay
  queue (default 24 h retention) and is delivered on reconnect.
- **Burns are enforced at delivery**, using the envelope recipients carried
  inside the encrypted payload — not guessable from message headers.

## Repository layout

| Path | What it is |
|------|------------|
| `services/relay/` | `chameleon-relay` — SMTP receiver + encrypted queue + WebSocket API |
| `services/local/` | `chameleon-local` — WebSocket pull client, alias engine, Maildir delivery, web UI |
| `config/nginx/` | nginx site config (TLS termination for the WebSocket API) |
| `config/dovecot/` | Dovecot config serving the Maildir over IMAP |
| `docs/setup.md` | Full step-by-step deployment guide |

## Setup

You need: a domain where you can set MX/A records, a VPS with a public IP for
the relay, and any always-on machine at home running Docker Compose.

The short version (full details in [`docs/setup.md`](docs/setup.md)):

1. **Keypair (home server)** — the private key must never touch the VPS:

   ```bash
   pip install services/local   # or: pip install -e services/local
   python -m chameleon_local keygen
   # writes secrets/private_key, prints CHAMELEON_PUBLIC_KEY=...
   ```

2. **DNS** — point your domain's MX record at the VPS; open ports 25 and 443.

3. **Relay (VPS)** — configure `services/relay/.env` (domain, public key,
   `CHAMELEON_API_TOKEN=$(openssl rand -hex 32)`), install the nginx config
   with a certbot certificate, redirect port 25 → 1025, then:

   ```bash
   docker compose -f docker-compose.relay.yml up -d
   ```

4. **Local + Dovecot (home)** — configure `services/local/.env` (relay URL and
   token) and the IMAP password, then:

   ```bash
   docker compose -f docker-compose.local.yml up -d
   ```

5. **Read mail** — point any IMAP client at the home server, port 143, and
   manage aliases at `http://<home-server>:8080` (protect it with
   `CHAMELEON_WEB_PASSWORD`).

## Development

Python 3.11+, pytest for tests. Install both services editable with their dev
extras and run the whole suite from the repo root:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e services/relay[dev] -e services/local[dev]
pytest
```

## License

[MIT](LICENSE) © Wayne Bratley
