#!/usr/bin/env python3
"""Splice psi-ms-columns.obo-fragment into psi-ms-core.obo to build psi-ms.obo.

Usage:
    python scripts/build_release_obo.py [--core psi-ms-core.obo]
        [--fragment psi-ms-columns.obo-fragment] [--output psi-ms.obo]
"""

import argparse
import re
import sys

import fastobo

CORE_DEFAULT = "psi-ms-core.obo"
FRAGMENT_DEFAULT = "psi-ms-columns.obo-fragment"
OUTPUT_DEFAULT = "psi-ms.obo"

MS_ID_LINE = re.compile(r"^id: MS:\S+$", re.MULTILINE)

# Stamped into the release header so anyone opening psi-ms.obo is told it is generated.
# check-release-obo-built.yml fails any change to psi-ms.obo that a rebuild does not
# reproduce, so a hand edit cannot merge either way.
GENERATED_REMARK = fastobo.header.RemarkClause(
    "GENERATED FILE, do not edit. psi-ms.obo is built by "
    "scripts/build_release_obo.py from psi-ms-core.obo and psi-ms-columns.obo-fragment. "
    "Edit psi-ms-core.obo instead."
)


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


def stamp_generated(core_text):
    """Add GENERATED_REMARK as the first remark of the header. With no remarks it goes
    before the ontology clause, where OBO 1.4 serialisation order puts remarks.

    fastobo parses the header and serialises it back after the insert. Only the header
    is re-serialised: the stanzas are passed through as text, so term formatting in the
    release stays exactly as written in core."""
    header_end = core_text.find("\n[")
    if header_end == -1:
        raise ValueError("no stanza found in core; cannot locate the end of the header")
    header_text = core_text[:header_end + 1].rstrip("\n") + "\n"
    header = fastobo.loads(header_text).header
    # Refuse to reformat: if fastobo would not write the header back as it is in core,
    # the stamp would also rewrite unrelated header lines.
    if str(header) != header_text:
        raise ValueError("fastobo does not round-trip the core header byte-identically")
    if any(str(c) == str(GENERATED_REMARK) for c in header):
        raise ValueError("core already carries the GENERATED FILE remark; it belongs to psi-ms.obo only")
    kinds = [type(c) for c in header]
    at = next(
        (kinds.index(k) for k in (fastobo.header.RemarkClause, fastobo.header.OntologyClause)
         if k in kinds),
        len(header),
    )
    header.insert(at, GENERATED_REMARK)
    return str(header) + core_text[len(header_text):]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", default=CORE_DEFAULT)
    parser.add_argument("--fragment", default=FRAGMENT_DEFAULT)
    parser.add_argument("--output", default=OUTPUT_DEFAULT)
    args = parser.parse_args()

    core_text = open(args.core, encoding="utf-8").read()
    fragment_text = open(args.fragment, encoding="utf-8").read()

    n_stanzas = check_fragment(fragment_text, args.fragment)
    merged = splice(stamp_generated(core_text), fragment_text)
    open(args.output, "w", encoding="utf-8").write(merged)

    print(f"wrote {args.output}: {args.core} + {n_stanzas} stanzas from {args.fragment} "
          f"({merged.count('[Term]')} terms)")


if __name__ == "__main__":
    sys.exit(main())
