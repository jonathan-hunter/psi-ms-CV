"""Unit + integration tests for scripts/chrom_columns/generate_psi_ms_columns.py.

Run: uv run --with pandas --with fastobo --with pytest python -m pytest scripts/chrom_columns/ -q
"""
import pathlib
import sys
from collections import Counter

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import generate_psi_ms_columns as gen  # noqa: E402


def m(modes=(), usps=(), ms_ids=()):
    return {"modes": Counter(modes), "usps": Counter(usps), "ms_ids": Counter(ms_ids)}


def write_tsv(path, rows, header="company\tcolumn\tmode\tusp"):
    path.write_text(header + "\n" + "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")


# --- small pure helpers ------------------------------------------------------

def test_escape_tag_doubles_backslash():
    assert gen.escape_tag("a\\b") == "a\\\\b"
    assert gen.escape_tag("plain") == "plain"


def test_escape_def_backslash_and_quote():
    assert gen.escape_def('a"b\\c') == 'a\\"b\\\\c'


def test_component_header_has_identity_but_no_release_version():
    header = gen.build_header()
    assert "ontology: http://purl.obolibrary.org/obo/ms/components/psi-ms-columns.owl" in header
    assert "idspace: MSREL http://purl.obolibrary.org/obo/ms#" in header
    assert "data-version:" not in header


def test_leaf_label_collision_suffix():
    # Model identity is (vendor, column); a model name shared across vendors collides
    # and gets a vendor suffix in its leaf label, a unique one does not.
    models = {("VendorA", "C18"): m(), ("VendorB", "C18"): m()}
    colliding = gen.colliding_names(models)
    assert "C18" in colliding
    assert gen.leaf_label("C18", "VendorA", colliding) == "C18 (VendorA)"
    assert gen.leaf_label("C8", "VendorA", colliding) == "C8"  # unique -> no suffix


# --- resolution & deviation flagging ----------------------------------------

def test_resolve_usp_agree_or_flag():
    assert gen.resolve_usp(Counter()) == ([], False)                       # no data -> nothing, no flag
    assert gen.resolve_usp(Counter({"L1": 3})) == (["L1"], False)          # all agree -> emit
    assert gen.resolve_usp(Counter({"L1": 3, "L7": 1})) == ([], True)      # any disagreement -> omit + flag
    assert gen.resolve_usp(Counter({"L1": 1, "L7": 1})) == ([], True)      # tie -> omit + flag


def test_resolve_mode_agree_or_flag():
    assert gen.resolve_mode(Counter()) == (None, False)                    # no data -> nothing, no flag
    assert gen.resolve_mode(Counter({"RP": 3})) == ("RP", False)           # all agree -> emit
    assert gen.resolve_mode(Counter({"HILIC": 13, "RP": 1})) == (None, True)  # any disagreement -> omit + flag
    assert gen.resolve_mode(Counter({"RP": 1, "HILIC": 1})) == (None, True)   # tie -> omit + flag


def test_resolve_model_reports_deviations():
    mode, codes, dev = gen.resolve_model(m(modes=["RP", "RP"], usps=["L1", "L1"]))
    assert (mode, codes, dev) == ("RP", ["L1"], {})
    mode, _, dev = gen.resolve_model(m(modes=["RP", "HILIC", "HILIC"], usps=["L1", "L1", "L1"]))
    assert mode is None and "mode" in dev and "usp" not in dev   # disagreement -> omit + flag
    mode, codes, dev = gen.resolve_model(m(modes=["RP"], usps=["L1", "L7"]))
    assert codes == [] and "usp" in dev   # disagreement -> omit + flag


# --- id assignment -----------------------------------------------------------

def test_assign_ids_mint_and_reuse():
    assert gen.assign_ids(["a", "b"], {}, (10, 20)) == {"a": "MS:10", "b": "MS:11"}
    assert gen.assign_ids(["a", "b"], {"a": "MS:15"}, (10, 20)) == {"a": "MS:15", "b": "MS:16"}


def test_assign_ids_band_exhausted():
    with pytest.raises(RuntimeError, match="exhausted"):
        gen.assign_ids(["a", "b"], {}, (10, 10))


def test_assign_ids_out_of_band_reuse_raises():
    with pytest.raises(RuntimeError, match="outside band"):
        gen.assign_ids(["a"], {"a": "MS:5"}, (10, 20))


# --- mirror cross-check ------------------------------------------------------

def _mirror(ms_ids, existing):
    models = {("V", "A"): m(ms_ids=ms_ids)}
    return gen.cross_check_mirror(models, {("V", "A"): "A"}, {"A": "MS:5001000"}, existing)


def test_mirror_blank_and_agree_pass():
    assert _mirror([], {}) == []
    assert _mirror(["MS:5001000", "MS:5001000"], {"A": "MS:5001000"}) == []


def test_mirror_rename_warns_not_aborts():
    assert _mirror(["MS:5008888"], {}) == [("A", "MS:5008888", "MS:5001000")]


