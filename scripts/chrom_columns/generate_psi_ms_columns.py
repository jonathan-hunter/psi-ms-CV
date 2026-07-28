#!/usr/bin/env python3
"""Generate psi-ms-columns.obo-fragment: model-level chromatographic column terms.

Reads the repo-rt column catalog (a TSV) and writes an OBO *fragment* -- [Term]
stanzas only, no header and no typedefs -- holding the vendor and model terms in
the MS:5000000 namespace:

    MS:5000000+  <vendor> chromatographic column model      (one per vendor)
      MS:5001000+  <product>                                (one per model)
        is_a <vendor> chromatographic column model
        is_a MS:1003921 ! liquid chromatographic column
        relationship: has_separation_mode ...               (when known)
        property_value: usp_designation "..." xsd:string    (per USP code)

The branch parent (MS:1004011 chromatographic column model) lives in psi-ms-core.obo,
as do every typedef and the `columns` subsetdef these stanzas use. The fragment is
deliberately not a standalone OBO document: scripts/build_release_obo.py splices it
into psi-ms-core.obo to produce the psi-ms.obo release artefact, and a header clause
appearing mid-file would be a syntax error there.

This generator does not correct or infer data — it only
verifies the input is valid UTF-8 and well-formed (correct field counts) and aborts
otherwise. Separation mode is read from the "mode" column and USP designation from
the "usp" column; each should be identical on every row of a model, so a field is
emitted only when every row agrees — any within-model disagreement emits nothing for
that field and is reported for upstream fixing (no majority guess is made).

Every term this fragment references but does not define is listed in
REQUIRED_CORE_TERMS and checked against psi-ms-core.obo before any output is written
(also available standalone via --check-core-refs, which CI runs on PRs touching core).

IDs are stable across runs: existing terms keep their id (read back from the
output file), and only genuinely new vendors/models get the next free id. This
keeps the auto-generated PRs minimal when repo-rt changes. (A model that is
renamed upstream, or that newly collides with another vendor's product and so
gains a disambiguating suffix, counts as a new term and is reassigned.)

The committed OBO is the sole authority for id assignment; repo-rt mirrors each
assigned id in a "psi_ms_id" catalog column. This generator also writes that
mapping to .github/repo-rt/psi-ms-column-ids.tsv (the contract repo-rt joins on to
back-propagate the ids) and, when the catalog carries a "psi_ms_id" column, cross-
checks it: a blank cell is a new model, a stable term whose mirror disagrees aborts
the run, and a new label carrying a stale id is reported as a likely rename.
--reset-ids ignores the existing OBO and assigns clean sequential ids (used once to
mint the initial baseline).

This fragment is versioned by Git and carries no data-version of its own; the release
version is psi-ms-core.obo's `data-version`, which the splice copies through.

Usage:
    python scripts/chrom_columns/generate_psi_ms_columns.py \\
        --input column_database.tsv --output psi-ms-columns.obo-fragment
    python scripts/chrom_columns/generate_psi_ms_columns.py --check-core-refs
"""

import argparse
import csv
import io
import os
import re
from collections import Counter, defaultdict

import pandas as pd

OUTPUT_DEFAULT = "psi-ms-columns.obo-fragment"
INPUT_DEFAULT = ".jeh-local/column_database_fixed.tsv"
MAPPING_DEFAULT = ".github/repo-rt/psi-ms-column-ids.tsv"
CORE_DEFAULT = "psi-ms-core.obo"

# ID bands within the MS:5000000 namespace (kept separate so the file stays
# grouped scaffold-then-leaves even as new terms are appended over time).
# The vendor band opens at MS:5000000: that id previously held the branch parent,
# which now lives in psi-ms-core.obo as MS:1004011. Note assign_ids is append-only
# by design -- a retired id must never be handed to a different term -- so 5000000
# is only reachable on a --reset-ids run, not by the next vendor to appear.
VENDOR_BAND = (5000000, 5000999)
LEAF_BAND = (5001000, 5999999)

# A regenerated catalog retaining fewer than this fraction of the prior model count
# is treated as a truncated/corrupt download and aborts (guarded in build_columns_obo).
MIN_RETAIN_FRACTION = 0.5

