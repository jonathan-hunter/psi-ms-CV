#!/usr/bin/env python3
"""Add "<vendor> <model>" EXACT synonyms to model terms, in place (issue #602).

PSI-MS model names leave the vendor out, so a search for "Thermo Scientific Q Exactive"
or "Waters ACQUITY UPLC BEH C18" would not find the term. This adds that synonym to
every term below a vendor class of the hierarchies in VENDOR_MODEL_ROOTS. It is
idempotent: run it again after adding model terms and only the new synonyms are added.

Usage:
    python scripts/add_vendor_synonyms.py [--dry-run] [FILE ...]

FILE defaults to psi-ms-core.obo and psi-ms-columns.obo-fragment, which are read
together (the column root is in core, its vendor classes in the fragment) and each
rewritten in place.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FILES_DEFAULT = ("psi-ms-core.obo", "psi-ms-columns.obo-fragment")

# Model hierarchies whose vendor classes are named "<vendor> <root name>", e.g.
# "Waters instrument model" under "instrument model".
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


@dataclass
class Term:
    file: int
    block: int
    name: str
    parents: list[str] = field(default_factory=list)
    obsolete: bool = False
    labels: set[str] = field(default_factory=set)  # lower-cased name and synonyms


def _tag(line):
    return line.split(":", 1)[0]


def _value(line):
    return line.split(": ", 1)[1].strip()


def _unquote(quoted):
    """The text of the OBO quoted string at the start of `quoted`."""
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


def parse_terms(files_blocks):
    terms = {}
    for f, blocks in enumerate(files_blocks):
        for b, block in enumerate(blocks):
            lines = block.strip("\n").splitlines()
            if not lines or lines[0] != "[Term]":
                continue
            tid, term = None, Term(f, b, "")
            for line in lines[1:]:
                match _tag(line):
                    case "id":
                        tid = _value(line)
                    case "name":
                        term.name = _value(line)
                        term.labels.add(term.name.lower())
                    case "synonym":
                        term.labels.add(_unquote(_value(line)).lower())
                    case "is_a":
                        term.parents.append(_value(line).split()[0])
                    case "is_obsolete":
                        term.obsolete = _value(line) == "true"
            terms[tid] = term
    return terms


def vendor_synonym(vendor, name):
    """"<vendor> <name>", or None when the name already carries the vendor.

    Skips "SCIEX 7500+" under SCIEX, "ThermoQuest Voyager" under Thermo Fisher Scientific
    and "Kromasil Diol" under Nouryon-Kromasil. A trailing "(<vendor>)" disambiguator,
    as in the RepoRT column "Denali C18 (Grace Vydac)", is dropped first; other
    qualifiers such as "(2)" or "(L)" are kept."""
    if name.endswith(")") and " (" in name:
        base, _, qualifier = name[:-1].rpartition(" (")
        if _tokens(qualifier) <= _tokens(vendor) or _tokens(vendor) <= _tokens(qualifier):
            name = base
    first_word = name.split()[0].lower()
    if first_word.startswith(vendor.split()[0].lower()) or first_word in _tokens(vendor):
        return None
    return f"{vendor} {name}"


def find_vendors(terms, children, roots):
    """Vendor class id -> vendor name.

    A vendor class is an is_a descendant of a root named "<vendor> <root name>". One
    nested under another vendor class whose vendor it merely extends (e.g. "Shimadzu
    MALDI-TOF instrument model" under "Shimadzu instrument model") is a product line,
    not a vendor."""
    vendors = {}
    for root in roots:
        suffix = " " + terms[root].name
        stack = [(root, None)]
        while stack:
            node, parent_vendor = stack.pop()
            for child in children.get(node, ()):
                if not terms[child].name.endswith(suffix):
                    continue
                vendor = terms[child].name.removesuffix(suffix)
                is_vendor = parent_vendor is None or not vendor.startswith(parent_vendor)
                if is_vendor:
                    vendors[child] = vendor
                stack.append((child, vendor if is_vendor else parent_vendor))
    return vendors


def add_vendor_synonyms(texts, roots=VENDOR_MODEL_ROOTS):
    """Add the synonyms to the OBO texts, read together as one vocabulary.

    Obsolete terms are skipped, as is any synonym that would match another term's name
    or synonym case-insensitively. Returns (new texts, synonyms added, skipped
    (term id, synonym) clashes)."""
    files_blocks = [text.split("\n\n") for text in texts]
    terms = parse_terms(files_blocks)
    children = {}
    for tid, term in terms.items():
        for parent in term.parents:
            if parent in terms:
                children.setdefault(parent, []).append(tid)

    proposed = {}  # term id -> synonyms
    for vendor_id, vendor in find_vendors(terms, children, roots).items():
        stack, seen = [vendor_id], set()
        while stack:
            for child in children.get(stack.pop(), ()):
                if child in seen:
                    continue
                seen.add(child)
                stack.append(child)
                term = terms[child]
                synonym = vendor_synonym(vendor, term.name)
                if term.obsolete or synonym is None or synonym.lower() in term.labels:
                    continue
                proposed.setdefault(child, set()).add(synonym)

    owners = {}  # lower-cased label -> ids of the terms that use or would use it
    for tid, term in terms.items():
        for label in term.labels:
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
        term = terms[tid]
        lines = files_blocks[term.file][term.block].split("\n")
        at = max(i for i, line in enumerate(lines) if _tag(line) in PRE_SYNONYM_TAGS) + 1
        lines[at:at] = [f"synonym: {_quote(s)} EXACT []" for s in keep]
        files_blocks[term.file][term.block] = "\n".join(lines)
        added += len(keep)
    return ["\n\n".join(blocks) for blocks in files_blocks], added, clashes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, default=[Path(f) for f in FILES_DEFAULT])
    parser.add_argument("--dry-run", action="store_true", help="report, do not write")
    args = parser.parse_args()

    texts = [p.read_text(encoding="utf-8") for p in args.files]
    new_texts, added, clashes = add_vendor_synonyms(texts)
    for tid, synonym in clashes:
        print(f"skipped {synonym!r} for {tid}: it matches another term's label")
    for path, old, new in zip(args.files, texts, new_texts):
        if new != old and not args.dry_run:
            path.write_text(new, encoding="utf-8")
    print(f"{'would add' if args.dry_run else 'added'} {added} vendor synonyms")


if __name__ == "__main__":
    sys.exit(main())
