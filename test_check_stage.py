#!/usr/bin/env python3
"""Tests for stage 1, the Namecheap IP check (pipeline.py:80-84, check_ip.py).

    python3 test_check_stage.py

Hermetic: no network, no Namecheap account, no real .env. check_ip imports
requests at module scope, so patching requests.get covers it; ENV_FILE is a
module-level Path, so pointing it at a temp directory covers the .env writes.
Nothing in production is modified to make any of this testable.

Stage 1 is four lines of pipeline.py and the gate on which the other four
stages depend, so the failure that matters is not "stage 1 crashed" — it is
stage 1 saying OK when the answer is no. Three separate paths did that:

  * check_ip.py:84 tested the SUBSTRING "ip", which fires inside ordinary
    English (Descr-ip-tion, Mult-ip-le, Z-ip, Equ-ip-ment, Scr-ip-t). Ten of
    the messages in IP_CASES below were classified as whitelist failures when
    they were nothing of the kind.

  * check_ip.py:87 returned the literal "OK" for every non-IP API error, so
    the API's own text — "API Key is invalid or API access has not been
    enabled", "Too many login attempts" — was discarded at the only point it
    existed.

  * pipeline.py:143 never read Status, and an error response carries no
    Domain elements. The two together produced a run that read as healthy
    end to end:

        Stage 1:  Testing Namecheap API access... OK
                  Namecheap API: OK
        Stage 2:  Namecheap domains: []
                  0 new domain(s) written to Supabase.

    which is indistinguishable from an account with nothing left to match.

The .env checks exist because _update_env_ip rewrites the file that holds
DATABASE_URL, NAMECHEAP_API_KEY, FLASK_SECRET_KEY and SUPERADMIN_PASSWORD. It
used to do so with write_text, which truncates the real file to zero bytes
before writing anything; check_atomic_write below fails the write on purpose
and asserts the original survived.
"""
from __future__ import annotations

import io
import ipaddress
import os
import re
import stat
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

import requests

import check_ip

NS_URI = "http://api.namecheap.com/xml.response"

CREDS = {
    "NAMECHEAP_API_USER": "test-user",
    "NAMECHEAP_API_KEY":  "test-key",
    "NAMECHEAP_USERNAME": "test-user",
}


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
        return bool(cond)

    def report(self) -> int:
        print()
        print(f"{self.passed} passed / {len(self.failed)} failed")
        for f in self.failed:
            print(f"  failed: {f}")
        return 1 if self.failed else 0


# ── fixtures ────────────────────────────────────────────────────────────
def _ok_xml() -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<ApiResponse Status="OK" xmlns="{NS_URI}">'
            '<CommandResponse><DomainGetListResult>'
            '<Domain ID="1" Name="example.uk" />'
            '</DomainGetListResult></CommandResponse></ApiResponse>')


def _err_xml(*messages: str, number: str = "1011102") -> str:
    """The shape Namecheap really returns, captured from the live API:

        <ApiResponse Status="ERROR" xmlns="...">
          <Errors>
            <Error Number="1011102">API Key is invalid or API access has
            not been enabled</Error>
          </Errors>
          ...
        </ApiResponse>

    Nought Domain elements, which is the whole mechanism behind defect C.
    """
    errs = "".join(f'<Error Number="{number}">{m}</Error>' for m in messages)
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<ApiResponse Status="ERROR" xmlns="{NS_URI}">'
            f'<Errors>{errs}</Errors></ApiResponse>')


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


