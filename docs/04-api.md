# API contract (v1)

Base `http://localhost:8000`. Registration numbers in paths are normalised server-side (`strip().upper()`).

## GET /v1/companies/{registrationNumber}/similar
Query: `limit` 1-50 (default 10), `state` (normalised before matching), `industry` (case-insensitive),
Header `X-Role`: `portal` (default; struck-off companies hidden) or `analyst` (struck-off visible, `status` shows it). Any other value: 422.
A stub for visibility only, not authentication.

200
```json
{
  "company": {"registrationNumber": "SYN-000101", "companyName": "...", "natureOfBusiness": "...",
              "state": "Perak", "industry": "Healthcare", "status": "active", "updatedAt": "2026-08-14T16:42:07+08:00"},
  "results": [
    {"registrationNumber": "SYN-000659", "companyName": "...", "natureOfBusiness": "...", "state": "Perak",
     "industry": "Healthcare", "status": "active", "score": 1.0, "updatedAt": "2026-08-14T09:16:31+08:00"}
  ],
  "meta": {"limit": 10, "returned": 10, "model": "BAAI/bge-small-en-v1.5", "reason": null}
}
```
`score` = `1 - cosine distance`, higher is more similar. The company itself is never in `results`.
Company exists but has no embedding: **200**, `results: []`, `meta.reason: "no_description"` (an empty panel, not an error).

## GET /v1/companies/{registrationNumber}
Header data for the page: same company fields plus `source` and `embeddingStatus`.

## GET /healthz, GET /readyz
`{"status":"ok"}`; `/readyz` returns 503 problem when the database is unreachable.

## Errors: RFC 7807 `application/problem+json`
```json
{"type": "https://onecredit.example/problems/company-not-found", "title": "Company not found",
 "status": 404, "detail": "No company with registration number SYN-999999", "instance": "/v1/companies/SYN-999999/similar"}
```
| Status | type suffix | When |
|---|---|---|
| 404 | `company-not-found` | unknown registration number |
| 422 | `validation-error` | bad `limit`, empty registration number; includes `errors: [{field, message}]` |
| 503 | `service-unavailable` | database unreachable |
| 500 | `internal-error` | anything else; never a stack trace |

Why: the portal branches on `status`, shows `title`, logs `type`; "no description" is a 200 so the panel renders an empty state.

## Ordering
`ORDER BY distance, same-state first, registration_number`: deterministic, so tests are stable.

## curl
```bash
curl -s "localhost:8000/v1/companies/SYN-000101/similar?limit=5" | jq
curl -si localhost:8000/v1/companies/NOPE/similar | head -n 12
```
