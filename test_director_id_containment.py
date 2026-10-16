"""
Director ID (XBINDER) — containment only.

This stage is not maintained here and nothing in it is exercised for its own
sake. What this file checks is the blast radius: director_id sits at index 5
of both stage orders, ahead of `app` and `publish`, and the dashboard's
run-all loop stops at the first stage that raises:

    for stage in STAGE_ORDER:        # api_v2._seq
        ...
        except Exception:
            break                    # app and publish never run

So any exception escaping stage_director_id costs the two stages that
actually produce and ship the artifact. Everything below exists to prove
that the stage absorbs its own failures instead of taking the run with it.

It also pins two imports the stage reaches for lazily, which a caller cannot
see and an unrelated refactor can therefore delete without breaking anything
until the stage next runs:

  * the six names it pulls from xbinder_auto, imported inside the function
  * run_pipeline._infer_gender, imported inside a branch, reached whenever
    "Director Gender" is absent — which is always, since no stage writes
    that column

Nothing here contacts XBINDER, Companies House or a browser.

    python test_director_id_containment.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

import pipeline


# ── stubs ────────────────────────────────────────────────────────────────────

class FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def current_tenant_id(self):
        return 1

    def all_rows(self, tenant_id):
        return self._rows


def _row(cn="20000001", name="HARBOUR COURIERS LTD", **over):
    base = {
        "Company Number": cn,
        "Company Name": name,
        "Directors": "MWANGI, John",
        "Director Nationalities": "Kenyan",
        "Director DOB": "1985-03-14",   # set, so no Companies House call
        "Director Gender": "M",
    }
    base.update(over)
    return base


class Xbinder:
    """A stub xbinder_auto whose every entry point can be made to fail."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on or set()
        self.binds: list[str] = []

    def _maybe(self, which):
        if which in self.fail_on:
            raise RuntimeError(f"stubbed {which} failure")

    def install(self):
        m = types.ModuleType("xbinder_auto")
        m.login = lambda s, e, p: (self._maybe("login"), {"token": "t", "user_id": "u"})[1]
        m.load_director_profile = lambda path, cn: (
            self._maybe("profile"),
            {"fname": "John", "sname": "", "lname": "Mwangi", "idnumber": "12345678"},
        )[1]

        def create_bind(session, token, profile):
            self._maybe("create_bind")
            return {"bind": {"id": "b1", "idNo": "12345678"}}

        def download(token, uid, bind_id, id_no, dest, headless=True):
            self._maybe("download")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\xff\xd8\xff")        # a file, not a real JPEG
            self.binds.append(bind_id)

        def split(combined):
            self._maybe("split")
            front = combined.parent / "ID Front.jpeg"
            back = combined.parent / "ID Back.jpeg"
            front.write_bytes(b"\xff\xd8\xff")
            back.write_bytes(b"\xff\xd8\xff")
            return front, back

        m.create_bind = create_bind
        m.search_bind = lambda s, t, q: {"items": []}
        m.download_id_via_ui = download
        m.split_combined_id_image = split
        sys.modules["xbinder_auto"] = m
        return self


def _run(rows, env=None, xb=None, make_output_dir=True, **kwargs):
    """
    Run stage_director_id against a throwaway tree.

    Returns the files it left behind, as paths relative to the tree root. The
    tree itself is gone by the time this returns, so callers assert against
    this listing rather than against the filesystem.
    """
    saved_env = {k: os.environ.get(k) for k in
                 ("XBINDER_EMAIL", "XBINDER_PASSWORD", "COMPANIES_HOUSE_API_KEY")}
    saved_base = pipeline.BASE_DIR
    saved_mod = sys.modules.get("xbinder_auto")
    try:
        for k in saved_env:
            os.environ.pop(k, None)
        os.environ.update(env or {})
        if xb:
            xb.install()
        with tempfile.TemporaryDirectory() as d:
            pipeline.BASE_DIR = Path(d)
            # Every other stage creates this; the stage writes its scratch
            # profile straight into it without a mkdir. See
            # test_missing_output_dir_degrades_without_raising.
            if make_output_dir:
                (Path(d) / "pipeline_output").mkdir()
            pipeline.db = FakeDb(rows)
            pipeline.stage_director_id(**kwargs)
            root = Path(d)
            return sorted(p.relative_to(root).as_posix()
                          for p in root.rglob("*") if p.is_file())
    finally:
        pipeline.BASE_DIR = saved_base
        for k, v in saved_env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        if saved_mod is not None:
            sys.modules["xbinder_auto"] = saved_mod
        else:
            sys.modules.pop("xbinder_auto", None)


CREDS = {"XBINDER_EMAIL": "x@example.com", "XBINDER_PASSWORD": "pw"}


# ── the stage absorbs its own failures ───────────────────────────────────────

def test_unconfigured_returns_instead_of_raising() -> None:
    """No credentials is the normal state here. It must not cost app/publish."""
    _run([_row()])          # raises out of _run if the stage raises
    print("  OK  with no XBINDER credentials the stage returns, it does not raise")


def test_login_failure_returns_instead_of_raising() -> None:
    _run([_row()], env=CREDS, xb=Xbinder(fail_on={"login"}))
    print("  OK  a failed XBINDER login returns, it does not raise")


