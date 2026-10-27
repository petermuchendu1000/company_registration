#!/usr/bin/env bash
# Service account, systemd units, and Caddy with automatic HTTPS.
#
#   bash deploy/install.sh gglabs.tukaze.com
#
# Exists because the alternative is pasting forty lines into a terminal,
# and the Vultr noVNC console interleaves keystrokes with the output of
# whatever is still running - which has now silently eaten a `curl -1sLf
# 'htt` prefix and left a shell stuck on an unterminated quote. One short
# command cannot be mangled that way.
#
# Idempotent: every step checks before acting, so re-running after a
# failure resumes rather than duplicating.
set -euo pipefail

HOSTNAME_ARG="${1:-}"
APP_DIR="/opt/company_registration"
BROWSERS="/opt/ms-playwright"

if [[ -z "$HOSTNAME_ARG" ]]; then
  echo "usage: bash deploy/install.sh <hostname>"
  echo "   eg: bash deploy/install.sh gglabs.tukaze.com"
  exit 2
fi

say() { printf '\n=== %s\n' "$*"; }
ok()  { printf '    [ok] %s\n' "$*"; }
bad() { printf '    [!!] %s\n' "$*"; }

cd "$APP_DIR"

# ── 1. service account ──────────────────────────────────────────────────
say "1. service account"
if id pipeline &>/dev/null; then
  ok "user 'pipeline' already exists"
else
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin pipeline
  ok "created user 'pipeline'"
fi

# Playwright downloaded its browsers into root's cache; the service user
# cannot read them there.
if [[ -d /root/.cache/ms-playwright ]]; then
  mkdir -p "$BROWSERS"
  cp -a /root/.cache/ms-playwright/. "$BROWSERS"/
  ok "browsers copied to $BROWSERS"
else
  bad "/root/.cache/ms-playwright missing - was 'playwright install' run?"
fi

chown -R pipeline:pipeline "$APP_DIR" "$BROWSERS"
chmod 600 "$APP_DIR/.env" 2>/dev/null || bad ".env not found"
ok "ownership and .env permissions set"

# ── 2. systemd units ────────────────────────────────────────────────────
say "2. systemd units"
install -m 644 deploy/xvfb.service deploy/company-registration.service \
        /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xvfb company-registration
sleep 4
for u in xvfb company-registration; do
  if systemctl is-active --quiet "$u"; then
    ok "$u is active"
  else
    bad "$u is NOT active - journalctl -u $u -n 40 --no-pager"
  fi
done

code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
       http://127.0.0.1:8000/health || echo "000")
if [[ "$code" == "200" ]]; then
  ok "the app answers on 127.0.0.1:8000 (health $code)"
else
  bad "the app did NOT answer on 127.0.0.1:8000 (got $code)"
  bad "journalctl -u company-registration -n 40 --no-pager"
  exit 1
fi

# ── 3. Caddy ────────────────────────────────────────────────────────────
say "3. Caddy"
if command -v caddy &>/dev/null; then
  ok "caddy already installed ($(caddy version | head -1))"
else
  apt-get install -y --no-install-recommends \
      debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
            /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
  ok "caddy installed"
fi

# The Caddyfile in the repo names a hostname; substitute whatever was
# passed so this script works for any domain without editing the file.
sed "s/gglabs\.tukaze\.com/${HOSTNAME_ARG}/" deploy/Caddyfile \
    > /etc/caddy/Caddyfile
ok "wrote /etc/caddy/Caddyfile for ${HOSTNAME_ARG}"

# Validate BEFORE reloading, so a bad config is caught while the running
# server is still serving the old one.
if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  ok "caddy config validates"
else
  bad "caddy config is INVALID - not reloading:"
  caddy validate --config /etc/caddy/Caddyfile || true
  exit 1
fi

systemctl reload caddy || systemctl restart caddy
sleep 8
if systemctl is-active --quiet caddy; then
  ok "caddy is active"
else
  bad "caddy is NOT active - journalctl -u caddy -n 40 --no-pager"
  exit 1
fi

# ── 4. report ───────────────────────────────────────────────────────────
say "4. result"
echo "    listening sockets:"
# Plain grep, NOT awk. Debian's awk is mawk, which does not support \s, so
# an earlier `awk '/:(80|443|8000)\s/'` here matched nothing and printed a
# bare header - output that reads as "nothing is listening" when in fact
# the filter was broken. Verified: mawk 1.3.4 prints only the header for
# that pattern against ss output containing all three sockets.
ss -tlnp 2>/dev/null > /tmp/_ss_install.txt
head -1 /tmp/_ss_install.txt | sed 's/^/      /'
grep -E ':(80|443|8000) ' /tmp/_ss_install.txt | sed 's/^/      /' || \
  echo "      (none of :80 :443 :8000 are listening)"
echo
echo "    Certificate issuance happens on the first HTTPS request and can"
echo "    take a few seconds. From your own machine, not this server:"
echo
echo "      curl -sSI https://${HOSTNAME_ARG}/health"
echo "      open     https://${HOSTNAME_ARG}"
echo
echo "    If the certificate fails, port 80 is the usual cause - it carries"
echo "    the ACME challenge even though the site is HTTPS:"
echo "      journalctl -u caddy -n 40 --no-pager"