def _call_api(body: str | None = None, raises: BaseException | None = None,
              creds: dict | None = None):
    """Call the real test_namecheap_api with requests.get stubbed."""
    saved_get = requests.get
    saved_env = {k: os.environ.get(k) for k in CREDS}
    try:
        for k, v in (CREDS if creds is None else creds).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if creds is not None:
            for k in CREDS:
                if k not in creds:
                    os.environ.pop(k, None)

        def fake_get(*a, **k):
            if raises is not None:
                raise raises
            return _Resp(body if body is not None else _ok_xml())

        requests.get = fake_get
        try:
            return check_ip.test_namecheap_api("203.0.113.7"), None
        except Exception as e:                  # noqa: BLE001 - reported
            return None, e
    finally:
        requests.get = saved_get
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── defect A: the IP classifier ─────────────────────────────────────────
# (message, is_really_an_IP_whitelist_problem). The first group is real
# documented Namecheap error text; the second group is messages whose words
# merely contain the letters "ip", which is what the old substring rule
# matched on.
IP_CASES = [
    ("Parameter RequestIP is invalid", True),                       # 1011150
    ("Invalid request IP", True),
    ("Invalid request IP: 203.0.113.7", True),
    ("Parameter ClientIp is missing", True),
    ("IP is not whitelisted", True),
    ("Your IP is not in the whitelist", True),
    # These two exercise the "whitelist" alternative on its own: every other
    # True case above also contains the standalone word "IP", so \bip\b alone
    # would satisfy them and deleting "whitelist" from the pattern would go
    # unnoticed. They are plausible wordings the alternative exists to cover,
    # not text quoted from Namecheap's documentation.
    ("Request from a non-whitelisted address", True),
    ("Access denied: address not whitelisted", True),
    # False here means only "_IP_ERROR_RE does not match it". It is a
    # blocking fault all the same, caught by _ACCESS_ERROR_RE instead — see
    # check_access_error below. Namecheap sends this exact text to a host
    # whose source address is not whitelisted.
    ("API Key is invalid or API access has not been enabled", False),
    ("Too many login attempts", False),
    ("Domain is invalid", False),                                   # 2030166
    ("Promotion code is invalid", False),                           # 2011170
    ("No Products in the cart", False),
    ("Authentication failed", False),
    ("Error from Enom when retrieving Domain list", False),
    ("UserName is invalid", False),
    ("Description is too long", False),
    ("Multiple errors occurred", False),
    ("Recipient address rejected", False),
    ("Zip code is invalid", False),
    ("Shipping address missing", False),
    ("Subscription has expired", False),
    ("Participant limit reached", False),
    ("Equipment unavailable", False),
    ("Script execution failed", False),
    ("Tipping point exceeded", False),
    ("Nickname is invalid", False),
]

# The rule that was at check_ip.py:84, kept here so the suite can show the
# new one is an improvement rather than merely asserting it is correct.
OLD_KEYWORDS = ("invalid request ip", "ip", "not allowed", "whitelist")


def check_ip_classifier(r: Results) -> None:
    print("\n-- defect A: 'ip' must be matched as a word, not a substring --")
    wrong = [(m, t) for m, t in IP_CASES
             if bool(check_ip._IP_ERROR_RE.search(m)) is not t]
    r.check(f"classifies all {len(IP_CASES)} sample messages correctly",
            not wrong, f"misclassified {wrong}")

    # If this is zero the check above proves nothing: it would mean the old
    # rule was already right and the change was pointless.
    old_wrong = [m for m, t in IP_CASES
                 if any(kw in m.lower() for kw in OLD_KEYWORDS) is not t]
    r.check("the previous substring rule got 10 of them wrong",
            len(old_wrong) == 10,
            f"old rule misclassified {len(old_wrong)}: {old_wrong}")

    # Each false-positive word, named, so a future edit that reintroduces a
    # bare substring test says which word broke it.
    for word in ("Description", "Multiple", "Recipient", "Zip", "Shipping",
                 "Subscription", "Participant", "Equipment", "Script",
                 "Tipping"):
        r.check(f"{word!r} is not read as an IP error",
                not check_ip._IP_ERROR_RE.search(f"{word} is invalid"))

    # ...and each real IP wording still is.
    for msg in ("Parameter RequestIP is invalid", "Invalid request IP",
                "Parameter ClientIp is missing", "IP is not whitelisted",
                "Request from a non-whitelisted address"):
        r.check(f"{msg!r} IS read as an IP error",
                bool(check_ip._IP_ERROR_RE.search(msg)))