def test_one_company_failing_does_not_stop_the_next() -> None:
    xb = Xbinder()
    rows = [_row("20000001", "HARBOUR COURIERS LTD"),
            _row("20000002", "DOCK ELECTRICAL LTD")]

    calls = {"n": 0}
    xb.install()
    mod = sys.modules["xbinder_auto"]
    real_create = mod.create_bind

    def flaky(session, token, profile):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("stubbed first-company failure")
        return real_create(session, token, profile)

    mod.create_bind = flaky
    files = _run(rows, env=CREDS)
    assert calls["n"] == 2, f"it gave up after the first failure: {calls['n']} attempt(s)"
    assert any("20000002" in f and f.endswith("ID Front.jpeg") for f in files), \
        f"the second company produced no ID: {files}"
    assert not any("20000001" in f and f.endswith("ID Front.jpeg") for f in files), \
        f"the failed company produced an ID anyway: {files}"
    print("  OK  one company failing does not abandon the rest of the batch")


def test_download_failure_is_contained() -> None:
    _run([_row()], env=CREDS, xb=Xbinder(fail_on={"download"}))
    print("  OK  a browser/download failure is contained to the company")


def test_missing_output_dir_degrades_without_raising() -> None:
    """
    The stage writes its scratch profile to pipeline_output/_tmp_profile_<cn>.json
    with no mkdir, so on a tree where no earlier stage has run, every company
    fails at "profile build FAILED". Recorded, not fixed: the failure is caught
    per company, so the stage still returns and run-all still reaches
    app/publish. It does nothing, rather than breaking anything.
    """
    xb = Xbinder()
    files = _run([_row()], env=CREDS, xb=xb, make_output_dir=False)
    assert xb.binds == [], "a bind was created despite the scratch write failing"
    assert not any(f.endswith("ID Front.jpeg") for f in files), \
        f"an ID was produced from a failed profile: {files}"
    print("  OK  a tree with no pipeline_output/ degrades to a no-op, not an exception")


def test_dry_run_contacts_nothing() -> None:
    xb = Xbinder(fail_on={"login", "create_bind", "download"})
    files = _run([_row()], env=CREDS, xb=xb, dry_run=True)
    assert xb.binds == [], xb.binds
    assert not any("ID" in f and f.endswith(".jpeg") for f in files), \
        f"a dry run wrote ID files: {files}"
    print("  OK  a dry run neither logs in nor writes ID files")


# ── the lazy imports the stage depends on ────────────────────────────────────

def test_xbinder_import_surface_is_intact() -> None:
    """stage_director_id imports these inside the function body."""
    sys.modules.pop("xbinder_auto", None)
    import importlib
    real = importlib.import_module("xbinder_auto")
    missing = [n for n in ("login", "create_bind", "search_bind", "download_id_via_ui",
                           "split_combined_id_image", "load_director_profile")
               if not hasattr(real, n)]
    assert not missing, f"xbinder_auto no longer exports: {missing}"
    print("  OK  all six names the stage imports from xbinder_auto still exist")


def test_infer_gender_is_still_importable() -> None:
    """
    No stage writes "Director Gender", so this branch is always taken. The
    import is three levels deep inside a loop, so nothing else would catch
    its removal.
    """
    from run_pipeline import _infer_gender
    assert _infer_gender("MWANGI, John") in {"M", "F", ""}
    xb = Xbinder()
    files = _run([_row(**{"Director Gender": ""})], env=CREDS, xb=xb)
    assert any(f.endswith("ID Front.jpeg") for f in files), \
        f"the missing-gender path did not complete: {files}"
    print("  OK  run_pipeline._infer_gender is reachable for the always-taken branch")


# ── position in the pipeline ─────────────────────────────────────────────────

def test_director_id_precedes_the_shipping_stages() -> None:
    """If this ever stops holding, the risk this file guards has moved."""
    import api_v2
    for order in (pipeline.STAGES, api_v2.STAGE_ORDER):
        i = order.index("director_id")
        for later in ("build", "app", "publish"):
            if later in order:
                assert order.index(later) > i, f"{later} now precedes director_id in {order}"
    print("  OK  director_id still sits ahead of the build/publish stages it can block")


def test_exec_stage_reports_done_without_raising_when_unconfigured() -> None:
    """
    api_v2._seq breaks out of the whole run-all loop on any exception, so an
    unconfigured director_id must come back as a normal result.
    """
    import api_v2
    saved = {k: os.environ.get(k) for k in ("XBINDER_EMAIL", "XBINDER_PASSWORD")}
    saved_base = pipeline.BASE_DIR
    try:
        for k in saved:
            os.environ.pop(k, None)
        with tempfile.TemporaryDirectory() as d:
            pipeline.BASE_DIR = Path(d)
            pipeline.db = FakeDb([_row()])
            out = api_v2._exec_stage("20000001", "director_id", _row())
        assert out == {"status": "done"}, out
    finally:
        pipeline.BASE_DIR = saved_base
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("  OK  an unconfigured director_id returns a result, so run-all reaches app/publish")


def main() -> int:
    import db as real_db
    tests = [
        test_unconfigured_returns_instead_of_raising,
        test_login_failure_returns_instead_of_raising,
        test_one_company_failing_does_not_stop_the_next,
        test_download_failure_is_contained,
        test_missing_output_dir_degrades_without_raising,
        test_dry_run_contacts_nothing,
        test_xbinder_import_surface_is_intact,
        test_infer_gender_is_still_importable,
        test_director_id_precedes_the_shipping_stages,
        test_exec_stage_reports_done_without_raising_when_unconfigured,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
        finally:
            pipeline.db = real_db
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
