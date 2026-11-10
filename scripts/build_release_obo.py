#!/usr/bin/env python3
"""Splice psi-ms-columns.obo-fragment into psi-ms-core.obo to build psi-ms.obo.

The release also gets generated "<vendor> <model>" EXACT synonyms (issue #602), so a
search for e.g. "Thermo Scientific Q Exactive" or "Waters ACQUITY UPLC BEH C18" finds
the model term even though PSI-MS model names leave the vendor out. They are added
only to the built psi-ms.obo; the sources stay free of derived data.

Usage:
    python scripts/build_release_obo.py [--core psi-ms-core.obo]
        [--fragment psi-ms-columns.obo-fragment] [--output psi-ms.obo]
"""

import argparse
import re
import sys

CORE_DEFAULT = "psi-ms-core.obo"
FRAGMENT_DEFAULT = "psi-ms-columns.obo-fragment"
OUTPUT_DEFAULT = "psi-ms.obo"

MS_ID_LINE = re.compile(r"^id: MS:\S+$", re.MULTILINE)

# Model hierarchies whose vendor classes are named "<vendor> <root name>", e.g.
# "Waters instrument model" under "instrument model". Every term below a vendor class
# gets a "<vendor> <name>" synonym.
VENDOR_MODEL_ROOTS = (
    "MS:1000031",  # instrument model
    "MS:1003605",  # liquid chromatography system model
    "MS:1003730",  # gas chromatography system model
    "MS:1004042",  # capillary electrophoresis system model
    "MS:1004013",  # chromatographic column model
)

# OBO 1.4 clause order: a synonym goes after these and before xref, is_a, etc.
PRE_SYNONYM_TAGS = frozenset(
    {"id", "is_anonymous", "name", "namespace", "alt_id", "def", "comment", "subset", "synonym"}
)
TOKEN = re.compile(r"[\w+.&]+")


def check_fragment(fragment_text, fragment_path):
    """The fragment must be stanzas only. A header clause here would land mid-file and
    every OBO parser would reject the result ('expected EOI, Comment, or TermClause').
    Full OBO syntax validation is performed downstream by fastobo."""
    blocks = [b for b in fragment_text.split("\n\n") if b.strip()]
    if not blocks:
        raise ValueError(f"{fragment_path} is empty")
    for block in blocks:
        if not block.lstrip("\n").startswith("["):
            first = block.lstrip("\n").splitlines()[0]
            raise ValueError(
                f"{fragment_path} must contain stanzas only, found a header clause: {first!r}"
            )
    return len(blocks)


def splice(core_text, fragment_text):
    """Insert the fragment after the last stanza of core's MS: block."""
    last = None
    for last in MS_ID_LINE.finditer(core_text):
        pass
    if last is None:
        raise ValueError("no 'id: MS:...' stanza found in core; cannot locate the seam")

    body = fragment_text.strip("\n")
    end = core_text.find("\n\n", last.end())
    if end == -1:                                   # MS block runs to end of file
        return core_text.rstrip("\n") + "\n\n" + body + "\n"
    return core_text[:end + 1] + "\n" + body + "\n" + core_text[end + 1:]


def _tag(line):
    return line.split(":", 1)[0]


def _value(line):
    return line.split(": ", 1)[1].strip()


def _parse_terms(blocks):
    """Map term id -> (block index, name, is_a ids, obsolete, lower-cased labels)."""
    terms = {}
    for i, block in enumerate(blocks):
        lines = block.strip("\n").splitlines()
        if not lines or lines[0] != "[Term]":
            continue
        fields = {"is_a": [], "labels": set(), "obsolete": False}
        for line in lines[1:]:
            match _tag(line):
                case "id":
                    fields["id"] = _value(line)
                case "name":
                    fields["name"] = _value(line)
                    fields["labels"].add(fields["name"].lower())
                case "synonym":
                    fields["labels"].add(_unquote(_value(line)).lower())
                case "is_a":
                    fields["is_a"].append(_value(line).split()[0])
                case "is_obsolete":
                    fields["obsolete"] = _value(line) == "true"
        terms[fields["id"]] = (i, fields["name"], fields["is_a"], fields["obsolete"], fields["labels"])
    return terms


def _unquote(quoted):
    """The text of an OBO quoted string at the start of `quoted`."""
    out, escaped = [], False
    for ch in quoted[1:]:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            break
        else:
            out.append(ch)
    return "".join(out)


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tokens(text):
    return {t.lower() for t in TOKEN.findall(text)}


