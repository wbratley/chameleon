# Chameleon — Setup Guide

Mail flows inbound → relay (VPS) → SQLite queue → WebSocket API → local server → Maildir → Dovecot → IMAP client.

The local server connects outbound to the relay. No tunnel, no inbound ports required on the home server.

Mail is encrypted end-to-end: the relay seals each message to your public key
(libsodium sealed box) before it ever touches the queue, and only the home
server holds the private key that can open it. So before deploying, you generate
a keypair — the **public** key goes in the relay config, the **private** key
stays on the home server (see step 0).

## Prerequisites

- A domain you control with the ability to set MX and A records
- A VPS with a public IP — this runs the relay
- A home server or NAS running Docker Compose — this stores your mail
- Docker and Docker Compose on both machines
- nginx + certbot on the VPS (TLS termination for the WebSocket API)

## 0. Generate the encryption keypair (home server)

Run this **on the home server** — the private key must never touch the VPS:

```bash
python -m chameleon_local keygen
```

This writes the private key to `secrets/private_key` (mode 0600) and prints a
`CHAMELEON_PUBLIC_KEY=...` line. Keep the terminal output handy:

- The printed `CHAMELEON_PUBLIC_KEY` value goes in the **relay** `.env` (step 2c).
- The `secrets/private_key` file is mounted into the **local** container as a
  Docker secret (step 3) — leave it where `keygen` put it, relative to the
  `docker-compose.local.yml` you'll run.

The relay refuses to start without `CHAMELEON_PUBLIC_KEY` set, and the local
container fails to start if `secrets/private_key` is missing.

## 1. DNS

Point your domain's MX record at the VPS:

```
@     MX  10  mail.yourdomain.com.
mail  A       <VPS_IP>
```

Allow ports 25 (SMTP inbound) and 443 (HTTPS/WSS) on the VPS firewall.

## 2. Deploy the relay (VPS)

### 2a. Configure nginx + TLS

Copy `config/nginx/chameleon.conf` to `/etc/nginx/conf.d/chameleon.conf`.  
Replace `relay.yourdomain.com` with your actual hostname.

Obtain a certificate:
```bash
certbot --nginx -d relay.yourdomain.com
```

### 2b. Port 25 redirect

The relay container listens on port 1025 to avoid needing root. Redirect port 25 to it:

```bash
iptables -t nat -A PREROUTING -p tcp --dport 25 -j REDIRECT --to-port 1025
# Make persistent:
iptables-save > /etc/iptables/rules.v4
```

### 2c. Configure and start

```bash
cp services/relay/.env.example services/relay/.env
```

Edit `services/relay/.env`:

- Set `CHAMELEON_MY_DOMAIN` and `CHAMELEON_RELAY_HOSTNAME`.
- Set `CHAMELEON_PUBLIC_KEY` to the value `keygen` printed in step 0.
- Generate a strong API token:

  ```bash
  openssl rand -hex 32  # use this as CHAMELEON_API_TOKEN
  ```

Build and start:
```bash
docker compose -f docker-compose.relay.yml up -d
```

Verify it's healthy:
```bash
docker compose -f docker-compose.relay.yml ps
curl http://127.0.0.1:8080/health
```

## 3. Deploy the local server (home)

### 3a. Configure

```bash
cp services/local/.env.example services/local/.env
```

Edit `services/local/.env`:
- Set `CHAMELEON_RELAY_WS_URL=wss://relay.yourdomain.com/ws`
- Set `CHAMELEON_RELAY_TOKEN` to the same value as `CHAMELEON_API_TOKEN` on the relay

Set an IMAP password:
```bash
echo "IMAP_PASSWORD=your_strong_password_here" > .env
```

### 3b. Start

```bash
docker compose -f docker-compose.local.yml up -d
```

This starts:
- `chameleon-local` — connects outbound to the relay WebSocket API and delivers mail to Maildir
- `dovecot` on ports 143 (IMAP) and 993 (IMAPS) — exposes your Maildir to mail clients

Check that the local service connected:
```bash
docker compose -f docker-compose.local.yml logs local
# Should show: connected url=wss://relay.yourdomain.com/ws
```

## 4. Connect a mail client

Configure any IMAP client (Thunderbird, Apple Mail, mutt) with:

| Setting     | Value                          |
|-------------|--------------------------------|
| IMAP server | your home server IP or hostname |
| Port        | 143                            |
| Security    | None (local network) or STARTTLS if you add a cert to Dovecot |
| Username    | any string (e.g. `me`)        |
| Password    | value from `IMAP_PASSWORD`    |

## 5. Send a test email

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

The message should appear in your IMAP inbox within seconds. If the local server was offline when mail arrived, it will be delivered the moment it reconnects.

## Troubleshooting

**No mail arriving at relay**: check relay logs (`docker compose -f docker-compose.relay.yml logs relay`). Confirm port 25 reaches the container: `telnet localhost 1025` from the VPS.

**Local server not connecting**: check `docker compose -f docker-compose.local.yml logs local`. Verify `CHAMELEON_RELAY_WS_URL` and `CHAMELEON_RELAY_TOKEN` match the relay config. Test the WebSocket endpoint: `curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Authorization: Bearer <token>" https://relay.yourdomain.com/ws`.

**IMAP login failing**: verify `DOVECOT_PASS` in the root `.env` matches what your client sends.

**Mail queued but not delivered**: if the relay has messages in the queue (`docker exec -it <relay> sqlite3 /data/queue.db "SELECT id, received_at FROM messages"`), the local server isn't processing them. Messages are removed from the queue only once the local server acks delivery, so a non-empty queue means it isn't consuming them. Check the token matches and the WebSocket connection is established.

**Permission errors on Maildir**: both `chameleon-local` and `dovecot` run as uid 5000 (`vmail`). The Docker Compose `user: "5000:5000"` and Dovecot's `userdb static args = uid=5000 gid=5000` enforce this. If the volume was created with wrong permissions, run `docker compose -f docker-compose.local.yml down -v` and recreate.
