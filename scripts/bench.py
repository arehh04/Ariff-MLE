"""Latency of GET /v1/companies/{reg}/similar against a running API, plus EXPLAIN (ANALYZE) of the SQL it runs.

Run (API on :8000, data loaded): uv run python -m scripts.bench [--requests 1000] [--workers 1 8]
Each request picks a random company with an embedding, so the numbers include real result rows and JSON encoding.
Not measured: cold start, network, a database bigger than the 2,000-company feed (see DESIGN.md section 7).
"""

import argparse
import http.client
import json
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from mle import db, repository


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def run(regs: list[str], host: str, port: int, workers: int) -> tuple[list[float], float, int]:
    """Spread `regs` over `workers` threads, each with its own keep-alive connection. Returns (ms per request, seconds, errors)."""

    def work(chunk: list[str]) -> tuple[list[float], int]:
        conn, times, errors = http.client.HTTPConnection(host, port, timeout=10), [], 0
        for reg in chunk:
            started = time.perf_counter()
            conn.request("GET", f"/v1/companies/{reg}/similar?limit=10", headers={"X-Role": "analyst"})
            response = conn.getresponse()
            response.read()
            times.append((time.perf_counter() - started) * 1000)
            errors += response.status != 200
        return times, errors

    chunks = [regs[i::workers] for i in range(workers)]
    started = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(work, chunks))
    elapsed = time.perf_counter() - started
    return [t for times, _ in results for t in times], elapsed, sum(e for _, e in results)


def explain(reg: str) -> str:
    with db.connect() as conn:
        company = repository.get_company(conn, reg)
        params = {
            "q": company["embedding"], "model": company["embedding_model"], "reg": reg, "own_state": company["state"],
            "include_struck_off": True,
            "state": None, "industry": None, "limit": 10, "pool": 100,
        }  # fmt: skip
        with conn.transaction():
            conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(repository._EF_SEARCH),))
            rows = conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + repository._SIMILAR, params).fetchall()
    return "\n".join(r[0] for r in rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--workers", type=int, nargs="*", default=[1, 8])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    with db.connect() as conn:
        pool = [
            r[0]
            for r in conn.execute("SELECT registration_number FROM companies WHERE embedding IS NOT NULL")
        ]
    regs = random.Random(args.seed).choices(pool, k=args.requests)
    run(regs[:50], args.host, args.port, 1)  # warm-up, not counted

    print(f"{args.requests} requests per row, {len(pool)} companies with embeddings, limit=10\n")
    print(
        f"{'workers':>7s} {'p50 ms':>8s} {'p95 ms':>8s} {'p99 ms':>8s} {'max ms':>8s} {'req/s':>7s} {'errors':>6s}"
    )
    for workers in args.workers:
        times, elapsed, errors = run(regs, args.host, args.port, workers)
        print(f"{workers:7d} {statistics.median(times):8.1f} {pct(times, 95):8.1f} {pct(times, 99):8.1f} "
              f"{max(times):8.1f} {len(times) / elapsed:7.0f} {errors:6d}")  # fmt: skip
    print("\nEXPLAIN (ANALYZE, BUFFERS) for one query:\n" + explain(regs[0]))
    print(json.dumps({"query_company": regs[0]}))


if __name__ == "__main__":
    main()
