"""API tests: FastAPI TestClient on a seeded companies_test database (test plan, API section)."""

import math

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from mle import repository
from mle.api import create_app
from mle.config import Settings
from mle.repository import process_batch
from tests.integration.conftest import make_event

TS = "2026-08-14T09:00:00+08:00"
# text -> angle in degrees: vectors on a circle, so cosine similarity is cos(angle difference) and orders are known.
ANGLES = {"clinic": 0, "clinic annex": 5, "clinic branch": 10, "shop": 20, "palm oil": 90}


class AngleEmbedder:
    name = "angle-test"
    dim = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            a = math.radians(ANGLES[t])
            out.append([math.cos(a), math.sin(a)] + [0.0] * 382)
        return out


@pytest.fixture
def client(conn, test_dsn):
    events = [
        make_event("SYN-000101", TS, companyName="Query Clinic", natureOfBusiness="clinic", state="Perak", industry="Healthcare"),
        make_event("SYN-000105", TS, companyName="Twin Perak", natureOfBusiness="clinic", state="Perak", industry="Healthcare"),
        make_event("SYN-000107", TS, companyName="Twin Perak 2", natureOfBusiness="clinic", state="Perak", industry="Healthcare"),
        make_event("SYN-000106", TS, companyName="Twin Selangor", natureOfBusiness="clinic", state="Selangor", industry="Healthcare"),
        make_event("SYN-000102", TS, companyName="Branch", natureOfBusiness="clinic branch", state="Perak", industry="Healthcare"),
        make_event("SYN-000108", TS, companyName="Annex", natureOfBusiness="clinic annex", state="Perak", industry="Healthcare"),
        make_event("SYN-000103", TS, companyName="Shop", natureOfBusiness="shop", state="Selangor", industry="Retail"),
        make_event("SYN-000104", TS, companyName="Palm", natureOfBusiness="palm oil", state="Johor", industry="Retail"),
        make_event("SYN-000109", TS, companyName="Name Only"),  # no description, no industry -> no embedding
    ]  # fmt: skip
    process_batch(conn, AngleEmbedder(), events)
    process_batch(
        conn,
        AngleEmbedder(),
        [make_event("SYN-000108", "2026-09-01T00:00:00+08:00", kind="company-struck-off")],
    )
    with TestClient(create_app(Settings(database_url=test_dsn))) as c:
        yield c


def regs(response) -> list[str]:
    return [r["registrationNumber"] for r in response.json()["results"]]


def similar(client, reg="SYN-000101", role=None, **params):
    return client.get(f"/v1/companies/{reg}/similar", params=params, headers={"X-Role": role} if role else {})


def test_shape_and_ordering(client):
    r = similar(client)
    assert r.status_code == 200
    body = r.json()
    assert body["company"] == {
        "registrationNumber": "SYN-000101", "companyName": "Query Clinic", "natureOfBusiness": "clinic",
        "state": "Perak", "industry": "Healthcare", "status": "active", "updatedAt": "2026-08-14T09:00:00+08:00",
    }  # fmt: skip
    assert regs(r) == ["SYN-000105", "SYN-000107", "SYN-000106", "SYN-000102", "SYN-000103", "SYN-000104"]
    scores = [x["score"] for x in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0) and scores[3] == pytest.approx(
        math.cos(math.radians(10)), abs=1e-5
    )
    assert body["meta"] == {"limit": 10, "returned": 6, "model": "angle-test", "reason": None}
    assert set(body["results"][0]) == {
        "registrationNumber", "companyName", "natureOfBusiness", "state", "industry", "status", "score", "updatedAt",
    }  # fmt: skip


def test_self_is_excluded(client):
    assert "SYN-000101" not in regs(similar(client))


def test_identical_descriptions_prefer_the_same_state_then_registration_number(client):
    # 105, 106, 107 all score 1.0 against 101 (Perak): Perak twins first (by reg number), then the Selangor one
    assert regs(similar(client))[:3] == ["SYN-000105", "SYN-000107", "SYN-000106"]