def test_mirror_drift_aborts():
    with pytest.raises(ValueError, match="drift"):
        _mirror(["MS:5008888"], {"A": "MS:5001000"})


def test_mirror_row_conflict_aborts():
    with pytest.raises(ValueError, match="conflicting"):
        _mirror(["MS:1", "MS:2"], {})


# --- read_catalog ------------------------------------------------------------

def test_read_catalog_overlength_aborts(tmp_path):
    p = tmp_path / "c.tsv"
    write_tsv(p, [["A", "X", "RP", "L1", "EXTRA"]])
    with pytest.raises(ValueError, match="over-length"):
        gen.read_catalog(str(p))


def test_read_catalog_short_row_padded(tmp_path):
    p = tmp_path / "c.tsv"
    p.write_text("company\tcolumn\tmode\tusp\nA\tX\tRP\n", encoding="utf-8")
    df = gen.read_catalog(str(p))
    assert df.iloc[0]["usp"] == ""


def test_read_catalog_missing_column_aborts(tmp_path):
    p = tmp_path / "c.tsv"
    p.write_text("company\tcolumn\tmode\nA\tX\tRP\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        gen.read_catalog(str(p))


def test_read_catalog_csv_quoted_identity_aborts(tmp_path):
    # A comma-containing field an upstream export wrapped CSV-style; QUOTE_NONE keeps
    # the quotes literal, so guard against them leaking into names/ids.
    p = tmp_path / "c.tsv"
    p.write_text('company\tcolumn\tmode\tusp\n"Acme, Inc"\tC18\tRP\tL1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="CSV-quoted"):
        gen.read_catalog(str(p))
    # a quoted model name (column field) is caught too
    p2 = tmp_path / "c2.tsv"
    p2.write_text('company\tcolumn\tmode\tusp\nAcme\t"C18, wide"\tRP\tL1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="CSV-quoted"):
        gen.read_catalog(str(p2))


# --- build_columns_obo: floors and duplicate ids -----------------------------

def test_build_zero_models_aborts():
    with pytest.raises(ValueError, match="0 column models"):
        gen.build_columns_obo({}, {})


def test_build_shrink_floor_aborts():
    existing = {f"leaf{i}": f"MS:{5001000 + i}" for i in range(100)}
    models = {("Acme", "C18"): m(modes=["RP"], usps=["L1"])}
    with pytest.raises(ValueError, match="shrank"):
        gen.build_columns_obo(models, existing)


def test_build_duplicate_id_aborts():
    models = {("Acme", "C18"): m(modes=["RP"], usps=["L1"]),
              ("Acme", "C8"): m(modes=["RP"], usps=["L7"])}
    existing = {"C18": "MS:5001000", "C8": "MS:5001000",
                "Acme chromatographic column model": "MS:5000001"}
    with pytest.raises(ValueError, match="duplicate ids"):
        gen.build_columns_obo(models, existing)


# --- integration -------------------------------------------------------------

def test_integration_loads_stable_and_renames(tmp_path):
    import fastobo

    p = tmp_path / "c.tsv"
    write_tsv(p, [["Acme", "C18", "RP", "L1"], ["Acme", "C8", "HILIC", "L114"]])
    models = gen.load_models(str(p))
    obo, mapping, report = gen.build_columns_obo(models, {})

    out = tmp_path / "o.obo"
    out.write_text(obo, encoding="utf-8")
    fastobo.load(str(out))                                   # valid OBO
    assert mapping.startswith("company\tcolumn\tpsi_ms_id\n")
    assert "Acme\tC18\t" in mapping                          # join key is (company, column)

    existing = gen.read_existing_ids(str(out))
    obo2, _, _ = gen.build_columns_obo(models, existing)
    assert obo2 == obo                                       # stable ids -> identical

    # rename C18 -> C19: new label mints the next free id, old id retired
    p2 = tmp_path / "c2.tsv"
    write_tsv(p2, [["Acme", "C19", "RP", "L1"], ["Acme", "C8", "HILIC", "L114"]])
    obo3, _, _ = gen.build_columns_obo(gen.load_models(str(p2)), existing)
    out3 = tmp_path / "o3.obo"
    out3.write_text(obo3, encoding="utf-8")
    ids = gen.read_existing_ids(str(out3))
    assert "C19" in ids and "C18" not in ids
    assert ids["C8"] == existing["C8"]                       # untouched model keeps id
    assert int(ids["C19"].split(":")[1]) > int(existing["C18"].split(":")[1])


def test_integration_backslash_name_escaped(tmp_path):
    import fastobo

    p = tmp_path / "c.tsv"
    write_tsv(p, [["Acme", "C18\\", "RP", "L1"]])
    obo, _, _ = gen.build_columns_obo(gen.load_models(str(p)), {})
    out = tmp_path / "o.obo"
    out.write_text(obo, encoding="utf-8")
    fastobo.load(str(out))                                   # no fold / no crash
    assert "name: C18\\\\" in obo
    # the def clause survived (one per term: parent + vendor + leaf)
    assert obo.count('def: "') == 3
