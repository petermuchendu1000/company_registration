"""
The dev@ forwarding stage.

dev@<domain> is the developer contact on the Play listing, so a silent
failure here is a blocked submission later. Two faults:

  1. The stage re-implemented the Namecheap calls inline against
     api.namecheap.com hardcoded — ignoring NAMECHEAP_SANDBOX — with its own
     XML parsing and no retry on a rate limit, duplicating
     namecheap_automation, which already preserves the domain's other
     mailboxes and honours both.
  2. It looked for the app manifest at pipeline_output/apps/{cn}, a layout
     nothing has written since the per-company folders landed. The file was
     never found, so support_email was never updated and the stage silently
     did half its job.

Namecheap and the datastore are stubbed; nothing leaves the machine.

    python test_stage_email.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

import pipeline


class Recorder:
    def __init__(self, result=None):
        self.calls: list[tuple[str, str]] = []
        self._result = result or {"status": "configured", "success": True}

    def ensure_dev_email_forwarding(self, domain, forward_to):
        self.calls.append((domain, forward_to))
        return self._result


def _install(rec: Recorder) -> None:
    mod = types.ModuleType("namecheap_automation")
    mod.ensure_dev_email_forwarding = rec.ensure_dev_email_forwarding
    sys.modules["namecheap_automation"] = mod


class FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def current_tenant_id(self):
        return 1

    def all_rows(self, tenant_id):
        return self._rows


ROW = {
    "Company Number": "20000001",
    "Company Name": "HARBOUR COURIERS LTD",
    "Domain": "harbour.online",
    "Assigned Email": "someone@gmail.com",
}


def _with_tree(fn):
    """
    Run fn(base) against a temp tree.

    Both knobs are needed: pipeline.BASE_DIR for the legacy
    pipeline_output/apps layout the stage still falls back to, and
    PIPELINE_OUTPUT_DIR for company_paths, which resolves the current
    per-tenant layout.
    """
    import os
    original = pipeline.BASE_DIR
    original_out = os.environ.get("PIPELINE_OUTPUT_DIR")
    with tempfile.TemporaryDirectory() as d:
        pipeline.BASE_DIR = Path(d)
        os.environ["PIPELINE_OUTPUT_DIR"] = str(Path(d) / "pipeline_output")
        try:
            return fn(Path(d))
        finally:
            pipeline.BASE_DIR = original
            os.environ.pop("PIPELINE_OUTPUT_DIR", None)
            if original_out is not None:
                os.environ["PIPELINE_OUTPUT_DIR"] = original_out


def test_uses_the_shared_namecheap_helper() -> None:
    rec = Recorder()
    _install(rec)
    pipeline.db = FakeDb([ROW])
    _with_tree(lambda base: pipeline.stage_email())
    assert rec.calls == [("harbour.online", "someone@gmail.com")], rec.calls
    print("  OK  the stage calls namecheap_automation instead of its own API code")


def test_manifest_is_found_in_the_current_layout() -> None:
    rec = Recorder()
    _install(rec)
    pipeline.db = FakeDb([ROW])

    def run(base: Path) -> Path:
        import company_paths
        app = company_paths.company_dir("20000001", "HARBOUR COURIERS LTD") / "app"
        app.mkdir(parents=True)
        manifest = app / "manifest.json"
        manifest.write_text(json.dumps({"support_email": "old@example.com"}))
        pipeline.stage_email()
        return json.loads(manifest.read_text())

    data = _with_tree(run)
    assert data["support_email"] == "dev@harbour.online", data
    print("  OK  the app manifest is found where the generator writes it")


def test_legacy_manifest_layout_still_works() -> None:
    rec = Recorder()
    _install(rec)
    pipeline.db = FakeDb([ROW])

    def run(base: Path) -> dict:
        legacy = base / "pipeline_output" / "apps" / "20000001"
        legacy.mkdir(parents=True)
        manifest = legacy / "manifest.json"
        manifest.write_text(json.dumps({"support_email": "old@example.com"}))
        pipeline.stage_email()
        return json.loads(manifest.read_text())

    data = _with_tree(run)
    assert data["support_email"] == "dev@harbour.online", data
    print("  OK  the older layout is still updated when that is what exists")


def test_external_dns_is_skipped_not_failed() -> None:
    rec = Recorder({"status": "error", "error": "Failed: ['Domain uses external DNS servers']"})
    _install(rec)
    pipeline.db = FakeDb([ROW])
    _with_tree(lambda base: pipeline.stage_email())
    assert rec.calls, "it did not even try"
    print("  OK  a domain on external DNS is reported as skipped")


def test_dry_run_changes_nothing() -> None:
    rec = Recorder()
    _install(rec)
    pipeline.db = FakeDb([ROW])

    def run(base: Path) -> dict:
        import company_paths
        app = company_paths.company_dir("20000001", "HARBOUR COURIERS LTD") / "app"
        app.mkdir(parents=True)
        manifest = app / "manifest.json"
        manifest.write_text(json.dumps({"support_email": "old@example.com"}))
        pipeline.stage_email(dry_run=True)
        return json.loads(manifest.read_text())

    data = _with_tree(run)
    assert rec.calls == [], f"a dry run called Namecheap: {rec.calls}"
    assert data["support_email"] == "old@example.com", data
    print("  OK  a dry run neither calls Namecheap nor rewrites the manifest")


def test_company_filter_is_honoured() -> None:
    rec = Recorder()
    _install(rec)
    other = {**ROW, "Company Number": "20000002", "Domain": "other.online"}
    pipeline.db = FakeDb([ROW, other])
    _with_tree(lambda base: pipeline.stage_email(company_numbers=["20000002"]))
    assert rec.calls == [("other.online", "someone@gmail.com")], rec.calls
    print("  OK  --company limits the stage to the named companies")


def main() -> int:
    import db as real_db
    tests = [
        test_uses_the_shared_namecheap_helper,
        test_manifest_is_found_in_the_current_layout,
        test_legacy_manifest_layout_still_works,
        test_external_dns_is_skipped_not_failed,
        test_dry_run_changes_nothing,
        test_company_filter_is_honoured,
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
