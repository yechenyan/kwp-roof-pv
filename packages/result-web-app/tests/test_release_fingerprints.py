"""Prevent publishing mixed-build PDFs, web data and GIS files."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import RANKING_INDEX, REPORT_INPUTS, VERIFIED_BUILD_FILES, digest, read, write
from package_delivery import REPORTS, release_fingerprints


@pytest.fixture
def verified_build(tmp_path):
    files = {}
    for name in VERIFIED_BUILD_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == RANKING_INDEX:
            write(path, {"shards": []})
        else:
            path.write_text(name)
        files[name] = digest(path)
    write(tmp_path / "verification.json", {"building_evidence_and_postcode_reconciliation": True,
                                           "build_files_sha256": files})
    for name in REPORTS:
        path = tmp_path / name
        path.write_text(name)
        write(path.with_suffix(".provenance.json"), {"inputs_sha256": {key: files[key] for key in REPORT_INPUTS},
                                                    "pdf_sha256": digest(path)})
    return tmp_path


def test_verified_reports_and_data_can_publish(verified_build):
    _, reports = release_fingerprints(verified_build)
    assert set(reports) == set(REPORTS)


def test_replaced_pdf_cannot_publish_with_new_geo_data(verified_build):
    (verified_build / REPORTS[0]).write_text("Older report")
    with pytest.raises(ValueError, match="Report differs from the verified build"):
        release_fingerprints(verified_build)


def test_individually_valid_report_from_different_build_is_rejected(verified_build):
    path = (verified_build / REPORTS[1]).with_suffix(".provenance.json")
    report = read(path)
    report["inputs_sha256"]["summary.json"] = "different build"
    write(path, report)
    with pytest.raises(ValueError, match="Report differs from the verified build"):
        release_fingerprints(verified_build)


def test_web_file_changed_after_verification_is_rejected(verified_build):
    (verified_build / "postcode-totals.json").write_text("Older postcode data")
    with pytest.raises(ValueError, match="Build file differs from verification: postcode-totals.json"):
        release_fingerprints(verified_build)
