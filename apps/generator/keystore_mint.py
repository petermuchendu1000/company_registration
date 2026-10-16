"""Mint per-company Android signing keystores, deterministically.

Password = hex(HMAC-SHA256(master_secret, derivation))[:32], where the
derivation is the company number at generation 1, and "{company}#r{n}" after
that.

The master secret lives in `apps/generator/.master_secret` (auto-created on
first run if missing). LOSING IT = every rebuild produces a different
signing identity = Android refuses the "update" install. Back it up.

ROTATION. A password derived from the company number alone cannot be changed:
deleting the keystore and minting again reproduces the same password, because
the same two inputs go in. That matters once a password has leaked — one was
committed to this repository in build.gradle.kts — since a fresh key behind a
known password is not a rotation.

Rotating the master secret would change every company's password at once and
invalidate every signing identity, so generations are per company instead.
`rotate()` bumps one company's generation, retires its keystore and mints a
new one; everyone else keeps the password they already have.

Generations live in `apps/generator/.keystore_rotations.json`, next to the
master secret and just as unlosable: without it a rotated company's password
cannot be derived again. Back up both together.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
_MASTER_SECRET_PATH = os.path.join(_HERE, ".master_secret")
_ROTATIONS_PATH = os.path.join(_HERE, ".keystore_rotations.json")


def _load_rotations() -> dict:
    try:
        with open(_ROTATIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_rotations(rotations: dict) -> None:
    with open(_ROTATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(rotations, f, indent=2, sort_keys=True)
        f.write("\n")
    try:
        os.chmod(_ROTATIONS_PATH, 0o600)
    except OSError:
        pass


def generation(company_number: str) -> int:
    """Which generation of this company's password is current. 1 by default."""
    try:
        return max(1, int(_load_rotations().get(str(company_number), 1)))
    except (TypeError, ValueError):
        return 1


def _derivation(company_number: str, gen: int) -> bytes:
    """
    What the HMAC is taken over.

    Generation 1 is the bare company number, so every keystore minted before
    rotation existed keeps the password it already has.
    """
    cn = str(company_number)
    return (cn if gen <= 1 else f"{cn}#r{gen}").encode("ascii")


def password_for(company_number: str, gen: int | None = None) -> str:
    master = _load_or_create_master()
    g = generation(company_number) if gen is None else gen
    digest = hmac.new(master, _derivation(company_number, g), hashlib.sha256).hexdigest()
    return digest[:32]   # 128 bits, >> keytool minimum of 6


def _load_or_create_master() -> bytes:
    if os.path.exists(_MASTER_SECRET_PATH):
        with open(_MASTER_SECRET_PATH, "rb") as f:
            return f.read().strip()
    secret = secrets.token_hex(32).encode("ascii")  # 64 hex chars
    with open(_MASTER_SECRET_PATH, "wb") as f:
        f.write(secret)
    print(f"  Minted master secret at {_MASTER_SECRET_PATH} — BACK THIS UP.")
    return secret


def _find_keytool() -> str:
    """keytool from PATH or JAVA_HOME. See apps/generator/toolchain.py."""
    from .toolchain import find_keytool
    return find_keytool()


@dataclass(frozen=True)
class Keystore:
    path: str
    store_password: str
    key_alias: str
    key_password: str


def mint(
    company_number: str,
    company_name: str,
    out_dir: str,
) -> Keystore:
    """Return a keystore at {out_dir}/{company_number}.jks. Idempotent."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{company_number}.jks")

    password = password_for(company_number)
    alias = "upload"

    if os.path.exists(path):
        return Keystore(path=path, store_password=password, key_alias=alias, key_password=password)

    keytool = _find_keytool()
    # Sanitise company name for X.500 DN (no commas, no quotes; <= 64 chars per RDN)
    cn = company_name.replace(",", " ").replace('"', "").replace("\\", "").strip()[:64]
    if not cn:
        cn = company_number
    dname = f"CN={cn}, O={cn}, L=London, C=GB"

    cmd = [
        keytool, "-genkeypair",
        "-keystore", path,
        "-storetype", "PKCS12",
        "-storepass", password,
        "-keypass", password,
        "-alias", alias,
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10950",   # 30 years
        "-dname", dname,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"keytool failed for {company_number}:\n"
            f"  stdout: {res.stdout}\n"
            f"  stderr: {res.stderr}"
        )
    return Keystore(path=path, store_password=password, key_alias=alias, key_password=password)


def rotate(company_number: str, company_name: str, out_dir: str) -> Keystore:
    """
    Retire this company's keystore and mint a new one under a new password.

    The old keystore is moved to {out_dir}/retired/{cn}.r{gen}.jks rather than
    deleted. If anything was ever distributed signed with it, that file is the
    only way to ship an update: Android identifies an app by its signing key,
    and refuses an update signed by a different one. Delete the retired copy
    once you are sure nothing shipped with it.

    Rotating only changes what this repository will sign *next*. An artifact
    already uploaded to Play keeps the identity it was signed with, so if the
    old key was an enrolled upload key, Play also needs an upload key reset.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{company_number}.jks")

    rotations = _load_rotations()
    old_gen = generation(company_number)
    new_gen = old_gen + 1

    if os.path.exists(path):
        retired_dir = os.path.join(out_dir, "retired")
        os.makedirs(retired_dir, exist_ok=True)
        retired = os.path.join(retired_dir, f"{company_number}.r{old_gen}.jks")
        shutil.move(path, retired)
        print(f"  retired generation {old_gen} -> {retired}")

    rotations[str(company_number)] = new_gen
    _save_rotations(rotations)
    print(f"  generation {old_gen} -> {new_gen} for {company_number}")

    ks = mint(company_number, company_name, out_dir)
    if ks.store_password == password_for(company_number, old_gen):
        raise RuntimeError(
            "rotation produced the same password — the generation was not applied"
        )
    return ks


def _main() -> int:
    import argparse
    default_out = os.path.join(
        os.path.dirname(_HERE), "template-shift-journal", "app", "keystores"
    )
    p = argparse.ArgumentParser(description="Mint or rotate a signing keystore.")
    p.add_argument("command", choices=["mint", "rotate", "show"])
    p.add_argument("company_number")
    p.add_argument("--name", default="", help="company name for the certificate DN")
    p.add_argument("--out", default=default_out)
    args = p.parse_args()

    if args.command == "show":
        gen = generation(args.company_number)
        path = os.path.join(args.out, f"{args.company_number}.jks")
        print(f"  {args.company_number}: generation {gen}, "
              f"keystore {'present' if os.path.exists(path) else 'absent'} at {path}")
        return 0

    name = args.name or args.company_number
    if args.command == "rotate":
        ks = rotate(args.company_number, name, args.out)
    else:
        ks = mint(args.company_number, name, args.out)
    # The password itself is never printed: this runs in terminals and CI logs.
    print(f"OK  {ks.path}  alias={ks.key_alias}  "
          f"generation={generation(args.company_number)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
