# Chameleon Phase 1 — Setup Guide

This guide walks through deploying the inbound mail relay on a VPS and the local server on your home machine, then connecting them with an frp tunnel.

## Prerequisites

- A domain you control with the ability to set MX and A records
- A VPS (any cloud provider) with a public IP — this is your relay
- A home server or NAS running Docker Compose — this is your local mail store
- Docker and Docker Compose installed on both machines

## 1. DNS

Point your domain's MX record at your VPS:

```
@   MX  10  mail.yourdomain.com.
mail  A     <VPS_IP>
```

Allow port 25 inbound on the VPS firewall.

## 2. Install frp

Download the frp binary on **both** the VPS and the home server from:
https://github.com/fatedier/frp/releases

Choose the `linux_amd64` archive. Extract and keep `frps` on the VPS and `frpc` on the home server.

## 3. Configure the tunnel

Copy the templates and fill in your values:

**VPS** — edit `config/frp/frps.toml`:
- Replace `CHANGE_ME_STRONG_RANDOM_SECRET` with a random string (e.g. `openssl rand -hex 32`)

**Home server** — edit `config/frp/frpc.toml`:
- Set `serverAddr` to your VPS IP
- Set `auth.token` to the same secret as frps

Run on VPS:
```bash
./frps -c config/frp/frps.toml
```

Run on home server:
```bash
./frpc -c config/frp/frpc.toml
```

Once connected, port 2525 on the VPS localhost will forward to port 2525 on the home server.

## 4. Configure the relay (VPS)

```bash
cp services/relay/.env.example services/relay/.env
```

Edit `services/relay/.env`:
```
CHAMELEON_MY_DOMAIN=yourdomain.com
CHAMELEON_RELAY_HOSTNAME=mail.yourdomain.com
CHAMELEON_LISTEN_PORT=25
```

Build and start:
```bash
docker compose -f docker-compose.relay.yml up -d
```

The relay container uses host networking and listens directly on port 25.

## 5. Configure the local server (home)

```bash
cp services/local/.env.example services/local/.env
```

Set an IMAP password in a `.env` file at the repo root:
```bash
echo "IMAP_PASSWORD=your_strong_password_here" > .env
```

Build and start:
```bash
docker compose -f docker-compose.local.yml up -d
```

This starts:
- `chameleon-local` on `127.0.0.1:2525` — receives mail from the relay via tunnel
- `dovecot` on ports 143 (IMAP) and 993 (IMAPS) — exposes your Maildir to mail clients

## 6. Connect a mail client

Configure any IMAP client (Thunderbird, Apple Mail, mutt) with:

| Setting      | Value                  |
|--------------|------------------------|
| IMAP server  | your home server IP    |
| Port         | 143                    |
| Security     | None (local network)   |
| Username     | any string (e.g. `me`) |
| Password     | value from `.env`      |

## 7. Send a test email

From any machine:
```bash
swaks --to test@yourdomain.com --server mail.yourdomain.com
```

Or with telnet:
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

Check your IMAP client — the message should appear in the inbox within seconds.

## Troubleshooting

**Mail not arriving**: check relay logs with `docker compose -f docker-compose.relay.yml logs relay`. Look for `forward_failed` — this means the relay can't reach the local server via the tunnel.

**Tunnel not connecting**: ensure frps is running on the VPS and frpc on the home server. Check that port 7000 is open on the VPS firewall.

**IMAP login failing**: verify `DOVECOT_PASS` in the root `.env` matches what your client is sending.

**Permission errors on Maildir**: both `chameleon-local` and `dovecot` must own files as uid 5000. The Docker Compose `user: "5000:5000"` and Dovecot's `userdb static args = uid=5000 gid=5000` enforce this.