# Subset carried by every term in this fragment, so consumers can include or exclude the
# whole column branch. `subsetdef: columns` is declared once, in psi-ms-core.obo -- the
# fragment has no header to declare it in, and after the splice there is only one document.
SUBSET = "columns"

# The branch parent, plus every other term these stanzas reference but do not define.
# All live in psi-ms-core.obo. Checked before writing output (see check_core_refs): a
# deletion breaks the release, and a rename leaves `! label` comments contradicting the
# term they point at, which nothing downstream would flag.
PARENT_ID = "MS:1004011"
LIQUID_COLUMN = "MS:1003921"
REQUIRED_CORE_TERMS = {
    PARENT_ID: "chromatographic column model",
    LIQUID_COLUMN: "liquid chromatographic column",
    "MS:1003579": "ion-exchange chromatography",
    "MS:1003580": "size-exclusion chromatography",
    "MS:1003582": "reversed phase chromatography",
    "MS:1003583": "normal phase chromatography",
    "MS:1003584": "hydrophilic interaction liquid chromatography",
    "MS:1003586": "mixed mode chromatography",
}

# Separation-mode key -> (technique term id, label, definition adjective).
MODE_INFO = {
    "RP": ("MS:1003582", "reversed phase chromatography", "reversed-phase"),
    "NP": ("MS:1003583", "normal phase chromatography", "normal-phase"),
    "HILIC": ("MS:1003584", "hydrophilic interaction liquid chromatography", "HILIC"),
    "IEX": ("MS:1003579", "ion-exchange chromatography", "ion-exchange"),
    "SEC": ("MS:1003580", "size-exclusion chromatography", "size-exclusion"),
    "mixed": ("MS:1003586", "mixed mode chromatography", "mixed-mode"),
}

# repo-rt "mode" column value -> separation-mode key. 
# "other", "NA" and blank stay unmapped (no has_separation_mode emitted).
TSV_MODE = {
    "RP": "RP",
    "NP": "NP",
    "HILIC": "HILIC",
    "IEX": "IEX",
    "SEC": "SEC",
    "mixed-Mode": "mixed",
}

# --- reading the catalog ----------------------------------------------------

REQUIRED_COLUMNS = ("company", "column", "mode", "usp")


def clean(text):
    """Drop control characters that would break an OBO name/def line."""
    return "".join(ch for ch in text if ch >= " ")


def read_catalog(tsv_path):
    """Load the pre-cleaned repo-rt catalog into a DataFrame, keeping every cell as
    a literal string. The catalog must be valid UTF-8 (names and USP codes are
    corrected upstream); a stray non-UTF-8 byte means the input skipped that fix,
    so we abort rather than silently recover it. na_filter=False keeps 'NA' and
    blanks as text; fillna covers cells missing from a short row.

    An over-length row (more tab-separated fields than the header) ABORTS the run.
    A TSV has no quoting, so a tab count is authoritative; pandas would otherwise
    either treat the surplus field as a row index (silently shifting every column)
    or drop/truncate the row, and a vanished or shifted model would silently lose its
    stable id. Short rows are still tolerated (padded by fillna). index_col=False is
    belt-and-braces against the index-shift heuristic, and quoting=QUOTE_NONE keeps a
    stray double-quote from merging rows and matches the raw tab-count check above.

    Because QUOTE_NONE does no quote processing, a field an upstream export wrapped in
    double quotes (the CSV convention for a value containing a comma) keeps those quotes
    as literal characters. In the identity columns they would leak into term names and
    — since a quoted name differs from the previously assigned unquoted one — mint fresh
    ids and churn the mapping, so a wrapped-quote identity field ABORTS the run."""
    try:
        text = open(tsv_path, "rb").read().decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(
            f"{tsv_path} is not valid UTF-8 ({e}); the catalog must be pre-cleaned "
        )
    lines = text.split("\n")
    ncols = len(lines[0].split("\t")) if lines and lines[0] else 0
    overlong = [i for i, ln in enumerate(lines[1:], start=2)
                if ln != "" and len(ln.split("\t")) > ncols]
    if overlong:
        raise ValueError(
            f"{tsv_path} has over-length row(s) at line(s) "
            f"{overlong[:10]}{' ...' if len(overlong) > 10 else ''} "
            f"(more than {ncols} tab-separated fields); fix the field count upstream "
            "rather than letting the row be dropped or shifted"
        )
    df = pd.read_csv(
        io.StringIO(text), sep="\t", dtype=str, na_filter=False,
        index_col=False, quoting=csv.QUOTE_NONE,
    ).fillna("")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"catalog is missing required columns: {missing}")
    # CSV-quoting guard (see docstring): a proper tab-delimited catalog wraps no field
    # in quotes. Flag identity cells that arrive wrapped in a matched double-quote pair
    # so the quoting is stripped upstream rather than silently corrupting names/ids.
    quoted = sorted({
        f"{col}={cell!r}"
        for col in ("company", "column")
        for cell in df[col].str.strip()
        if len(cell) >= 2 and cell.startswith('"') and cell.endswith('"')
    })
    if quoted:
        raise ValueError(
            f"{tsv_path} has CSV-quoted identity field(s): "
            f"{quoted[:10]}{' ...' if len(quoted) > 10 else ''}; a tab-delimited catalog "
            "must not wrap fields in double quotes (strip the quoting upstream)"
        )
    return df