# ── defect B: the discarded message ─────────────────────────────────────
def check_error_message_survives(r: Results) -> None:
    print("\n-- defect B: a non-IP API error must not be reported as 'OK' --")
    (res, err) = _call_api(_ok_xml())
    r.check("Status=OK gives (True, 'OK')", res == (True, "OK"), f"{res!r} {err!r}")

    for msg in ("Too many login attempts",
                "Domain is invalid",
                "Promotion code is invalid",
                "No Products in the cart"):
        (res, err) = _call_api(_err_xml(msg))
        ok = res is not None and res[0] is True and res[1] == msg
        r.check(f"non-IP error is returned verbatim: {msg[:38]!r}", ok,
                f"got {res!r} {err!r}")
        r.check(f"...and is NOT the string 'OK': {msg[:38]!r}",
                res is not None and res[1] != "OK", f"got {res!r}")

    for msg in ("Parameter RequestIP is invalid", "IP is not whitelisted"):
        (res, err) = _call_api(_err_xml(msg))
        r.check(f"IP error gives (False, msg): {msg!r}",
                res == (False, msg), f"got {res!r} {err!r}")

    # The checks above pass under the OLD substring rule too, because none of
    # those four real messages happens to contain the letters "ip". Without
    # the three below, every assertion about the classifier is made against
    # the compiled pattern in isolation and nothing pins the call site: a
    # revert of check_ip.py:84 alone would go unnoticed. These push an
    # "ip"-inside-a-word message through the real function.
    for msg in ("Description is too long", "Zip code is invalid",
                "Multiple errors occurred"):
        (res, err) = _call_api(_err_xml(msg))
        r.check(f"{msg!r} is not a whitelist verdict (via the real call)",
                res == (True, msg), f"got {res!r} {err!r}")

    # Several Errors are joined, not dropped.
    (res, err) = _call_api(_err_xml("Domain is invalid", "Too many requests"))
    r.check("multiple Error elements are all reported",
            res is not None and res[1] == "Domain is invalid; Too many requests",
            f"got {res!r}")

    # Status=ERROR with no Error element must not collapse to a blank message
    # that then reads as success.
    (res, err) = _call_api('<?xml version="1.0"?>'
                           f'<ApiResponse Status="ERROR" xmlns="{NS_URI}" />')
    r.check("Status=ERROR with no Error element still reports something",
            res is not None and res[1] and res[1] != "OK", f"got {res!r}")

    # A transport failure names the exception type. str(ConnectTimeout) is a
    # URL blob with no indication of what went wrong.
    (res, err) = _call_api(raises=requests.exceptions.ConnectTimeout("boom"))
    r.check("transport failure reports (False, ...)",
            res is not None and res[0] is False, f"got {res!r} {err!r}")
    r.check("transport failure names the exception type",
            res is not None and "ConnectTimeout" in res[1], f"got {res!r}")

    # Malformed XML must not be mistaken for an empty-but-valid response.
    (res, err) = _call_api("<not xml")
    r.check("malformed XML reports (False, ...)",
            res is not None and res[0] is False, f"got {res!r} {err!r}")
    r.check("malformed XML names ParseError",
            res is not None and "ParseError" in res[1], f"got {res!r}")


# ── error 1011102: three causes, one message ────────────────────────────
# Measured against the live API from a non-whitelisted host: the real key,
# the same key with one hex digit changed, and a nonexistent ApiUser all
# produced byte-identical responses —
#
#   <ApiResponse Status="ERROR" ...><Errors>
#     <Error Number="1011102">API Key is invalid or API access has not
#     been enabled</Error></Errors>...
#
# so Namecheap has no distinct message for "your source address is not
# whitelisted", and this one cannot be read as "the key is wrong". Treating
# it as a non-IP error — which is what the first version of this fix did —
# warns and carries on while the single thing stage 1 exists to detect has
# happened.
ACCESS_MSG = "API Key is invalid or API access has not been enabled"


def check_access_error(r: Results) -> None:
    print("\n-- 1011102 is ambiguous and must stop the run, not warn --")
    r.check("the live 1011102 text is recognised",
            bool(check_ip._ACCESS_ERROR_RE.search(ACCESS_MSG)))
    r.check("the 'API access' half is recognised on its own",
            bool(check_ip._ACCESS_ERROR_RE.search(
                "API access has not been enabled")),
            "the two halves of the message must match independently")
    r.check("the 'API Key is invalid' half is recognised on its own",
            bool(check_ip._ACCESS_ERROR_RE.search("API Key is invalid")))
    for other in ("Domain is invalid", "Too many login attempts",
                  "Promotion code is invalid", "Parameter RequestIP is invalid",
                  "UserName is invalid", "Description is too long"):
        r.check(f"{other!r} is not read as an access error",
                not check_ip._ACCESS_ERROR_RE.search(other))

    (res, err) = _call_api(_err_xml(ACCESS_MSG))
    r.check("the real 1011102 response gives (False, msg)",
            res == (False, ACCESS_MSG), f"got {res!r} {err!r}")

    ret, err, out, _ = _run_ensure((False, ACCESS_MSG))
    r.check("the stage stops", isinstance(err, SystemExit) and err.code == 1,
            f"got ret={ret!r} err={err!r}")
    r.check("...and does not return an IP as if the check had passed",
            ret is None, f"got {ret!r}")
    r.check("...naming the whitelist as the usual cause",
            "Whitelisted IPs" in out, f"got {out!r}")
    # Assert on wording unique to the instruction line. "API access" alone is
    # satisfied by the echoed API message at the foot of the block, so a
    # mutant that deleted the instruction still passed.
    r.check("...naming the API-access switch",
            "API access switched ON" in out, f"got {out!r}")
    r.check("...naming the key in .env",
            "NAMECHEAP_API_KEY in .env" in out, f"got {out!r}")
    r.check("...and the address to check", "203.0.113.7" in out, f"got {out!r}")
    r.check("...without claiming it is definitely the whitelist",
            "whitelist your IP" not in out,
            "the response cannot distinguish the three causes, so the "
            f"instructions must not pick one. {out!r}")
    r.check("...and it is not reported as a warning-and-continue",
            "WARNING" not in out, f"got {out!r}")


