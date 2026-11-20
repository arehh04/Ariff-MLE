"""scripts/reconcile.py must pass on a correct database and name the problem for every kind of damage."""

import pytest

from scripts import reconcile as rec
from tests.integration.conftest import CountingEmbedder
from tests.integration.test_proofs import FEED, feed_messages, load

MODEL = "fake-1"


@pytest.fixture
def loaded(conn):
    load(conn, feed_messages(), 100)
    return conn


def check(conn):
    return rec.reconcile(conn, FEED, MODEL)


ANY = "SELECT registration_number FROM companies ORDER BY 1 LIMIT 1"
WITH_STATE = "SELECT registration_number FROM companies WHERE state IS NOT NULL ORDER BY 1 LIMIT 1"
ACTIVE = "SELECT registration_number FROM companies WHERE status = 'active' ORDER BY 1 LIMIT 1"
EMBEDDED = "SELECT registration_number FROM companies WHERE embedding IS NOT NULL ORDER BY 1 LIMIT 1"


def pick(conn, sql=ANY):
    return conn.execute(sql).fetchone()[0]


def test_a_correct_database_has_no_problems(loaded):
    problems, report = check(loaded)
    assert problems == []
    assert "companies: expected 2000, stored 2000" in report and "struck off: expected 8, stored 8" in report


def test_replaying_the_feed_still_reconciles(loaded):
    load(loaded, feed_messages(), 100, CountingEmbedder())  # redelivery raises seen_count, which is allowed
    assert check(loaded)[0] == []


def test_detects_a_missing_company(loaded):
    reg = pick(loaded)
    loaded.execute("DELETE FROM companies WHERE registration_number = %s", (reg,))
    problems, _ = check(loaded)
    assert any("MISSING" in p and reg in p for p in problems)


def test_detects_an_unexpected_company(loaded):
    loaded.execute(
        "INSERT INTO companies (registration_number, source_updated_at) VALUES ('SYN-999999', now())"
    )
    assert any("UNEXPECTED" in p and "SYN-999999" in p for p in check(loaded)[0])


def test_detects_a_wrong_value(loaded):
    reg = pick(loaded, WITH_STATE)
    loaded.execute("UPDATE companies SET state = 'Atlantis' WHERE registration_number = %s", (reg,))
    assert any("wrong values" in p and reg in p and "Atlantis" in p for p in check(loaded)[0])


def test_detects_a_wrong_status_and_the_struck_off_count(loaded):
    loaded.execute(
        "UPDATE companies SET status = 'struck_off' WHERE registration_number = %s",
        (pick(loaded, ACTIVE),),
    )
    problems, _ = check(loaded)
    assert any("struck-off count differs" in p for p in problems) and any(
        "wrong values" in p for p in problems
    )


def test_detects_a_lost_embedding(loaded):
    reg = pick(loaded, EMBEDDED)
    loaded.execute("UPDATE companies SET embedding = NULL WHERE registration_number = %s", (reg,))
    assert any("embedding presence differs" in p and reg in p for p in check(loaded)[0])


def test_detects_a_lost_event(loaded):
    loaded.execute("DELETE FROM company_events WHERE ctid = (SELECT ctid FROM company_events LIMIT 1)")
    assert any("event log differs" in p for p in check(loaded)[0])


def test_detects_a_lost_reject_row(loaded):
    loaded.execute("DELETE FROM rejected_events WHERE id = (SELECT min(id) FROM rejected_events)")
    assert any("reject rows differ" in p for p in check(loaded)[0])


def test_detects_an_undercounted_reject(loaded):
    loaded.execute(
        "UPDATE rejected_events SET seen_count = 0 WHERE id = (SELECT min(id) FROM rejected_events)"
    )
    assert any("counted fewer times" in p for p in check(loaded)[0])


def test_an_empty_database_reports_every_company_missing(conn):
    problems, _ = check(conn)
    assert any("2000 companies MISSING" in p for p in problems)


def test_main_exit_codes(loaded, monkeypatch, capsys):
    monkeypatch.setattr(rec.db, "connect", lambda: loaded)
    assert rec.main(["--model", MODEL]) == 0 and "OK:" in capsys.readouterr().out
    loaded.execute("DELETE FROM companies WHERE registration_number = %s", (pick(loaded),))
    assert rec.main(["--model", MODEL]) == 1 and "MISMATCH" in capsys.readouterr().out