def load_models(tsv_path):
    """Return {(vendor, product): {"modes": Counter, "usps": Counter, "ms_ids": Counter}}.

    A catalog has many rows per model (one per physical size); grouping by
    (vendor, product) lets resolve_model vote over those rows. USP cells are
    normalized before counting so representational variants ("L1/L11" vs "L11/L1")
    aggregate instead of tying.

    product is the model name read straight from the catalog's "column" field (e.g.
    "ACE C18" for company "Advanced Chromatography Technologies") — the authoritative,
    vendor-stripped model label. (vendor, product) is the model's identity throughout:
    the grouping key, the leaf-label base, and the repo-rt mapping join key.
    """
    df = read_catalog(tsv_path)
    # psi_ms_id is an optional mirror column (absent on the first catalog); default to
    # an all-"" Series so the cross-check simply sees no ids rather than raising on a
    # KeyError. Both branches yield a Series (never a bare scalar) so the column is a
    # consistent type for static analysis as well as pandas' scalar broadcasting.
    if "psi_ms_id" in df.columns:
        ms_id = df["psi_ms_id"].str.strip()
    else:
        ms_id = pd.Series("", index=df.index, dtype=str)
    # Whitespace is trimmed with the vectorized .str.strip() accessor rather than a
    # per-element lambda: it is typed to return a string Series, so the callbacks below
    # never touch .strip() on a cell the checker widens to NAType (read_catalog's
    # dtype=str/na_filter=False guarantee a real str at runtime regardless).
    df = df.assign(
        vendor=df["company"].str.strip().map(clean),
        product=df["column"].str.strip().map(clean),
        # "" (not None) for unknown modes / no codes so the columns stay all-string
        # (a None would become a truthy NaN and slip past the `if` filters below).
        mode_key=df["mode"].str.strip().map(lambda m: TSV_MODE.get(str(m), "")),
        usp_canon=df["usp"].map(lambda u: "/".join(sorted(split_usp_codes(u), key=usp_sort_key))),
        ms_id=ms_id,
    )
    df = df[(df["vendor"] != "") & (df["product"] != "")]

    models = {}
    for (vendor, product), group in df.groupby(["vendor", "product"], sort=False):
        models[(vendor, product)] = {
            "modes": Counter(m for m in group["mode_key"] if m),
            "usps": Counter(u for u in group["usp_canon"] if u),
            "ms_ids": Counter(i for i in group["ms_id"] if i),
        }
    return models


# --- resolving a model's separation mode and USP designation ----------------

def split_usp_codes(cell):
    """Split a raw USP cell into atomic codes, e.g. 'L1/L11' or 'L20, L33'."""
    codes = [c.strip() for c in re.split(r"[/,]", cell)]
    return [c for c in codes if c and c != "NA"]