# ── defect G: empty credentials ─────────────────────────────────────────
def check_missing_credentials(r: Results) -> None:
    print("\n-- defect G: a missing credential must name itself, not be sent empty --")
    for key in CREDS:
        creds = {k: v for k, v in CREDS.items() if k != key}
        (res, err) = _call_api(_ok_xml(), creds=creds)
        r.check(f"missing {key} raises", isinstance(err, RuntimeError),
                f"got res={res!r} err={err!r}")
        r.check(f"...and the error names {key}",
                err is not None and key in str(err), f"got {err!r}")

    (res, err) = _call_api(_ok_xml(), creds={**CREDS, "NAMECHEAP_API_KEY": ""})
    r.check("an empty-string credential also raises",
            isinstance(err, RuntimeError), f"got res={res!r} err={err!r}")

    (res, err) = _call_api(_ok_xml(), creds={})
    r.check("all three missing are named together",
            err is not None and all(k in str(err) for k in CREDS),
            f"got {err!r}")

    # The guard must not fire when everything is present, or every other
    # check in this suite would be passing for the wrong reason.
    (res, err) = _call_api(_ok_xml())
    r.check("complete credentials do NOT raise", err is None, f"got {err!r}")


# ── defect E/H: the public IP lookup ────────────────────────────────────
def _call_get_public_ip(answers: list):
    """answers: per-provider, either a body string or an Exception to raise."""
    calls = []
    saved = requests.get

    def fake_get(url, *a, **k):
        calls.append(url)
        a_ = answers[len(calls) - 1]
        if isinstance(a_, BaseException):
            raise a_
        return _Resp(a_)

    try:
        requests.get = fake_get
        try:
            return check_ip.get_public_ip(), None, calls
        except Exception as e:                  # noqa: BLE001 - reported
            return None, e, calls
    finally:
        requests.get = saved


def check_public_ip(r: Results) -> None:
    print("\n-- defects E and H: provider fallback, and the answer must be an IP --")
    r.check("api.ipify.org is still tried first",
            check_ip.IP_PROVIDERS[0] == "https://api.ipify.org",
            f"providers={check_ip.IP_PROVIDERS!r}; the normal path must be "
            "byte-identical to the previous single-provider behaviour")
    r.check("there is more than one provider",
            len(check_ip.IP_PROVIDERS) > 1,
            "one provider and no fallback meant a single third-party outage "
            "aborted all five pipeline stages")

    n = len(check_ip.IP_PROVIDERS)

    ip, err, calls = _call_get_public_ip(["203.0.113.7"] + ["unused"] * (n - 1))
    r.check("the first provider's answer is used", ip == "203.0.113.7",
            f"got {ip!r} {err!r}")
    r.check("...and no other provider is contacted", len(calls) == 1,
            f"contacted {calls}")

    ip, err, calls = _call_get_public_ip(
        [requests.exceptions.ConnectTimeout("down"), "198.51.100.9"]
        + ["unused"] * (n - 2))
    r.check("a dead first provider falls through to the second",
            ip == "198.51.100.9", f"got {ip!r} {err!r}")
    r.check("...having tried exactly two", len(calls) == 2, f"contacted {calls}")

    # The reason validation exists: the body used to be written into .env
    # after nothing more than .strip().
    ip, err, calls = _call_get_public_ip(
        ["<html><body>502 Bad Gateway</body></html>", "198.51.100.9"]
        + ["unused"] * (n - 2))
    r.check("an HTML error page is rejected, not returned",
            ip == "198.51.100.9", f"got {ip!r} {err!r}")

    ip, err, calls = _call_get_public_ip(
        ["rate limit exceeded", "198.51.100.9"] + ["unused"] * (n - 2))
    r.check("a text notice that is not an address is rejected",
            ip == "198.51.100.9", f"got {ip!r} {err!r}")

    ip, err, calls = _call_get_public_ip(["203.0.113.999", "198.51.100.9"]
                                         + ["unused"] * (n - 2))
    r.check("an out-of-range octet is rejected",
            ip == "198.51.100.9", f"got {ip!r} {err!r}")

    ip, err, calls = _call_get_public_ip(
        ["2001:db8::1"] + ["unused"] * (n - 1))
    r.check("a valid IPv6 answer is accepted", ip == "2001:db8::1",
            f"got {ip!r} {err!r}")

    ip, err, calls = _call_get_public_ip(["  203.0.113.7\n"]
                                         + ["unused"] * (n - 1))
    r.check("surrounding whitespace is stripped", ip == "203.0.113.7",
            f"got {ip!r} {err!r}")

    ip, err, calls = _call_get_public_ip(
        [requests.exceptions.ConnectTimeout("a")] * n)
    r.check("all providers failing raises RuntimeError",
            isinstance(err, RuntimeError), f"got ip={ip!r} err={err!r}")
    r.check("...and all providers were tried", len(calls) == n,
            f"contacted {calls}")
    r.check("...and the error names each provider",
            err is not None and all(p in str(err)
                                    for p in check_ip.IP_PROVIDERS),
            f"got {err!r}")

    ip, err, calls = _call_get_public_ip(["nonsense"] * n)
    r.check("all providers answering nonsense raises RuntimeError",
            isinstance(err, RuntimeError), f"got ip={ip!r} err={err!r}")
    r.check("...and says what was received",
            err is not None and "nonsense" in str(err), f"got {err!r}")


