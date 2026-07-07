#!/usr/bin/env python3
"""Build the release psi-ms.obo by splicing the column-model module into it.

The master psi-ms.obo is kept free of the MS:5000000 chromatographic-column terms
(they live in psi-ms-columns.obo, regenerated weekly from repo-rt). At release time
this folds the module's [Term] stanzas into a copy of psi-ms.obo, so the published
OBO carries the columns too — matching the OWL, which robot-merges the same module.

The base is preserved byte-for-byte except for the inserted block: the module's
[Term] stanzas go immediately after the last MS: term stanza (before the PEFF terms),
keeping MS ids ascending. The module's two typedefs (has_separation_mode,
usp_designation) already live in master; as a safety net, any typedef the module
declares that the base lacks is injected into the base's typedef block first (a no-op
in normal operation).

Usage:
    python scripts/chrom_columns/build_release_obo.py <base.obo> <module.obo> <out.obo>
    (out may equal base to overwrite in place)
"""
import re
import sys

STANZA_RE = re.compile(r"(?m)^\[(Term|Typedef)\]")
ID_RE = re.compile(r"(?m)^id:\s*(\S+)")


def iter_stanzas(text):
    """Yield (kind, id, start, end) per [Term]/[Typedef] stanza in OBO text. start/end
    are char offsets; a stanza spans from its header to the next stanza header (or EOF),
    so the span includes the trailing blank-line separator."""
    matches = list(STANZA_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        idm = ID_RE.search(text, start, end)
        yield m.group(1), (idm.group(1) if idm else None), start, end


def build_release_obo(base_text, module_text):
    """Return base_text with the module's [Term] stanzas spliced in after the last MS:
    term (and any typedef the base is missing injected into its typedef block)."""
    module_stanzas = list(iter_stanzas(module_text))
    module_typedefs = [(sid, module_text[s:e].rstrip("\n"))
                       for kind, sid, s, e in module_stanzas if kind == "Typedef"]
    term_starts = [s for kind, sid, s, e in module_stanzas if kind == "Term"]
    if not term_starts:
        raise ValueError("module OBO has no [Term] stanzas to splice")
    term_block = module_text[term_starts[0]:].rstrip("\n") + "\n\n"

    base_stanzas = list(iter_stanzas(base_text))
    base_typedef_ids = {sid for kind, sid, s, e in base_stanzas if kind == "Typedef"}
    ms_terms = [(s, e) for kind, sid, s, e in base_stanzas
                if kind == "Term" and sid and sid.startswith("MS:")]
    if not ms_terms:
        raise ValueError("base OBO has no MS: term stanzas to insert after")
    insert_at = ms_terms[-1][1]  # end of the last MS term = start of the next stanza

    # Idempotent safety net: inject any module typedef the base lacks, just before the
    # base's first [Term]. With the typedefs already in master this is empty.
    missing = [txt for sid, txt in module_typedefs if sid not in base_typedef_ids]
    first_term_start = next(s for kind, sid, s, e in base_stanzas if kind == "Term")
    typedef_block = "".join(txt + "\n\n" for txt in missing)

    return (
        base_text[:first_term_start]
        + typedef_block
        + base_text[first_term_start:insert_at]
        + term_block
        + base_text[insert_at:]
    )


def main(argv):
    if len(argv) != 4:
        print("usage: build_release_obo.py <base.obo> <module.obo> <out.obo>",
              file=sys.stderr)
        return 2
    base = open(argv[1], encoding="utf-8").read()
    module = open(argv[2], encoding="utf-8").read()
    open(argv[3], "w", encoding="utf-8").write(build_release_obo(base, module))
    print(f"wrote {argv[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