def usp_sort_key(code):
    # Numeric order so L9 sorts before L114 (plain string sort would invert them).
    digits = re.sub(r"\D", "", code)
    return int(digits) if digits else 0


def resolve_usp(usp_counter):
    """Resolve a model's USP designation from its per-row cells.

    A column model should carry the same USP code (or combined cell) on every row, so
    this returns (codes, deviated): when every row agrees, codes is that single value
    and deviated is False; when the rows disagree at all, there is no trustworthy value
    so codes is empty and deviated is True, flagging the model for an upstream fix. No
    majority vote is taken — any inconsistency is surfaced rather than guessed through.
    """
    if len(usp_counter) == 1:
        (value,) = usp_counter
        return sorted(split_usp_codes(value), key=usp_sort_key), False
    return [], len(usp_counter) > 1


def resolve_mode(mode_counter):
    """Resolve a model's separation mode from its per-row cells, like resolve_usp.

    Returns (mode_key or None, deviated): a single agreed value is emitted, any
    disagreement emits nothing and sets deviated for the report, and no-data emits
    nothing without flagging."""
    if len(mode_counter) == 1:
        (value,) = mode_counter
        return value, False
    return None, len(mode_counter) > 1


def resolve_model(entry):
    """Resolve a model to (mode, usp_literals, deviations).

    mode          separation-mode key for has_separation_mode, or None.
    usp_literals  USP codes to emit as usp_designation (empty when unresolved).
    deviations    {field: {value: count}} for every field (usp, mode) whose rows do
                  not agree, surfaced in the report so the inconsistency is fixed
                  upstream. A field whose rows disagree emits nothing (no majority
                  guess); only a field on which every row agrees is emitted.
    """
    codes, usp_deviated = resolve_usp(entry["usps"])
    mode, mode_deviated = resolve_mode(entry["modes"])
    deviations = {}
    if usp_deviated:
        deviations["usp"] = dict(entry["usps"])
    if mode_deviated:
        deviations["mode"] = dict(entry["modes"])
    return mode, codes, deviations


# --- naming ------------------------------------------------------------------

def colliding_names(models):
    """Model names (the catalog "column" field) shared by more than one vendor and so
    needing vendor disambiguation in the leaf label."""
    vendors_by_name = defaultdict(set)
    for vendor, product in models:
        vendors_by_name[product].add(vendor)
    return {name for name, vendors in vendors_by_name.items() if len(vendors) > 1}


def leaf_label(product, vendor, colliding):
    """Leaf term label: the model name, suffixed with the vendor only when that name is
    shared across vendors (so the emitted labels stay unique)."""
    return f"{product} ({vendor})" if product in colliding else product


def with_period(sentence):
    """End a sentence with a period unless it already does (vendors ending 'Inc.')."""
    return sentence if sentence.endswith(".") else sentence + "."