# ── defect D: the .env rewrite ──────────────────────────────────────────
ENV_BODY = (
    "# production secrets\n"
    "DATABASE_URL=postgresql://app:s3cret@db.internal:5432/company\n"
    "NAMECHEAP_API_KEY=f81fa145ea414646babce9f8c98cb860\n"
    "NAMECHEAP_CLIENT_IP=95.179.203.236\n"
    "FLASK_SECRET_KEY=deadbeefcafebabe\n"
    "SUPERADMIN_PASSWORD=correct-horse\n"
)

OTHER_KEYS = ("DATABASE_URL", "NAMECHEAP_API_KEY", "FLASK_SECRET_KEY",
              "SUPERADMIN_PASSWORD")


def _with_env(body: str | None, mode: int | None = 0o600):
    """Context: check_ip.ENV_FILE points at a fresh temp .env."""
    class _Ctx:
        def __enter__(self):
            self.dir = Path(tempfile.mkdtemp())
            self.path = self.dir / ".env"
            if body is not None:
                self.path.write_text(body, encoding="utf-8")
                if mode is not None:
                    os.chmod(self.path, mode)
            self.saved = check_ip.ENV_FILE
            check_ip.ENV_FILE = self.path
            return self

        def __exit__(self, *exc):
            check_ip.ENV_FILE = self.saved
            return False

        def leftovers(self):
            return sorted(p.name for p in self.dir.iterdir()
                          if p.name != ".env")

    return _Ctx()


def check_env_rewrite(r: Results) -> None:
    print("\n-- defect D: rewriting .env must preserve every other key --")
    with _with_env(ENV_BODY) as c:
        check_ip._update_env_ip("203.0.113.7")
        out = c.path.read_text(encoding="utf-8")
        r.check("the IP line was updated",
                "NAMECHEAP_CLIENT_IP=203.0.113.7" in out, f"got {out!r}")
        r.check("the old IP is gone", "95.179.203.236" not in out, f"got {out!r}")
        r.check("exactly one NAMECHEAP_CLIENT_IP line",
                len(re.findall(r"^NAMECHEAP_CLIENT_IP=", out, re.M)) == 1,
                f"got {out!r}")
        for key in OTHER_KEYS:
            orig = next(l for l in ENV_BODY.splitlines()
                        if l.startswith(key + "="))
            r.check(f"{key} survives byte-for-byte",
                    orig in out.splitlines(), f"expected {orig!r} in {out!r}")
        r.check("the comment survives", "# production secrets" in out)
        r.check("line count unchanged",
                len(out.splitlines()) == len(ENV_BODY.splitlines()),
                f"{len(out.splitlines())} vs {len(ENV_BODY.splitlines())}")
        r.check("no temp file left behind", c.leftovers() == [],
                f"leftovers: {c.leftovers()}")

    print("\n-- the key is appended when absent, and the file is created --")
    with _with_env("DATABASE_URL=x\n") as c:
        check_ip._update_env_ip("203.0.113.7")
        out = c.path.read_text(encoding="utf-8")
        r.check("appended when the key was absent",
                "NAMECHEAP_CLIENT_IP=203.0.113.7" in out, f"got {out!r}")
        r.check("the existing key survives the append",
                "DATABASE_URL=x" in out, f"got {out!r}")
        r.check("the file still ends in a newline", out.endswith("\n"),
                f"got {out!r}")

    with _with_env(None) as c:
        check_ip._update_env_ip("203.0.113.7")
        r.check("a missing .env is created", c.path.exists())
        r.check("...with only the IP line",
                c.path.read_text(encoding="utf-8").strip()
                == "NAMECHEAP_CLIENT_IP=203.0.113.7",
                f"got {c.path.read_text(encoding='utf-8')!r}")
        r.check("...at mode 0600",
                stat.S_IMODE(c.path.stat().st_mode) == 0o600,
                f"mode {oct(stat.S_IMODE(c.path.stat().st_mode))}")

    print("\n-- permissions: .env must stay 0600 (deploy/install.sh:52) --")
    # os.replace keeps the SOURCE file's mode, not the destination's, so an
    # atomic write that forgets to chmod the temp file silently relaxes the
    # permissions on a file full of secrets.
    with _with_env(ENV_BODY, mode=0o600) as c:
        check_ip._update_env_ip("203.0.113.7")
        m = stat.S_IMODE(c.path.stat().st_mode)
        r.check("0600 stays 0600", m == 0o600, f"mode {oct(m)}")
        r.check("the file is not group- or world-readable",
                not (m & (stat.S_IRGRP | stat.S_IROTH)), f"mode {oct(m)}")
    with _with_env(ENV_BODY, mode=0o640) as c:
        check_ip._update_env_ip("203.0.113.7")
        m = stat.S_IMODE(c.path.stat().st_mode)
        r.check("an operator's own 0640 is preserved, not overwritten",
                m == 0o640, f"mode {oct(m)}")


