import sys

import fastobo
from fastobo.term import SubsetClause
from fastobo.id import PrefixedIdent


def check_term(term) -> bool:
    for clause in term:
        if isinstance(clause, SubsetClause):
            return True
    return False


def get_subset_for_id(identifier: PrefixedIdent) -> str | None:
    if identifier.prefix in {"PEFF", "UO", "NCIT"}:
        return identifier.prefix
    if identifier.prefix != "MS":
        return None
    if identifier.local == "0000000":
        return "core"
    return {"1": "core", "4": "mzQC", "5": "columns"}.get(identifier.local[:1])


def main():
    path = sys.argv[1]
    cv = fastobo.load(path)
    failed = 0
    for term in cv:
        if check_term(term):
            continue
        if isinstance(term.id, PrefixedIdent):
            subset = get_subset_for_id(term.id)
            print(f"{term.id}|{term[0].name} needs subset {subset}")
            failed += 1

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()