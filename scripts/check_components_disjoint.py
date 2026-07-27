#!/usr/bin/env python3
"""Fail if the source components define the same term id, or disagree on a shared typedef.

ROBOT merges rather than rejects: if psi-ms-core.obo and psi-ms-columns.obo both define
MS:5000000, the release gets one fused class carrying clauses from both, and no
downstream check notices. `fastobo-validator --duplicates` only looks within a single
file, and check_aggregate.py runs after the fusion has already happened, so the
collision has to be caught here, before the merge.

Term ids must be disjoint across components. Typedef ids may repeat -- the column module
re-declares MSREL:has_separation_mode so it stands alone -- but the repeated stanzas must
be identical, otherwise the merged property silently depends on which file ROBOT read.

Usage:
    python scripts/check_components_disjoint.py <component.obo> <component.obo> [...]
"""
import sys
from collections import defaultdict


def stanzas(path):
    """Return {("Term"|"Typedef", id): stanza body as a tuple of clause lines}."""
    found, kind, ident, body = {}, None, None, []

    def flush():
        if kind in ("Term", "Typedef") and ident is not None:
            found[(kind, ident)] = tuple(body)

    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("[") and line.endswith("]"):
            flush()
            kind, ident, body = line.strip("[]"), None, []
        elif line.startswith("id: ") and ident is None:
            ident = line[4:].strip()
        elif line:
            body.append(line)
    flush()
    return found


def check(paths):
    seen = defaultdict(dict)                     # (kind, id) -> {path: body}
    for path in paths:
        for key, body in stanzas(path).items():
            seen[key][path] = body

    errors = []
    for (kind, ident), by_path in sorted(seen.items()):
        if len(by_path) < 2:
            continue
        where = ", ".join(sorted(by_path))
        if kind == "Term":
            errors.append(f"term {ident} is defined in more than one component: {where}")
        elif len(set(by_path.values())) > 1:
            errors.append(f"typedef {ident} differs between components: {where}")
    return errors, len(seen)


def self_test():
    import tempfile
    core = "[Term]\nid: MS:1\nname: a\n\n[Typedef]\nid: MSREL:r\nname: r\n"
    ok = "[Term]\nid: MS:2\nname: b\n\n[Typedef]\nid: MSREL:r\nname: r\n"
    clash = "[Term]\nid: MS:1\nname: other\n"
    drift = "[Term]\nid: MS:2\nname: b\n\n[Typedef]\nid: MSREL:r\nname: renamed\n"
    with tempfile.TemporaryDirectory() as d:
        def run(*texts):
            paths = []
            for n, text in enumerate(texts):
                p = f"{d}/{n}.obo"
                open(p, "w", encoding="utf-8").write(text)
                paths.append(p)
            return check(paths)[0]
        assert run(core, ok) == [], run(core, ok)          # shared typedef, ids disjoint
        assert "MS:1" in run(core, clash)[0]
        assert "MSREL:r" in run(core, drift)[0]
    print("self-test OK")
    return 0


def main(argv):
    if len(argv) > 1 and argv[1] == "--self-test":
        return self_test()
    if len(argv) < 3:
        print("usage: check_components_disjoint.py <component.obo> <component.obo> [...]",
              file=sys.stderr)
        return 2
    errors, total = check(argv[1:])
    if errors:
        print("ERROR: source components are not disjoint:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"{len(argv) - 1} components, {total} distinct stanzas, no colliding term ids")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