def check_atomic_write(r: Results) -> None:
    print("\n-- defect D: a failed write must leave the original .env intact --")
    # This is the check the old implementation could not pass. write_text
    # opens the real .env with mode "w", which truncates it to zero bytes
    # before a byte is written; anything that interrupts the write leaves the
    # production secrets gone.
    saved_replace = os.replace

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    with _with_env(ENV_BODY) as c:
        err = None
        try:
            os.replace = boom
            check_ip._update_env_ip("203.0.113.7")
        except Exception as e:                  # noqa: BLE001 - reported
            err = e
        finally:
            os.replace = saved_replace
        r.check("the failure is raised, not swallowed", isinstance(err, OSError),
                f"got {err!r}")
        r.check("the original .env is byte-for-byte unchanged",
                c.path.read_text(encoding="utf-8") == ENV_BODY,
                f"got {c.path.read_text(encoding='utf-8')!r}")
        for key in OTHER_KEYS:
            r.check(f"{key} survived the failed write",
                    key + "=" in c.path.read_text(encoding="utf-8"))
        r.check("no half-written copy of the secrets left behind",
                c.leftovers() == [], f"leftovers: {c.leftovers()}")

    # Same, failing earlier - during the write itself rather than the rename.
    saved_fsync = os.fsync
    with _with_env(ENV_BODY) as c:
        err = None
        try:
            os.fsync = boom
            check_ip._update_env_ip("203.0.113.7")
        except Exception as e:                  # noqa: BLE001 - reported
            err = e
        finally:
            os.fsync = saved_fsync
        r.check("a failure during the write is also raised",
                isinstance(err, OSError), f"got {err!r}")
        r.check("the original .env is still intact",
                c.path.read_text(encoding="utf-8") == ENV_BODY,
                f"got {c.path.read_text(encoding='utf-8')!r}")
        r.check("and still no leftovers", c.leftovers() == [],
                f"leftovers: {c.leftovers()}")

    print("\n-- the temp file must be a sibling of .env, not somewhere else --")
    # os.replace across filesystems raises EXDEV, so a temp file in /tmp would
    # turn every IP change into a crash on any host where /tmp is a separate
    # mount — and the "no leftovers" checks above would not notice, because
    # they only look in .env's own directory.
    seen = []
    with _with_env(ENV_BODY) as c:
        def spy(src, dst, *a, **k):
            seen.append((str(src), str(dst)))
            return saved_replace(src, dst, *a, **k)
        try:
            os.replace = spy
            check_ip._update_env_ip("203.0.113.7")
        finally:
            os.replace = saved_replace
        r.check("os.replace was used at all", len(seen) == 1, f"calls: {seen}")
        r.check("the temp file is in the same directory as .env",
                len(seen) == 1
                and os.path.dirname(seen[0][0]) == str(c.dir),
                f"temp was {seen[0][0] if seen else None!r}, "
                f".env dir is {c.dir}")
        r.check("...and it replaced .env itself",
                len(seen) == 1 and seen[0][1] == str(c.path), f"calls: {seen}")

    print("\n-- the new IP reaches re.sub as data, not as a replacement template --")
    # new_ip comes from a third-party HTTP response. re.sub reads backslash
    # escapes in a replacement string, so \g<0> or \1 in that value would be
    # expanded into the file.
    for hostile in (r"\g<0>", r"\1", "a\\b"):
        with _with_env(ENV_BODY) as c:
            check_ip._update_env_ip(hostile)
            out = c.path.read_text(encoding="utf-8")
            r.check(f"{hostile!r} is written literally",
                    f"NAMECHEAP_CLIENT_IP={hostile}" in out, f"got {out!r}")
            r.check(f"...and the other keys survive {hostile!r}",
                    all(k + "=" in out for k in OTHER_KEYS), f"got {out!r}")