def test_struck_off_hidden_for_portal_and_visible_for_analyst(client):
    assert "SYN-000108" not in regs(similar(client))  # default role is portal
    assert "SYN-000108" not in regs(similar(client, role="portal"))
    analyst = similar(client, role="analyst")
    assert "SYN-000108" in regs(analyst)
    assert {x["registrationNumber"]: x["status"] for x in analyst.json()["results"]}[
        "SYN-000108"
    ] == "struck_off"


def test_bad_role_is_422(client):
    r = similar(client, role="admin")
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["errors"][0]["field"] == "X-Role"


def test_lowercase_and_padded_registration_number_works(client):
    assert similar(client, reg="%20syn-000101%20").json()["company"]["registrationNumber"] == "SYN-000101"


def test_limit_and_filters(client):
    assert regs(similar(client, limit=2)) == ["SYN-000105", "SYN-000107"]
    assert similar(client, limit=2).json()["meta"]["returned"] == 2
    assert set(regs(similar(client, state="perak"))) == {
        "SYN-000105",
        "SYN-000107",
        "SYN-000102",
    }  # state is normalised
    assert set(regs(similar(client, industry="RETAIL"))) == {
        "SYN-000103",
        "SYN-000104",
    }  # industry ignores case


def test_company_without_description_is_200_with_a_reason(client):
    r = similar(client, reg="SYN-000109")
    assert r.status_code == 200
    assert r.json()["results"] == [] and r.json()["meta"]["reason"] == "no_description"


def test_unknown_company_is_404_problem(client):
    r = similar(client, reg="SYN-999999")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/company-not-found") and body["status"] == 404
    assert "SYN-999999" in body["detail"] and body["instance"] == "/v1/companies/SYN-999999/similar"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"limit": "abc"}])
def test_bad_limit_is_422_with_field_list(client, params):
    r = similar(client, **params)
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/validation-error")
    assert body["errors"][0]["field"] == "limit" and body["errors"][0]["message"]


def test_blank_registration_number_is_422(client):
    r = similar(client, reg="%20")
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "registrationNumber"


def test_detail_endpoint(client):
    body = client.get("/v1/companies/syn-000101").json()
    assert body["registrationNumber"] == "SYN-000101" and body["embeddingStatus"] == "ok" and "source" in body
    assert client.get("/v1/companies/SYN-000109").json()["embeddingStatus"] == "missing"
    assert client.get("/v1/companies/SYN-999999").status_code == 404


def test_struck_off_company_itself_is_hidden_from_portal(client):
    assert client.get("/v1/companies/SYN-000108").status_code == 404
    assert similar(client, reg="SYN-000108").status_code == 404
    assert (
        client.get("/v1/companies/SYN-000108", headers={"X-Role": "analyst"}).json()["status"] == "struck_off"
    )


def test_unknown_route_is_problem_json(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")


def test_probes(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}


def test_pool_replaces_connections_killed_by_a_database_restart(client, test_dsn):
    assert client.get("/readyz").status_code == 200
    with psycopg.connect(test_dsn, autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'companies_test' AND pid <> pg_backend_pid()"
        )
    assert similar(client).status_code == 200  # no spurious 503 after the database comes back


def test_unreachable_database_is_503_problem_json_without_traceback(client):
    client.app.state.pool = ConnectionPool(
        "postgresql://postgres:postgres@localhost:1/none", timeout=0.3, open=True
    )
    for url in ("/v1/companies/SYN-000101/similar", "/v1/companies/SYN-000101", "/readyz"):
        r = client.get(url)
        assert r.status_code == 503 and r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["type"].endswith("/service-unavailable") and "Traceback" not in r.text
    assert client.get("/healthz").status_code == 200  # liveness does not depend on the database


def test_unexpected_error_is_500_problem_without_internals(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(repository, "get_company", boom)
    quiet = TestClient(client.app, raise_server_exceptions=False)
    r = quiet.get("/v1/companies/SYN-000101")
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/internal-error")
    assert "secret internal detail" not in r.text and "Traceback" not in r.text