def escape_def(text):
    """Escape a value for an OBO quoted def: string (upstream names may contain " or \\)."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def escape_tag(text):
    """Escape a value for an unquoted OBO tag line (name:). Backslash is the OBO escape
    character, so it must be doubled; clean() already removes the newlines/tabs that
    would otherwise need escaping in an unquoted value. Without this a trailing
    backslash folds the next line into the name and a mid-string one deletes a char."""
    return text.replace("\\", "\\\\")


def leaf_definition(vendor, mode):
    if not mode:
        return with_period(f"A liquid chromatographic column model manufactured by {vendor}")
    adjective = MODE_INFO[mode][2]
    article = "An" if adjective[0].lower() in "aeiou" else "A"
    return with_period(
        f"{article} {adjective} liquid chromatographic column model manufactured by {vendor}"
    )


# --- stanza builders (return text, no trailing blank line) ------------------

def vendor_stanza(vendor, vendor_id):
    definition = with_period(f"Chromatographic column model manufactured by {vendor}")
    lines = [
        "[Term]",
        f"id: {vendor_id}",
        f"name: {escape_tag(vendor)} chromatographic column model",
        f'def: "{escape_def(definition)}" [PSI:MS]',
        f"subset: {SUBSET}",
        f"is_a: {PARENT_ID} ! chromatographic column model",
    ]
    return "\n".join(lines)


def leaf_stanza(leaf_id, vendor, vendor_id, label, mode, usp_literals):
    lines = [
        "[Term]",
        f"id: {leaf_id}",
        f"name: {escape_tag(label)}",
        f'def: "{escape_def(leaf_definition(vendor, mode))}" [PSI:MS]',
        f"subset: {SUBSET}",
        f"is_a: {vendor_id} ! {vendor} chromatographic column model",
        f"is_a: {LIQUID_COLUMN} ! liquid chromatographic column",
    ]
    if mode:
        mode_id, mode_label, _ = MODE_INFO[mode]
        lines.append(f"relationship: has_separation_mode {mode_id} ! {mode_label}")
    for code in usp_literals:
        lines.append(f'property_value: usp_designation "{escape_def(code)}" xsd:string')
    return "\n".join(lines)


# --- cross-file reference check ----------------------------------------------

def check_core_refs(core_path):
    """Verify every REQUIRED_CORE_TERMS id is defined in core under the expected name.

    The fragment references these but cannot define them, and no per-file validator can
    see the mismatch: the fragment referencing core is legitimate by design. Names are
    compared as well as ids because a rename is the likelier accident -- the id still
    resolves after one, so the merged release ships silently wrong `! label` comments.

    Raises ValueError listing every problem, rather than stopping at the first.
    """
    names = {}
    current = None
    with open(core_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("id: "):
                current = line[len("id: "):].strip()
            elif line.startswith("name: ") and current:
                names[current] = line[len("name: "):].strip()
                current = None

    problems = []
    for term_id, expected in sorted(REQUIRED_CORE_TERMS.items()):
        actual = names.get(term_id)
        if actual is None:
            problems.append(f"{term_id} is not defined in {core_path} (expected {expected!r})")
        elif actual != expected:
            problems.append(f"{term_id} is named {actual!r} in {core_path}, expected {expected!r}")
    if problems:
        raise ValueError(
            "psi-ms-core.obo no longer provides the terms this fragment references:\n  "
            + "\n  ".join(problems)
        )
    return len(REQUIRED_CORE_TERMS)


# --- stable id assignment ----------------------------------------------------

def read_existing_ids(path):
    """Map term name -> existing MS id from a prior generation (for stable ids)."""
    ids = {}
    if not os.path.exists(path):
        return ids
    current = None
    for line in open(path, encoding="utf-8"):
        if line.startswith("id: MS:"):
            current = line[len("id: "):].strip()
        elif line.startswith("name: ") and current:
            ids[line[len("name: "):].strip()] = current
            current = None
    return ids


def assign_ids(names, existing, band):
    """Assign ids in [lo, hi]: reuse an existing id by name, else the next free id
    above the highest already used in the band (so existing ids never move)."""
    lo, hi = band
    used = [int(i.split(":")[1]) for i in existing.values()]
    used = [n for n in used if lo <= n <= hi]
    nxt = max(used) + 1 if used else lo
    result = {}
    for name in names:
        if name in existing:
            reused = int(existing[name].split(":")[1])
            if not (lo <= reused <= hi):  # e.g. a leaf label that equals a vendor name
                raise RuntimeError(
                    f"existing id {existing[name]} for {name!r} is outside band {band}"
                )
            result[name] = existing[name]
        else:
            if nxt > hi:  # fail loudly rather than mint a colliding out-of-band id
                raise RuntimeError(f"id band {band} exhausted; widen it")
            result[name] = f"MS:{nxt}"
            nxt += 1
    return result


# --- writing -----------------------------------------------------------------

def cross_check_mirror(models, labels, leaf_ids, existing):
    """Validate the catalog's psi_ms_id mirror against the ids just assigned.

    Returns rename warnings (a new model whose rows still carry a stale id) and
    raises ValueError on genuine drift so the sync aborts before opening a PR:
      - blank cell                -> new model / not yet back-propagated (ignored)
      - id matches assigned       -> mirror agrees (ignored)
      - stable term, id differs   -> drift, abort
      - rows of a model disagree  -> corrupt mirror, abort
      - new label, id differs     -> likely rename, warn (new id back-propagates)
    """
    drift, row_conflicts, renames = [], [], []
    for vp, entry in models.items():
        assigned = leaf_ids[labels[vp]]
        col_ids = set(entry["ms_ids"])
        if not col_ids:
            continue
        if len(col_ids) > 1:
            row_conflicts.append((vp[1], sorted(col_ids)))
            continue
        col_id = col_ids.pop()
        if col_id == assigned:
            continue
        (drift if labels[vp] in existing else renames).append((vp[1], col_id, assigned))
    if drift or row_conflicts:
        lines = ["psi_ms_id mirror disagrees with assigned ids (aborting sync):"]
        lines += [f"  drift: {p!r} column={c} assigned={a}" for p, c, a in drift]
        lines += [f"  conflicting ids within model {p!r}: {ids}" for p, ids in row_conflicts]
        raise ValueError("\n".join(lines))
    return renames


def build_mapping_tsv(models, labels, leaf_ids):
    """Return the (company, column, psi_ms_id) mapping text -- the cross-repo contract
    repo-rt joins on (its add_psi_ms_id.py must join on the same company + column keys).
    Keys are the cleaned company / column; clean() drops the C0 control chars (including
    tab and newline), so no value can contain a TSV delimiter and the rows need no
    quoting."""
    rows = sorted(
        (vendor, product, leaf_ids[labels[(vendor, product)]]) for vendor, product in models
    )
    lines = ["company\tcolumn\tpsi_ms_id"]
    lines += [f"{c}\t{n}\t{i}" for c, n, i in rows]
    return "\n".join(lines) + "\n"


def build_columns_obo(models, existing):
    """Return (obo_text, mapping_text, report). Terms are emitted in id order so the
    file stays sorted and stable; report holds the run summary for logs and main()."""
    if not models:
        raise ValueError("catalog produced 0 column models; refusing to write an empty module")
    prior_leaves = sum(1 for i in existing.values()
                       if LEAF_BAND[0] <= int(i.split(":")[1]) <= LEAF_BAND[1])
    if prior_leaves and len(models) < prior_leaves * MIN_RETAIN_FRACTION:
        raise ValueError(
            f"catalog shrank from {prior_leaves} to {len(models)} models "
            f"(<{MIN_RETAIN_FRACTION:.0%} retained); aborting as a likely truncated "
            "download. Re-run with --reset-ids if this drop is intentional."
        )

    colliding = colliding_names(models)
    vendors = sorted({vendor for vendor, _ in models})

    vendor_ids = assign_ids(
        [f"{v} chromatographic column model" for v in vendors], existing, VENDOR_BAND
    )
    vendor_id = {v: vendor_ids[f"{v} chromatographic column model"] for v in vendors}

    labels = {(v, p): leaf_label(p, v, colliding) for v, p in models}
    # Each emitted leaf needs a unique label (and thus id). leaf_label only
    # disambiguates names shared ACROSS vendors, so guard against two products of
    # one vendor reducing to the same label (e.g. an upstream whitespace variant),
    # which would otherwise emit duplicate OBO terms. Fail loudly to fix upstream.
    dupes = sorted(lbl for lbl, n in Counter(labels.values()).items() if n > 1)
    if dupes:
        raise ValueError(f"non-unique leaf labels (fix in the upstream catalog): {dupes}")
    leaf_ids = assign_ids(sorted(labels.values()), existing, LEAF_BAND)

    # Defence in depth: assign_ids mints unique ids by construction, but a corrupt
    # committed OBO (two names sharing one id) would be reused faithfully. Refuse to
    # emit a duplicate-id module rather than ship one in an auto-PR.
    id_counts = Counter([*vendor_id.values(), *leaf_ids.values()])
    dup_ids = sorted(i for i, n in id_counts.items() if n > 1)
    if dup_ids:
        raise ValueError(f"duplicate ids generated (corrupt existing-id map?): {dup_ids}")

    renames = cross_check_mirror(models, labels, leaf_ids, existing)

    stanzas = []
    for vendor in vendors:
        vid = vendor_id[vendor]
        stanzas.append((int(vid.split(":")[1]), vendor_stanza(vendor, vid)))

    report = {"deviations": [], "renames": renames}
    for (vendor, product), entry in models.items():
        mode, usp_literals, deviations = resolve_model(entry)
        label = labels[(vendor, product)]
        lid = leaf_ids[label]
        stanzas.append(
            (int(lid.split(":")[1]),
             leaf_stanza(lid, vendor, vendor_id[vendor], label, mode, usp_literals))
        )
        if deviations:  # rows of this model disagree on usp/mode — flag for upstream fix
            report["deviations"].append((product, deviations))

    stanzas.sort(key=lambda pair: pair[0])
    body_block = "\n\n".join(text for _, text in stanzas) + "\n"
    return body_block, build_mapping_tsv(models, labels, leaf_ids), report


def print_report(models, report):
    print(f"vendors: {len({v for v, _ in models})}   models: {len(models)}")
    if report["renames"]:
        print(f'\npsi_ms_id renames (new id minted, back-propagates next cycle) — {len(report["renames"])}:')
        for product, old, new in report["renames"]:
            print(f"  {product}  column={old} -> assigned={new}")
    if report["deviations"]:
        print(f'\nwithin-model value deviations (fix upstream) — {len(report["deviations"])}:')
        for product, dev in report["deviations"]:
            for field, counts in dev.items():
                print(f"  {product}  {field}={counts}")


def report_markdown(models, report):
    """Markdown data-quality summary for the sync PR body (deviations + renames)."""
    lines = [f"- vendors: {len({v for v, _ in models})}", f"- models: {len(models)}", ""]
    if report["deviations"]:
        lines.append(f'### Within-model value deviations (fix upstream) — {len(report["deviations"])}')
        for product, dev in report["deviations"]:
            for field, counts in dev.items():
                lines.append(f"- `{product}` — {field}={counts}")
        lines.append("")
    if report["renames"]:
        lines.append(f'### psi_ms_id renames (new id minted, back-propagates next cycle) — {len(report["renames"])}')
        for product, old, new in report["renames"]:
            lines.append(f"- `{product}` — column={old} → assigned={new}")
        lines.append("")
    if not report["deviations"] and not report["renames"]:
        lines.append("No within-model deviations or renames.")
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", default=INPUT_DEFAULT, help="repo-rt column TSV")
    parser.add_argument("--output", default=OUTPUT_DEFAULT, help="OBO module to write")
    parser.add_argument("--mapping", default=MAPPING_DEFAULT,
                        help="company/column -> psi_ms_id TSV (repo-rt back-prop contract)")
    parser.add_argument("--reset-ids", action="store_true",
                        help="ignore existing ids in --output and assign clean sequential ids")
    parser.add_argument("--report", help="write a Markdown data-quality summary to this path")
    parser.add_argument("--core", default=CORE_DEFAULT,
                        help="psi-ms-core.obo, checked for the terms this fragment references")
    parser.add_argument("--check-core-refs", action="store_true",
                        help="only verify --core provides REQUIRED_CORE_TERMS, then exit")
    args = parser.parse_args()

    if args.check_core_refs:
        n = check_core_refs(args.core)
        print(f"{args.core}: all {n} referenced terms present with expected names")
        return

    check_core_refs(args.core)
    models = load_models(args.input)
    if args.reset_ids:
        existing = {}
    else:
        existing = read_existing_ids(args.output)  # read before we overwrite it
    obo_text, mapping_text, report = build_columns_obo(models, existing)
    open(args.output, "w", encoding="utf-8").write(obo_text)
    print(f"wrote {args.output}")

    if os.path.dirname(args.mapping):
        os.makedirs(os.path.dirname(args.mapping), exist_ok=True)
    open(args.mapping, "w", encoding="utf-8").write(mapping_text)
    print(f"wrote {args.mapping}")

    if args.report:
        open(args.report, "w", encoding="utf-8").write(report_markdown(models, report))
        print(f"wrote {args.report}")

    print_report(models, report)


if __name__ == "__main__":
    main()