# ── ensure_ip_whitelisted: the stage's actual control flow ──────────────
def _run_ensure(api_result, current="203.0.113.7", stored="203.0.113.7",
                verbose=True, body=ENV_BODY):
    """Call the real ensure_ip_whitelisted with its two helpers stubbed."""
    saved_pub = check_ip.get_public_ip
    saved_api = check_ip.test_namecheap_api
    saved_env = os.environ.get("NAMECHEAP_CLIENT_IP")
    buf = io.StringIO()
    err = None
    ret = None
    with _with_env(body) as c:
        try:
            if stored is None:
                os.environ.pop("NAMECHEAP_CLIENT_IP", None)
            else:
                os.environ["NAMECHEAP_CLIENT_IP"] = stored
            check_ip.get_public_ip = lambda: current
            check_ip.test_namecheap_api = lambda ip: api_result
            with redirect_stdout(buf):
                ret = check_ip.ensure_ip_whitelisted(verbose=verbose)
        except BaseException as e:               # SystemExit is expected
            err = e
        finally:
            check_ip.get_public_ip = saved_pub
            check_ip.test_namecheap_api = saved_api
            written = c.path.read_text(encoding="utf-8") if c.path.exists() else ""
            if saved_env is None:
                os.environ.pop("NAMECHEAP_CLIENT_IP", None)
            else:
                os.environ["NAMECHEAP_CLIENT_IP"] = saved_env
    return ret, err, buf.getvalue(), written


def check_ensure_flow(r: Results) -> None:
    print("\n-- stage 1 says OK only when the API said OK --")
    ret, err, out, _ = _run_ensure((True, "OK"))
    r.check("a clean call returns the IP", ret == "203.0.113.7",
            f"got {ret!r} {err!r}")
    r.check("...raises nothing", err is None, f"got {err!r}")
    r.check("...prints OK", "OK" in out, f"got {out!r}")
    r.check("...prints no warning", "WARNING" not in out, f"got {out!r}")

    print("\n-- defect B: a non-IP API error must be visible, not silent --")
    # Deliberately NOT the 1011102 text, which is a blocking fault and is
    # covered by check_access_error. This is an error specific to one
    # command, where carrying on is the right call.
    msg = "Domain is invalid"
    ret, err, out, _ = _run_ensure((True, msg))
    r.check("the stage still returns the IP (contract unchanged)",
            ret == "203.0.113.7", f"got {ret!r} {err!r}")
    r.check("...and does not exit", err is None, f"got {err!r}")
    r.check("...but prints a WARNING", "WARNING" in out, f"got {out!r}")
    r.check("...carrying the API's own message", msg in out, f"got {out!r}")
    r.check("...and does not print a bare OK",
            not re.search(r"access\.\.\. OK", out), f"got {out!r}")
    r.check("...and says later stages will fail",
            "later stages" in out.lower() or "will fail" in out.lower(),
            f"got {out!r}")

    # The warning is the only record of the fault, so it must not be hidden
    # behind verbose.
    ret, err, out, _ = _run_ensure((True, msg), verbose=False)
    r.check("the WARNING is printed even when verbose=False",
            "WARNING" in out, f"got {out!r}")

    print("\n-- an IP verdict exits 1 with whitelist instructions --")
    ret, err, out, _ = _run_ensure((False, "Parameter RequestIP is invalid"))
    r.check("exits", isinstance(err, SystemExit), f"got {err!r}")
    r.check("...with status 1", isinstance(err, SystemExit) and err.code == 1,
            f"got {err!r}")
    r.check("...printing ACTION REQUIRED", "ACTION REQUIRED" in out, f"got {out!r}")
    r.check("...naming the IP to whitelist", "203.0.113.7" in out, f"got {out!r}")
    r.check("...and the settings URL",
            check_ip.NAMECHEAP_SETTINGS_URL in out, f"got {out!r}")
    r.check("...and the API message", "RequestIP" in out, f"got {out!r}")

    print("\n-- a transport failure must NOT be dressed up as a whitelist verdict --")
    ret, err, out, _ = _run_ensure((False, "ConnectTimeout: api.namecheap.com"))
    r.check("still exits 1", isinstance(err, SystemExit) and err.code == 1,
            f"got {err!r}")
    r.check("does NOT tell the operator to whitelist an IP",
            "ACTION REQUIRED" not in out,
            "sending an operator to the Namecheap whitelist page for a "
            f"network timeout wastes the one thing they have: time. {out!r}")
    r.check("says the verdict is unknown", "unknown" in out.lower(), f"got {out!r}")
    r.check("...and still reports the real error", "ConnectTimeout" in out,
            f"got {out!r}")

    # "ConnectTimeout: api.namecheap.com" contains neither "ip" nor "not
    # allowed", so the check above also passes under the old substring rule
    # and pins nothing. A proxy answering 405 does contain "Not Allowed",
    # which the old rule counted as a whitelist failure - and this container's
    # own outbound proxy returns exactly that for a disallowed host.
    ret, err, out, _ = _run_ensure((False, "HTTPError: 405 Method Not Allowed"))
    r.check("a 405 is not reported as a whitelist failure",
            "ACTION REQUIRED" not in out,
            "'Not Allowed' in a transport error is not a whitelist verdict. "
            f"{out!r}")
    r.check("...and the 405 itself is reported", "405" in out, f"got {out!r}")

    print("\n-- a changed IP is recorded before the check, and announced --")
    ret, err, out, written = _run_ensure((True, "OK"), current="198.51.100.9",
                                         stored="95.179.203.236")
    r.check("the change is announced", "changed" in out.lower(), f"got {out!r}")
    r.check("the new IP is written to .env",
            "NAMECHEAP_CLIENT_IP=198.51.100.9" in written, f"got {written!r}")
    r.check("the rest of .env survives the update",
            all(k + "=" in written for k in OTHER_KEYS), f"got {written!r}")
    r.check("the returned IP is the current one", ret == "198.51.100.9",
            f"got {ret!r}")

    ret, err, out, written = _run_ensure((True, "OK"), current="203.0.113.7",
                                         stored="203.0.113.7")
    r.check("an unchanged IP says so", "unchanged" in out.lower(), f"got {out!r}")
    r.check("...and .env is left exactly as it was", written == ENV_BODY,
            f"got {written!r}")

    ret, err, out, written = _run_ensure((True, "OK"), current="198.51.100.9",
                                         stored=None)
    r.check("a first run with no stored IP writes one",
            "NAMECHEAP_CLIENT_IP=198.51.100.9" in written, f"got {written!r}")


