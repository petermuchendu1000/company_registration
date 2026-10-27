#!/usr/bin/env bash
# Why is the site not reachable? Read-only.
#
#   bash deploy/diagnose.sh gglabs.tukaze.com
#
# ERR_TIMED_OUT in a browser means packets were DROPPED, not refused. That
# rules in a firewall and rules out "nothing is listening" (which refuses),
# but only once you have actually looked. There are three independent
# places it can break, and each section below isolates one:
#
#   A. is Caddy bound to :80 and :443 on this host?
#   B. does this host answer HTTP on loopback?       (app vs proxy)
#   C. is something dropping packets before they arrive? (host firewall)
#
# A passing A and B with a failing browser means the problem is upstream of
# the host - a Vultr cloud firewall - which cannot be seen from in here and
# is named at the end.
#
# No awk regex extensions are used anywhere. Debian's awk is mawk, which
# does not support \s, and an earlier version of install.sh printed an
# empty socket list for that reason - output that reads as "nothing is
# listening" when the filter was simply broken.
set -uo pipefail

HOST="${1:-gglabs.tukaze.com}"
say() { printf '\n=== %s\n' "$*"; }
ok()  { printf '    [ok] %s\n' "$*"; }
bad() { printf '    [!!] %s\n' "$*"; }

say "A. what is listening on this host"
# Plain fixed-string greps; no regex extensions.
ss -tlnp > /tmp/_ss.txt 2>/dev/null
head -1 /tmp/_ss.txt | sed 's/^/      /'
grep -E ':(80|443|8000|22) ' /tmp/_ss.txt | sed 's/^/      /' || true
for p in 80 443 8000; do
  if grep -qE ":${p} " /tmp/_ss.txt; then ok "something is listening on :${p}"
  else bad "NOTHING is listening on :${p}"; fi
done

say "B. does this host answer on loopback"
# Read curl's EXIT STATUS, not its printed body. An earlier version did
# `code=$(curl ... || echo 000)` and tested `[ "$code" != "000" ]`. When
# curl failed it printed 000 AND the fallback appended another, giving
# "000000", which is != "000", so a FAILED request was reported [ok].
# That is exactly the check that cannot fail in the direction it must.
probe() {   # probe <label> <curl args...>
  local label="$1"; shift
  local out rc
  out=$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$@" 2>/dev/null)
  rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$out" ] && [ "$out" != "000" ]; then
    ok "$label -> HTTP $out"
  else
    bad "$label -> FAILED (curl exit $rc, code '${out:-none}')"
  fi
}
probe "http://127.0.0.1/"             "http://127.0.0.1/"
probe "http://127.0.0.1:8000/health"  "http://127.0.0.1:8000/health"
probe "https://${HOST}/ via loopback" -k --resolve "${HOST}:443:127.0.0.1" \
      "https://${HOST}/health"

say "C. host firewall"
found=0
if command -v nft >/dev/null 2>&1; then
  rules=$(nft list ruleset 2>/dev/null | grep -vc '^$' || echo 0)
  if [ "${rules:-0}" -gt 0 ]; then
    found=1; bad "nftables has ${rules} rule lines:"; nft list ruleset 2>/dev/null | sed 's/^/      /' | head -40
  else ok "nftables ruleset is empty"; fi
fi
if command -v iptables >/dev/null 2>&1; then
  n=$(iptables -S 2>/dev/null | grep -vc '^-P' || echo 0)
  pol=$(iptables -S 2>/dev/null | grep '^-P' | tr '\n' ' ')
  echo "      iptables policies: ${pol:-none}"
  if [ "${n:-0}" -gt 0 ]; then found=1; bad "iptables has ${n} non-policy rules:"; iptables -S | sed 's/^/      /' | head -30
  else ok "iptables has no non-policy rules"; fi
  if iptables -S 2>/dev/null | grep -q '^-P INPUT DROP'; then
    found=1; bad "INPUT policy is DROP - inbound traffic is blocked by default"
  fi
fi
if command -v ufw >/dev/null 2>&1; then
  ustat=$(ufw status 2>/dev/null | head -1)
  echo "      ufw: ${ustat}"
  if echo "$ustat" | grep -qi active; then
    found=1
    ufw status 2>/dev/null | sed 's/^/      /'
    for p in 80 443; do
      if ufw status 2>/dev/null | grep -qE "(^| )${p}(/tcp)?( |$|.*ALLOW)"; then
        ok "ufw permits ${p}"
      else
        bad "ufw does NOT permit ${p}  ->  ufw allow ${p}/tcp"
      fi
    done
  fi
fi
[ "$found" = "0" ] && ok "no host firewall rules found"

say "D. caddy state"
systemctl is-active caddy >/dev/null 2>&1 && ok "caddy is active" || bad "caddy is NOT active"
echo "      last 12 caddy log lines:"
journalctl -u caddy -n 12 --no-pager 2>/dev/null | sed 's/^/      /'
CERTDIR=/var/lib/caddy/.local/share/caddy/certificates
if [ -d "$CERTDIR" ]; then
  n=$(find "$CERTDIR" -name '*.crt' 2>/dev/null | wc -l)
  if [ "$n" -gt 0 ]; then ok "$n certificate(s) obtained"; find "$CERTDIR" -name '*.crt' | sed 's/^/      /'
  else bad "no certificate obtained yet"; fi
else
  bad "no certificate directory yet ($CERTDIR) - ACME has not succeeded"
fi

say "E. verdict"
echo "    If A and B are [ok] and C shows no blocking rules, then this host"
echo "    is serving correctly and the packets are being dropped BEFORE they"
echo "    reach it. That is outside this machine and cannot be seen from in"
echo "    here. Check the Vultr control panel:"
echo
echo "      Products -> Firewall, and the Firewall tab on this instance."
echo "    A Vultr firewall group attached to the instance drops anything not"
echo "    explicitly allowed; ports 80 and 443 both need an accept rule."
echo "    Port 80 is required even though the site is HTTPS - it carries the"
echo "    ACME challenge, and without it no certificate can ever be issued."
