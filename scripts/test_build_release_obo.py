"""Tests for scripts/build_release_obo.py.

Run: uv run --with pytest python -m pytest scripts/ -q
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_release_obo as build  # noqa: E402


CORE = """\
format-version: 1.2
data-version: 4.1.259
ontology: ms

[Typedef]
id: part_of
xref: BFO:0000050

[Term]
id: MS:1000001
name: first

[Term]
id: MS:4000215
name: last MS term

[Term]
id: PEFF:0000001
name: peff term

[Term]
id: UO:0000000
name: unit
"""

FRAGMENT = """\
[Term]
id: MS:5000000
name: Acme chromatographic column model

[Term]
id: MS:5001000
name: C18
"""


def test_fragment_lands_between_the_ms_and_peff_blocks():
    merged = build.splice(CORE, FRAGMENT)
    order = [line for line in merged.splitlines() if line.startswith("id: ")]
    assert order == [
        "id: part_of",
        "id: MS:1000001",
        "id: MS:4000215",
        "id: MS:5000000",
        "id: MS:5001000",
        "id: PEFF:0000001",
        "id: UO:0000000",
    ]


def test_splice_is_a_pure_insertion():
    # Core's bytes either side of the seam must be untouched: the released psi-ms.obo is
    # byte-stable only if the build never reformats what it copies.
    merged = build.splice(CORE, FRAGMENT)
    kept = [b for b in merged.split("\n\n") if "\nid: MS:5" not in "\n" + b]
    assert "\n\n".join(kept) == CORE


def test_splice_is_deterministic():
    assert build.splice(CORE, FRAGMENT) == build.splice(CORE, FRAGMENT)


def test_stanzas_stay_blank_line_separated():
    merged = build.splice(CORE, FRAGMENT)
    assert "\n\n[Term]\nid: MS:5000000\n" in merged
    assert "name: C18\n\n[Term]\nid: PEFF:0000001\n" in merged
    assert "\n\n\n" not in merged


def test_ms_block_at_end_of_file_appends():
    core = "format-version: 1.2\n\n[Term]\nid: MS:4000215\nname: last\n"
    merged = build.splice(core, FRAGMENT)
    assert merged.endswith("name: C18\n")
    assert merged.startswith(core.rstrip("\n"))


def test_core_without_an_ms_stanza_is_an_error():
    with pytest.raises(ValueError, match="no 'id: MS:...' stanza"):
        build.splice("format-version: 1.2\n\n[Term]\nid: PEFF:0000001\n", FRAGMENT)


def test_header_clause_in_the_fragment_is_rejected():
    # The failure this prevents is a syntax error in the merged release, because a header
    # clause is only legal before the first stanza.
    with pytest.raises(ValueError, match="stanzas only"):
        build.check_fragment("format-version: 1.2\n\n" + FRAGMENT, "f.obo")


def test_empty_fragment_is_rejected():
    with pytest.raises(ValueError, match="is empty"):
        build.check_fragment("\n\n", "f.obo")


def test_check_fragment_counts_stanzas():
    assert build.check_fragment(FRAGMENT, "f.obo") == 2
