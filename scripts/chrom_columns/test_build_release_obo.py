"""Tests for scripts/chrom_columns/build_release_obo.py.

Run: uv run --with fastobo --with pytest python -m pytest scripts/chrom_columns/ -q
"""
import pathlib
import re
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_release_obo as bro  # noqa: E402


HEADER = "format-version: 1.2\ndata-version: 4.1.258\nsaved-by: X\nontology: ms\n"

# A base with the two column typedefs already present (the real master state), MS terms
# through the QC block, then a PEFF term — mirroring psi-ms.obo's prefix layout.
BASE = (
    HEADER + "\n"
    "[Typedef]\nid: part_of\nname: part_of\n\n"
    "[Typedef]\nid: has_separation_mode\nname: has_separation_mode\n\n"
    "[Typedef]\nid: usp_designation\nname: usp_designation\n\n"
    "[Term]\nid: MS:1000001\nname: alpha\n\n"
    "[Term]\nid: MS:4000215\nname: omega\n\n"
    "[Term]\nid: PEFF:0000001\nname: peff one\n"
)

MODULE = (
    "format-version: 1.2\ndata-version: 1.0.001\nontology: ms-columns\n\n"
    "[Typedef]\nid: has_separation_mode\nname: has_separation_mode\n\n"
    "[Typedef]\nid: usp_designation\nname: usp_designation\n\n"
    "[Term]\nid: MS:5000000\nname: chromatographic column model\n\n"
    "[Term]\nid: MS:5000001\nname: Acme chromatographic column model\n"
)


def ids(text):
    return re.findall(r"(?m)^id: (\S+)", text)


def test_terms_inserted_after_last_ms_before_peff():
    out = bro.build_release_obo(BASE, MODULE)
    assert ids(out) == [
        "part_of", "has_separation_mode", "usp_designation",
        "MS:1000001", "MS:4000215", "MS:5000000", "MS:5000001", "PEFF:0000001",
    ]


def test_base_preserved_byte_for_byte_except_insertion():
    out = bro.build_release_obo(BASE, MODULE)
    inserted = (
        "[Term]\nid: MS:5000000\nname: chromatographic column model\n\n"
        "[Term]\nid: MS:5000001\nname: Acme chromatographic column model\n\n"
    )
    # Splicing the module terms in front of the first PEFF stanza must reproduce the
    # base exactly everywhere else (header, typedefs, existing terms, spacing).
    assert out == BASE.replace(
        "[Term]\nid: PEFF:0000001", inserted + "[Term]\nid: PEFF:0000001"
    )


def test_typedef_injected_when_base_missing_it():
    base_missing = (
        HEADER + "\n"
        "[Typedef]\nid: part_of\nname: part_of\n\n"
        "[Term]\nid: MS:4000215\nname: omega\n\n"
        "[Term]\nid: PEFF:0000001\nname: p\n"
    )
    out = bro.build_release_obo(base_missing, MODULE)
    assert out.count("id: has_separation_mode") == 1  # injected exactly once
    assert out.count("id: usp_designation") == 1
    # injected into the typedef block, before any [Term]
    assert out.index("id: has_separation_mode") < out.index("[Term]")


def test_no_duplicate_typedef_when_base_has_it():
    out = bro.build_release_obo(BASE, MODULE)
    assert out.count("id: has_separation_mode") == 1
    assert out.count("id: usp_designation") == 1


def test_base_without_ms_terms_aborts():
    base = HEADER + "\n[Term]\nid: PEFF:0000001\nname: p\n"
    with pytest.raises(ValueError, match="no MS: term"):
        bro.build_release_obo(base, MODULE)


def test_integration_real_files_parse_and_sorted(tmp_path):
    import fastobo

    base = (ROOT / "psi-ms.obo").read_text(encoding="utf-8")
    module = (ROOT / "psi-ms-columns.obo").read_text(encoding="utf-8")
    out = bro.build_release_obo(base, module)

    merged = tmp_path / "psi-ms.obo"
    merged.write_text(out, encoding="utf-8")
    fastobo.load(str(merged))                              # valid OBO
    assert "\nid: MS:5000000\n" in out                     # column parent present
    assert "\nid: MS:5000001\n" in out                     # a vendor term present

    # ordering still passes the repo's sort check
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_sorted.py"), str(merged)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
