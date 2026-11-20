"""Does the HNSW index return the same top 10 as an exact search? Run against the dev database (data loaded).

For every company with an embedding, run the service's real similarity query twice: once as the API does (HNSW index,
pool 100) and once exact (index scans disabled, no pool limit), and compare the top-10 lists (as sets and in order).

Run: uv run python -m scripts.check_recall
Not checked: other limits or filters, other data sizes, queries for companies that were changed after loading.
"""

from collections import Counter

from mle import db, repository


def top10(conn, company: dict, exact: bool) -> list[str]:
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(repository._EF_SEARCH),))
        if exact:
            conn.execute("SET LOCAL enable_indexscan = off")
        params = {
            "q": company["embedding"], "model": company["embedding_model"], "reg": company["registration_number"],
            "own_state": company["state"], "include_struck_off": True, "state": None, "industry": None,
            "limit": 10, "pool": 10**6 if exact else 100,
        }  # fmt: skip
        return [
            r["registration_number"] for r in repository._dicts(conn.execute(repository._SIMILAR, params))
        ]


def main() -> None:
    with db.connect() as conn:
        regs = [
            r[0]
            for r in conn.execute("SELECT registration_number FROM companies WHERE embedding IS NOT NULL")
        ]
        same_set = same_order = 0
        mismatched: Counter[str] = Counter()
        for reg in regs:
            company = repository.get_company(conn, reg)
            indexed, exact = top10(conn, company, False), top10(conn, company, True)
            same_set += set(indexed) == set(exact)
            same_order += indexed == exact
            if indexed != exact:
                mismatched[company["nature_of_business"] or "?"] += 1
    print(f"{len(regs)} companies: identical top-10 set {same_set}, identical order {same_order}")
    if mismatched:
        print("mismatches by description:", mismatched.most_common(5))


if __name__ == "__main__":
    main()