# ── defect C: the stage1 -> stage2 silent-failure chain ─────────────────
def check_stage2_reads_status(r: Results) -> None:
    print("\n-- defect C: stage 2 must not read a rejected call as an empty account --")
    import pipeline

    for key, val in (("NAMECHEAP_API_USER", "test-user"),
                     ("NAMECHEAP_API_KEY", "test-key"),
                     ("NAMECHEAP_USERNAME", "test-user"),
                     ("NAMECHEAP_CLIENT_IP", "127.0.0.1")):
        os.environ.setdefault(key, val)

    class _Rec:
        def all_rows(self, use_cache: bool = True):
            return [{"Company Number": "00000001",
                     "Company Name": "ACME WIDGETS LTD", "Domain": ""}]

        def update_row(self, cn, updates):
            raise AssertionError("stage_match wrote during a rejected call")

    body = _err_xml("API Key is invalid or API access has not been enabled")
    saved_get, saved_db = requests.get, pipeline.db
    buf = io.StringIO()
    err = None
    try:
        requests.get = lambda *a, **k: _Resp(body)
        pipeline.db = _Rec()
        with redirect_stdout(buf):
            pipeline.stage_match(dry_run=False)
    except Exception as e:                      # noqa: BLE001 - reported
        err = e
    finally:
        requests.get = saved_get
        pipeline.db = saved_db
    out = buf.getvalue()

    r.check("a rejected getList raises", isinstance(err, RuntimeError),
            f"got err={err!r} out={out!r}")
    r.check("...and the error carries the API's message",
            err is not None and "API Key is invalid" in str(err), f"got {err!r}")
    r.check("...and the stage does not print an empty domain list",
            "Namecheap domains: []" not in out,
            "an empty list here is indistinguishable from an account with "
            f"nothing left to match. {out!r}")
    r.check("...and does not report a successful run",
            "0 new domain(s) written" not in out, f"got {out!r}")


def main() -> int:
    r = Results()
    print(f"target: {os.path.join(REPO, 'check_ip.py')}"
          f" + {os.path.join(REPO, 'pipeline.py')} stage_check/stage_match")
    check_ip_classifier(r)
    check_error_message_survives(r)
    check_access_error(r)
    check_missing_credentials(r)
    check_public_ip(r)
    check_env_rewrite(r)
    check_atomic_write(r)
    check_ensure_flow(r)
    check_stage2_reads_status(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