def vendor_synonym(vendor, name):
    """"<vendor> <name>", or None when the name already carries the vendor.

    Skips "SCIEX 7500+" under SCIEX, "ThermoQuest Voyager" under Thermo Fisher Scientific
    and "Kromasil Diol" under Nouryon-Kromasil. A trailing "(<vendor>)" disambiguator,
    as in the RepoRT column "Denali C18 (Grace Vydac)", is dropped first."""
    if name.endswith(")") and " (" in name:
        base, _, qualifier = name[:-1].rpartition(" (")
        if _tokens(qualifier) <= _tokens(vendor) or _tokens(vendor) <= _tokens(qualifier):
            name = base
    first_word = name.split()[0].lower()
    if first_word.startswith(vendor.split()[0].lower()) or first_word in _tokens(vendor):
        return None
    return f"{vendor} {name}"


def add_vendor_synonyms(obo_text, roots=VENDOR_MODEL_ROOTS):
    """Add "<vendor> <model>" EXACT synonyms below each root's vendor classes.

    A vendor class is an is_a descendant of the root named "<vendor> <root name>". One
    nested under another vendor class whose vendor it merely extends (e.g. "Shimadzu
    MALDI-TOF instrument model" under "Shimadzu instrument model") is a product line,
    not a vendor, and contributes no synonym of its own. Obsolete terms are skipped, as
    is any synonym that would match another term's name or synonym (case-insensitively).
    Returns (text, number of synonyms added, skipped (term id, synonym) clashes)."""
    blocks = obo_text.split("\n\n")
    terms = _parse_terms(blocks)
    children = {}
    for tid, (_, _, parents, _, _) in terms.items():
        for parent in parents:
            children.setdefault(parent, []).append(tid)

    vendors = {}  # vendor class id -> vendor name
    for root in roots:
        root_suffix = " " + terms[root][1]
        stack = [(root, None)]
        while stack:
            node, parent_vendor = stack.pop()
            for child in children.get(node, ()):
                child_name = terms[child][1]
                if not child_name.endswith(root_suffix):
                    continue
                vendor = child_name.removesuffix(root_suffix)
                is_vendor = parent_vendor is None or not vendor.startswith(parent_vendor)
                if is_vendor:
                    vendors[child] = vendor
                stack.append((child, vendor if is_vendor else parent_vendor))

    proposed = {}  # term id -> synonyms
    for vendor_id, vendor in vendors.items():
        stack, seen = [vendor_id], set()
        while stack:
            for child in children.get(stack.pop(), ()):
                if child in seen:
                    continue
                seen.add(child)
                stack.append(child)
                _, name, _, obsolete, labels = terms[child]
                synonym = vendor_synonym(vendor, name)
                if obsolete or synonym is None or synonym.lower() in labels:
                    continue
                proposed.setdefault(child, set()).add(synonym)

    owners = {}  # lower-cased label -> ids of the terms that use or would use it
    for tid, (_, _, _, _, labels) in terms.items():
        for label in labels:
            owners.setdefault(label, set()).add(tid)
    for tid, synonyms in proposed.items():
        for synonym in synonyms:
            owners.setdefault(synonym.lower(), set()).add(tid)

    added, clashes = 0, []
    for tid in sorted(proposed):
        keep = []
        for synonym in sorted(proposed[tid]):
            if len(owners[synonym.lower()]) > 1:
                clashes.append((tid, synonym))
            else:
                keep.append(synonym)
        if not keep:
            continue
        block_index = terms[tid][0]
        lines = blocks[block_index].split("\n")
        at = max(i for i, line in enumerate(lines) if _tag(line) in PRE_SYNONYM_TAGS) + 1
        lines[at:at] = [f"synonym: {_quote(s)} EXACT []" for s in keep]
        blocks[block_index] = "\n".join(lines)
        added += len(keep)
    return "\n\n".join(blocks), added, clashes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", default=CORE_DEFAULT)
    parser.add_argument("--fragment", default=FRAGMENT_DEFAULT)
    parser.add_argument("--output", default=OUTPUT_DEFAULT)
    args = parser.parse_args()

    core_text = open(args.core, encoding="utf-8").read()
    fragment_text = open(args.fragment, encoding="utf-8").read()

    n_stanzas = check_fragment(fragment_text, args.fragment)
    merged = splice(core_text, fragment_text)
    merged, n_synonyms, clashes = add_vendor_synonyms(merged)
    open(args.output, "w", encoding="utf-8").write(merged)

    print(f"wrote {args.output}: {args.core} + {n_stanzas} stanzas from {args.fragment} "
          f"({merged.count('[Term]')} terms, {n_synonyms} vendor synonyms)")
    for tid, synonym in clashes:
        print(f"skipped vendor synonym {synonym!r} for {tid}: it matches another term's label")


if __name__ == "__main__":
    sys.exit(main())
