"""
The certificate subpipeline.

Four defects, all of which produced a confident-looking wrong answer rather
than an error:

  1. A 200 response whose body was an HTML error page was written out as a
     .pdf and counted as a certificate by every later check.
  2. Filing history returns newest first, 100 per page. For a company that
     has filed more than that, the incorporation is not on page one, and the
     fallback took the last item of that page — the hundredth-newest filing,
     saved and labelled as the certificate of incorporation.
  3. The dashboard's file-based detection looked for "*_cert.pdf", a name
     nothing in this tree has ever written.
  4. A failed download returned a reason rather than raising, so the job was
     recorded as done while the stage still showed Pending.

Companies House is stubbed, so no request leaves the machine.

    python test_certificate.py
"""

from __future__ import annotations

import os
import tempfile

import run_pipeline

PDF = b"%PDF-1.4\n" + b"x" * 4000


class FakeResponse:
    def __init__(self, status_code=200, body=b""):
        self.status_code = status_code
        self._body = body

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def _filing(n, *, incorporation=False, doc=True):
    return {
        "category": "incorporation" if incorporation else "accounts",
        "description": "incorporation-company" if incorporation else f"accounts-{n}",
        "type": "NEWINC" if incorporation else "AA",
        "date": f"20{n:02d}-01-01",
        "links": {"document_metadata": f"/document/doc{n}"} if doc else {},
    }


def _patch(monkey: dict):
    for name, value in monkey.items():
        setattr(run_pipeline, name, value)


def test_html_error_page_is_not_saved_as_a_certificate() -> None:
    body = b"<!DOCTYPE html><html><body>Service unavailable</body></html>"
    _patch({
        "ch_get": lambda path, params=None: {"items": [_filing(1, incorporation=True)],
                                             "total_count": 1},
        "ch_get_raw": lambda url, headers=None, stream=False: FakeResponse(200, body),
    })
    with tempfile.TemporaryDirectory() as d:
        path, err = run_pipeline.download_certificate("00000001", d)
        assert path is None, f"it saved the error page as {path}"
        assert "not a PDF" in err, err
        assert os.listdir(d) == [], f"it left a file behind: {os.listdir(d)}"
    print("  OK  an HTML error page is rejected, not saved as a .pdf")


def test_truncated_pdf_is_rejected_and_cleaned_up() -> None:
    _patch({
        "ch_get": lambda path, params=None: {"items": [_filing(1, incorporation=True)],
                                             "total_count": 1},
        "ch_get_raw": lambda url, headers=None, stream=False: FakeResponse(200, b"%PDF-1.4\ntiny"),
    })
    with tempfile.TemporaryDirectory() as d:
        path, err = run_pipeline.download_certificate("00000001", d)
        assert path is None, path
        assert "bytes" in err, err
        assert os.listdir(d) == [], f"the stub file was left behind: {os.listdir(d)}"
    print("  OK  a too-small document is rejected and the file removed")


def test_incorporation_found_beyond_the_first_page() -> None:
    """A company with 250 filings: the incorporation is on the last page."""
    page1 = [_filing(n) for n in range(100)]                     # newest 100
    tail = [_filing(n) for n in range(100, 149)] + [_filing(149, incorporation=True)]
    requested: list[dict] = []

    def ch_get(path, params=None):
        requested.append(dict(params or {}))
        if (params or {}).get("start_index"):
            return {"items": tail, "total_count": 250}
        return {"items": page1, "total_count": 250}

    _patch({"ch_get": ch_get,
            "ch_get_raw": lambda url, headers=None, stream=False: FakeResponse(200, PDF)})
    with tempfile.TemporaryDirectory() as d:
        path, err = run_pipeline.download_certificate("00000002", d)
        assert err is None, err
        assert path and path.endswith("Certificate_00000002.pdf"), path
    assert len(requested) == 2, f"it never paged past the first request: {requested}"
    assert requested[1]["start_index"] == 150, requested[1]
    print("  OK  the incorporation filing is found past the first page")


def test_no_document_at_all_is_an_error() -> None:
    _patch({
        "ch_get": lambda path, params=None: {"items": [_filing(1, doc=False)], "total_count": 1},
        "ch_get_raw": lambda url, headers=None, stream=False: FakeResponse(200, PDF),
    })
    with tempfile.TemporaryDirectory() as d:
        path, err = run_pipeline.download_certificate("00000003", d)
        assert path is None, path
        assert "document" in err.lower(), err
    print("  OK  a company with no filed documents reports that, rather than guessing")


def test_good_certificate_is_saved() -> None:
    _patch({
        "ch_get": lambda path, params=None: {"items": [_filing(1, incorporation=True)],
                                             "total_count": 1},
        "ch_get_raw": lambda url, headers=None, stream=False: FakeResponse(200, PDF),
    })
    with tempfile.TemporaryDirectory() as d:
        path, err = run_pipeline.download_certificate("00000004", d)
        assert err is None, err
        assert os.path.getsize(path) == len(PDF), os.path.getsize(path)
        with open(path, "rb") as f:
            assert f.read(5) == b"%PDF-"
    print("  OK  a real certificate is written whole")


def test_dashboard_detects_the_file_the_downloader_writes() -> None:
    """_compute_stages used to look for a name nothing ever wrote."""
    import api_v2
    with tempfile.TemporaryDirectory() as d:
        cdir = os.path.join(d, "00000005 - TEST LTD")
        cert = os.path.join(cdir, "certificate")
        os.makedirs(cert)
        with open(os.path.join(cert, "Certificate_00000005.pdf"), "wb") as f:
            f.write(PDF)

        original = api_v2._co_dir
        api_v2._co_dir = lambda cn, name: __import__("pathlib").Path(cdir)
        try:
            stages = api_v2._compute_stages("00000005", {"Company Name": "TEST LTD"})
        finally:
            api_v2._co_dir = original
    assert stages["certificate"] is True, "the downloaded PDF was not recognised"
    print("  OK  a downloaded certificate is recognised without the row field")


def main() -> int:
    tests = [
        test_html_error_page_is_not_saved_as_a_certificate,
        test_truncated_pdf_is_rejected_and_cleaned_up,
        test_incorporation_found_beyond_the_first_page,
        test_no_document_at_all_is_an_error,
        test_good_certificate_is_saved,
        test_dashboard_detects_the_file_the_downloader_writes,
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
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
