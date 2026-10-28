"""A saved evaluation belongs to whoever saved it, and nothing shared sees it.

What these tests hold the code to (app/project_access.py, app/project_audit.py,
app/services/evaluation_store.py):

  - `POST /evaluate` computes and writes nothing; `?save=true` writes the
    evaluation and its predictions_log row together, owned by the caller, and
    only for a specialist or an administrator;
  - a specialist reads their own rows and nothing else, analyst and viewer read
    the shared corpus, and somebody else's id is a 404 like a missing one;
  - every administrator read or deletion is recorded and committed before the
    data leaves, and a recording that fails returns no data;
  - adding pilot rows changes no shared figure -- trends, data health, drift,
    forecasting, training, diagnostics.

Every test runs against a private SQLite file holding only project data (the
`world` fixture), so counts and before/after comparisons are exact rather than
deltas over whatever the rest of the suite left behind, and `DELETE /history`
cannot reach anyone else's rows. Accounts stay in the suite's database: the
token check reads them there, through its own session.

Each test is aimed at one line whose removal must fail it. Where a negative case
could pass for the wrong reason, its docstring says which reason is excluded.

The schema itself -- foreign keys, RESTRICT, the CHECK against direct SQL, the
populated upgrade -- is tested against PostgreSQL in
tests/test_project_ownership_postgres.py.
"""
import importlib.util
import os
import re
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app import accounts, database
from app.database import (Base, Evaluation, PredictionLog, ProjectAccessEvent,
                          ProjectAccessTarget)
from app.passwords import _hash_password
from tests._auth_tokens import access_token_for

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROJECT = {"name": "Owned Solar", "budget": 250000, "co2_reduction": 120,
           "social_impact": 7, "duration_months": 24, "region": "Germany"}


def H(account):
    return {"Authorization": f"Bearer {access_token_for(account.username)}"}


# --------------------------------------------------------------------- world


def _evaluation(session, *, scope, owner=None, name, score, when, budget=100000.0):
    row = Evaluation(name=name, budget=budget, co2_reduction=50.0, social_impact=6.0,
                     duration_months=12, total_score=score, environment_score=score,
                     social_score=score, economic_score=score, success_probability=score,
                     recommendation="r", risk_level="MEDIUM", region="Europe",
                     lat=50.0, lon=10.0, created_at=when,
                     owner_user_id=owner, data_scope=scope)
    session.add(row)
    session.flush()
    return row.id


def _log(session, *, scope, owner=None, budget=100000.0, co2=50.0, category="Solar Energy",
         probability=70.0, prediction=1, when=None):
    row = PredictionLog(endpoint="evaluate", budget=budget, co2_reduction=co2,
                        social_impact=6.0, duration_months=12, category=category,
                        region="Germany", prediction=prediction, probability=probability,
                        esg_total_score=60.0, latency_ms=12.0, model_version="unknown",
                        timestamp=when or datetime.utcnow(),
                        owner_user_id=owner, data_scope=scope)
    session.add(row)
    session.flush()
    return row.id


class World(SimpleNamespace):
    def session(self):
        return self.Session()

    def count(self, model, **where):
        with self.Session() as s:
            q = s.query(model)
            for k, v in where.items():
                q = q.filter(getattr(model, k) == v)
            return q.count()

    def add_pilot(self, owner, *, name, score=1.0, budget=9.9e10, when=None, category=None):
        """A pilot evaluation and log row with values no public row has."""
        when = when or datetime.utcnow() + timedelta(days=1)
        with self.Session() as s:
            e = _evaluation(s, scope="pilot", owner=owner.id, name=name, score=score,
                            when=when, budget=budget)
            # A null budget and category feed every null-rate numerator
            # (data health's and the summary's); a CO2 of 500 the
            # out-of-range one.
            lg = _log(s, scope="pilot", owner=owner.id, budget=None, co2=500.0,
                      category=category, probability=3.0, prediction=0)
            s.commit()
        return e, lg

    def events(self, **where):
        with self.Session() as s:
            q = s.query(ProjectAccessEvent)
            for k, v in where.items():
                q = q.filter(getattr(ProjectAccessEvent, k) == v)
            out = []
            for e in q.order_by(ProjectAccessEvent.occurred_at, ProjectAccessEvent.id):
                targets = (s.query(ProjectAccessTarget)
                           .filter(ProjectAccessTarget.event_id == e.id)
                           .order_by(ProjectAccessTarget.id).all())
                out.append(SimpleNamespace(event=e, targets=targets))
            return out


@pytest.fixture
def world(client, make_account, monkeypatch, tmp_path):
    """Five accounts and a private project database: public, pilot A, pilot B.

    Pilot B is the sentinel: a unique name, extreme numbers and the newest
    date, so a leak shows in a minimum, a mean, the last window and a count.
    """
    import app.main as main
    import app.api.admin_diagnostics as admin_diagnostics

    people = SimpleNamespace(
        a=make_account(prefix="spec-a"),
        b=make_account(prefix="spec-b"),
        admin=make_account(prefix="own-admin", role="admin"),
        analyst=make_account(prefix="own-analyst", role="analyst"),
        viewer=make_account(prefix="own-viewer", role="viewer"),
    )

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'projects.db'}",
                              connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    now = datetime.utcnow()
    with Session() as s:
        legacy = [_evaluation(s, scope="public", name=f"legacy-{i}", score=50.0 + i,
                              when=now - timedelta(days=3 - i)) for i in range(3)]
        legacy_log = [_log(s, scope="public", when=now - timedelta(hours=1)) for _ in range(3)]
        a_eval = _evaluation(s, scope="pilot", owner=people.a.id, name="pilot-a",
                             score=61.0, when=now - timedelta(hours=2))
        a_log = _log(s, scope="pilot", owner=people.a.id)
        s.commit()

    # Patched after the accounts exist: make_account writes through
    # app.database.SessionLocal too, and must keep writing to the suite's
    # database. The token check is unaffected -- app.auth bound its own
    # SessionLocal at import.
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(main, "get_db_sync", lambda: Session())
    monkeypatch.setattr(admin_diagnostics, "SessionLocal", Session)

    w = World(engine=engine, Session=Session, people=people, legacy=legacy,
              legacy_log=legacy_log, a_eval=a_eval, a_log=a_log)
    w.b_eval, w.b_log = w.add_pilot(people.b, name="pilot-b-sentinel")
    yield w
    engine.dispose()


def _ids(response):
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _csv_ids(response):
    assert response.status_code == 200, response.text
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("ID,"), lines[0]
    return {int(line.split(",", 1)[0]) for line in lines[1:]}


# ------------------------------------------------- 1-5: calculating and saving


def test_an_anonymous_calculation_writes_nothing(client, world):
    r = client.post("/api/v1/evaluate", json=PROJECT)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is False and r.json()["evaluation_id"] is None
    assert r.headers["cache-control"] == "private, no-store"
    assert world.count(Evaluation) == 5
    assert world.count(PredictionLog) == 5


def test_an_anonymous_save_is_401_and_writes_nothing(client, world):
    r = client.post("/api/v1/evaluate?save=true", json=PROJECT)
    assert r.status_code == 401
    assert world.count(Evaluation) == 5
    assert world.count(PredictionLog) == 5


@pytest.mark.parametrize("kind", ["garbage", "not-bearer", "blocked", "revoked"])
@pytest.mark.parametrize("save", [False, True])
def test_a_bad_token_is_401_and_never_anonymous(client, world, make_account, kind, save):
    """A token that is present and wrong is refused -- not read as no token.

    `blocked` uses a token minted at the account's *current* version, so only
    the block check can refuse it; `revoked` is a token from before a password
    reset, so only the version check can. Both modes are checked: read as
    anonymous, a preview would answer 200, which is the failure this excludes.
    """
    if kind == "garbage":
        headers = {"Authorization": "Bearer not.a.token"}
    elif kind == "not-bearer":
        headers = {"Authorization": "Basic dXNlcjpwYXNz"}
    elif kind == "blocked":
        headers = H(make_account(prefix="blocked", is_blocked=True))
    else:
        from app.auth import SessionLocal as account_sessions  # the suite's database
        user = make_account(prefix="revoked")
        headers = H(user)
        with account_sessions() as s:
            accounts.reset_password(s, user.id, "a fresh passphrase for this test",
                                    hash_password=_hash_password)
    url = "/api/v1/evaluate?save=true" if save else "/api/v1/evaluate"
    r = client.post(url, json=PROJECT, headers=headers)
    assert r.status_code == 401, r.text
    assert world.count(Evaluation) == 5 and world.count(PredictionLog) == 5


@pytest.mark.parametrize("role", ["specialist", "admin"])
def test_a_save_writes_both_rows_under_the_callers_account(client, world, make_account, role):
    saver = make_account(prefix=f"saver-{role}", role=role)
    r = client.post("/api/v1/evaluate?save=true", json=PROJECT, headers=H(saver))

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["saved"] is True and isinstance(body["evaluation_id"], int)
    assert r.headers["cache-control"] == "private, no-store"
    with world.session() as s:
        ev = s.get(Evaluation, body["evaluation_id"])
        logs = s.query(PredictionLog).filter(PredictionLog.owner_user_id == saver.id).all()
    assert (ev.owner_user_id, ev.data_scope) == (saver.id, "pilot")
    assert [(lg.owner_user_id, lg.data_scope) for lg in logs] == [(saver.id, "pilot")]
    assert ev.total_score == pytest.approx(body["total_score"])


@pytest.mark.parametrize("role", ["analyst", "viewer"])
def test_analyst_and_viewer_cannot_save(client, world, make_account, role):
    r = client.post("/api/v1/evaluate?save=true", json=PROJECT,
                    headers=H(make_account(prefix=f"nosave-{role}", role=role)))
    assert r.status_code == 403
    assert world.count(Evaluation) == 5 and world.count(PredictionLog) == 5


@pytest.mark.parametrize("key", ["owner_user_id", "user_id", "data_scope"])
def test_the_body_cannot_name_the_owner_or_the_scope(client, world, key):
    """Refused, not ignored: an extra key Pydantic drops would otherwise ride
    along on the raw JSON the route reads."""
    value = "public" if key == "data_scope" else str(world.people.b.id)
    r = client.post("/api/v1/evaluate?save=true", json=dict(PROJECT, **{key: value}),
                    headers=H(world.people.a))
    assert r.status_code == 422, r.text
    assert key in r.json()["detail"]["fields"]
    assert world.count(Evaluation) == 5 and world.count(PredictionLog) == 5


def test_the_owner_is_the_token_not_anything_the_request_says(client, world):
    """With a control key absent, the row still belongs to the caller -- B
    saving never produces a row owned by A, whatever else is in the request."""
    r = client.post("/api/v1/evaluate?save=true&owner_user_id=" + str(world.people.a.id),
                    json=PROJECT, headers=H(world.people.b))
    assert r.status_code == 201, r.text
    with world.session() as s:
        assert s.get(Evaluation, r.json()["evaluation_id"]).owner_user_id == world.people.b.id


# --------------------------------------------- 6-8: who reads which rows


def test_a_specialist_reads_only_their_own_rows(client, world):
    a = H(world.people.a)
    page = client.get("/api/v1/history", headers=a)
    assert _ids(page) == {world.a_eval}
    assert page.json()["total"] == 1
    assert page.headers["cache-control"] == "private, no-store"
    assert client.get(f"/api/v1/history/{world.a_eval}", headers=a).status_code == 200
    assert _csv_ids(client.get("/api/v1/export/csv", headers=a)) == {world.a_eval}
    log = client.get("/api/v1/analytics/predictions-log", headers=a)
    assert {row["id"] for row in log.json()} == {world.a_log}


@pytest.mark.parametrize("whose", ["other specialist", "public"])
def test_somebody_elses_id_is_404_like_a_missing_one(client, world, whose):
    target = world.b_eval if whose == "other specialist" else world.legacy[0]
    other = client.get(f"/api/v1/history/{target}", headers=H(world.people.a))
    missing = client.get("/api/v1/history/987654321", headers=H(world.people.a))
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


@pytest.mark.parametrize("role", ["analyst", "viewer"])
def test_analyst_and_viewer_read_the_shared_corpus_only(client, world, role):
    who = H(getattr(world.people, role))
    assert _ids(client.get("/api/v1/history", headers=who)) == set(world.legacy)
    assert client.get(f"/api/v1/history/{world.b_eval}", headers=who).status_code == 404
    assert client.get(f"/api/v1/history/{world.legacy[0]}", headers=who).status_code == 200
    assert _csv_ids(client.get("/api/v1/export/csv", headers=who)) == set(world.legacy)
    log = client.get("/api/v1/analytics/predictions-log", headers=who).json()
    assert {row["id"] for row in log} == set(world.legacy_log)


@pytest.mark.parametrize("role", ["a", "b", "analyst", "viewer", "admin"])
def test_list_detail_and_export_agree_for_every_role(client, world, role):
    who = H(getattr(world.people, role))
    page = client.get("/api/v1/history?limit=500", headers=who)
    listed = _ids(page)
    assert page.json()["total"] == len(listed)
    assert _csv_ids(client.get("/api/v1/export/csv", headers=who)) == listed
    for eval_id in listed:
        assert client.get(f"/api/v1/history/{eval_id}", headers=who).status_code == 200


def test_only_the_administrators_view_carries_ownership(client, world):
    """Explicit views: a new column reaches a response only by being named."""
    mine = client.get("/api/v1/history", headers=H(world.people.a)).json()["items"][0]
    assert "owner_user_id" not in mine and "data_scope" not in mine
    log = client.get("/api/v1/analytics/predictions-log", headers=H(world.people.a)).json()[0]
    assert "owner_user_id" not in log and "data_scope" not in log

    items = client.get("/api/v1/history?limit=500", headers=H(world.people.admin)).json()["items"]
    by_id = {i["id"]: i for i in items}
    assert by_id[world.b_eval]["owner_user_id"] == str(world.people.b.id)
    assert by_id[world.b_eval]["data_scope"] == "pilot"
    assert by_id[world.legacy[0]]["data_scope"] == "public"


# ------------------------------------------------------- 9: refusals first


@pytest.mark.parametrize("role", ["a", "viewer", "analyst"])
def test_delete_is_403_for_everyone_but_admin_whatever_the_id(client, world, role):
    """The same 403 for an own, a foreign and a missing id: the role is refused
    before any lookup, so the answer says nothing about which ids exist."""
    who = H(getattr(world.people, role))
    for eval_id in (world.a_eval, world.b_eval, 987654321):
        r = client.delete(f"/api/v1/history/{eval_id}", headers=who)
        assert r.status_code == 403, (eval_id, r.status_code)
    assert client.delete("/api/v1/history", headers=who).status_code == 403
    assert world.count(Evaluation) == 5
    assert world.events() == []


# ---------------------------------------------------- 10-11: the audit


def test_an_administrators_list_is_recorded_with_what_it_returned(client, world):
    admin = H(world.people.admin)
    r = client.get("/api/v1/history?limit=2", headers=admin)
    returned = [i["id"] for i in r.json()["items"]]

    [rec] = world.events()
    assert rec.event.action == "list" and rec.event.outcome == "ok"
    assert rec.event.actor_user_id == world.people.admin.id
    assert rec.event.actor_role == "admin"
    assert rec.event.route_template == "/api/v1/history"
    assert rec.event.matched_count == r.json()["total"] == 5
    assert rec.event.returned_count == 2
    assert rec.event.filters == {"limit": 2, "offset": 0}
    assert [(t.resource_type, t.resource_id, t.effect) for t in rec.targets] == [
        ("evaluation", i, "returned") for i in returned]

    client.get("/api/v1/history?limit=2", headers=admin)
    assert len(world.events()) == 2, "a repeated read is a second event"


def test_the_target_snapshot_records_whose_row_it_was(client, world):
    client.get(f"/api/v1/history/{world.b_eval}", headers=H(world.people.admin))
    [rec] = world.events(action="detail")
    [t] = rec.targets
    assert (t.resource_id, t.owner_user_id, t.data_scope, t.effect) == (
        world.b_eval, world.people.b.id, "pilot", "returned")


def test_an_administrators_miss_is_recorded_too(client, world):
    r = client.get("/api/v1/history/987654321", headers=H(world.people.admin))
    assert r.status_code == 404
    [rec] = world.events()
    assert (rec.event.action, rec.event.outcome, rec.event.status_code) == ("detail", "not_found", 404)
    assert [(t.resource_id, t.effect, t.data_scope) for t in rec.targets] == [
        (987654321, "not_found", None)]


def test_an_administrator_reading_their_own_project_is_recorded(client, world):
    admin = H(world.people.admin)
    saved = client.post("/api/v1/evaluate?save=true", json=PROJECT, headers=admin).json()
    client.get(f"/api/v1/history/{saved['evaluation_id']}", headers=admin)
    [rec] = world.events(action="detail")
    assert rec.targets[0].owner_user_id == world.people.admin.id


def test_an_export_and_a_log_read_name_every_row(client, world):
    admin = H(world.people.admin)
    exported = _csv_ids(client.get("/api/v1/export/csv", headers=admin))
    log = client.get("/api/v1/analytics/predictions-log", headers=admin).json()

    [export] = world.events(action="export")
    assert {t.resource_id for t in export.targets} == exported
    assert export.event.returned_count == len(exported) == 5
    [read] = world.events(action="log_read")
    assert {(t.resource_type, t.resource_id) for t in read.targets} == {
        ("prediction_log", row["id"]) for row in log}


def test_a_deletion_and_its_record_are_one_change(client, world):
    r = client.delete(f"/api/v1/history/{world.b_eval}", headers=H(world.people.admin))
    assert r.status_code == 200 and r.json() == {"status": "deleted", "id": world.b_eval}
    with world.session() as s:
        assert s.get(Evaluation, world.b_eval) is None
        # The log row stays: nothing links it to the evaluation reliably.
        assert s.get(PredictionLog, world.b_log) is not None
    [rec] = world.events(action="delete")
    assert rec.event.affected_count == 1
    assert [(t.resource_id, t.owner_user_id, t.effect) for t in rec.targets] == [
        (world.b_eval, world.people.b.id, "deleted")]


def _gone_before_this_request_touches_it(world, where):
    """Another administrator's DELETE commits just before this request's first
    statement on `evaluations` -- the interleaving of two concurrent deletes,
    made deterministic. Through a separate sqlite3 connection, so the request's
    own engine and events are not involved."""
    import sqlite3

    fired = []

    def before(conn, cursor, statement, params, context, executemany):
        if fired or "evaluations" not in statement:
            return
        fired.append(statement)
        other = sqlite3.connect(world.engine.url.database)
        other.execute("DELETE FROM evaluations WHERE " + where)
        other.commit()
        other.close()

    sa.event.listen(world.engine, "before_cursor_execute", before)
    return lambda: sa.event.remove(world.engine, "before_cursor_execute", before)


def test_a_row_another_delete_removed_first_is_not_recorded_as_deleted(client, world):
    """The record says what this request deleted. Read before the DELETE, the
    row looked present, so the second of two concurrent deletes recorded it as
    `deleted` with affected_count 1 while deleting nothing."""
    stop = _gone_before_this_request_touches_it(world, f"id = {world.b_eval}")
    try:
        r = client.delete(f"/api/v1/history/{world.b_eval}", headers=H(world.people.admin))
    finally:
        stop()
    assert r.status_code == 404
    [rec] = world.events(action="delete")
    assert (rec.event.outcome, rec.event.affected_count) == ("not_found", 0)
    assert [t.effect for t in rec.targets] == ["not_found"]


def test_a_clear_that_finds_the_rows_gone_records_none(client, world):
    stop = _gone_before_this_request_touches_it(world, "data_scope = 'public'")
    try:
        r = client.delete("/api/v1/history", headers=H(world.people.admin))
    finally:
        stop()
    assert r.status_code == 200
    assert r.json()["deleted_count"] == 0
    [rec] = world.events(action="clear")
    assert rec.event.affected_count == 0 and rec.targets == []


def test_deleting_a_missing_id_is_a_recorded_404(client, world):
    r = client.delete("/api/v1/history/987654321", headers=H(world.people.admin))
    assert r.status_code == 404
    [rec] = world.events(action="delete")
    assert rec.event.outcome == "not_found" and rec.event.affected_count == 0


def test_clearing_the_history_clears_the_shared_corpus_only(client, world):
    r = client.delete("/api/v1/history", headers=H(world.people.admin))
    assert r.status_code == 200
    assert r.json() == {"status": "cleared", "scope": "public", "deleted_count": 3}
    with world.session() as s:
        left = {(e.id, e.data_scope) for e in s.query(Evaluation)}
    assert left == {(world.a_eval, "pilot"), (world.b_eval, "pilot")}
    [rec] = world.events(action="clear")
    assert {t.resource_id for t in rec.targets} == set(world.legacy)
    assert {t.effect for t in rec.targets} == {"deleted"}


def _break_the_audit(world):
    """The audit store disappears: every insert into it now fails."""
    with world.engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE project_access_targets"))
        conn.execute(sa.text("DROP TABLE project_access_events"))


@pytest.mark.parametrize("path", ["/api/v1/history", "/api/v1/history/{b}",
                                  "/api/v1/export/csv", "/api/v1/analytics/predictions-log"])
def test_an_unrecorded_read_returns_no_data(client, world, path):
    """Fail closed: 503, and nothing of the rows in the body."""
    _break_the_audit(world)
    r = client.get(path.format(b=world.b_eval), headers=H(world.people.admin))
    assert r.status_code == 503, r.text
    assert "pilot-b-sentinel" not in r.text and "legacy-0" not in r.text


@pytest.mark.parametrize("path", ["/api/v1/history/{b}", "/api/v1/history"])
def test_an_unrecorded_deletion_does_not_happen(client, world, path):
    _break_the_audit(world)
    r = client.delete(path.format(b=world.b_eval), headers=H(world.people.admin))
    assert r.status_code == 503, r.text
    assert world.count(Evaluation) == 5


def test_the_access_log_is_readable_by_administrators_only(client, world):
    admin = H(world.people.admin)
    client.get(f"/api/v1/history/{world.b_eval}", headers=admin)
    page = client.get("/api/v1/admin/project-access-log", headers=admin)
    assert page.status_code == 200, page.text
    [item] = page.json()["items"]
    assert item["action"] == "detail"
    assert item["targets"][0]["resource_id"] == world.b_eval
    by_target = client.get(f"/api/v1/admin/project-access-log?resource_id={world.b_eval}",
                           headers=admin).json()
    assert by_target["total"] == 1

    assert client.get("/api/v1/admin/project-access-log",
                      headers=H(world.people.a)).status_code == 403
    assert client.get("/api/v1/admin/project-access-log").status_code == 401


def test_the_access_log_has_no_route_that_changes_it(client):
    from fastapi.routing import APIRoute
    methods = {m for r in client.app.routes if isinstance(r, APIRoute)
               and r.path.startswith("/api/v1/admin/project-access-log") for m in r.methods}
    assert methods == {"GET"}


# -------------------------------------------------------------- 20: HEAD


def test_head_takes_the_same_access_rule_and_the_same_audit(client, world):
    assert client.head(f"/api/v1/history/{world.b_eval}", headers=H(world.people.a)).status_code == 404
    assert client.head("/api/v1/history").status_code == 401
    r = client.head("/api/v1/history", headers=H(world.people.admin))
    assert r.status_code == 200 and r.content == b""
    assert len(world.events(action="list")) == 1


# ------------------------------------------- 12-14: the save transaction


def test_a_failed_log_row_leaves_no_evaluation(client, world, monkeypatch):
    """Both rows or neither. The log row is made invalid -- pilot with no
    owner, which the CHECK refuses -- so the failure happens at the second row,
    after the evaluation was added: a separate commit would leave it behind."""
    from app.services import evaluation_store

    real = evaluation_store.prediction_log_row

    def broken(**kwargs):
        row = real(**kwargs)
        row.owner_user_id = None
        return row

    monkeypatch.setattr(evaluation_store, "prediction_log_row", broken)
    r = client.post("/api/v1/evaluate?save=true", json=PROJECT, headers=H(world.people.a))
    assert r.status_code == 503, r.text
    assert "evaluation_id" not in r.text
    assert world.count(Evaluation) == 5 and world.count(PredictionLog) == 5


def test_a_warm_result_cache_cannot_answer_a_save(client, world):
    """The old LRU, filled with this exact payload, is not consulted: it used to
    return before either write, and its stored answer carried an id."""
    from app.cache import cache

    planted = {"total_score": -1.0, "saved": True, "evaluation_id": 424242}
    cache.set(cache.make_key("eval", PROJECT), planted, ttl=600)
    try:
        r = client.post("/api/v1/evaluate?save=true", json=PROJECT, headers=H(world.people.a))
        assert r.status_code == 201, r.text
        assert r.json()["evaluation_id"] != 424242 and r.json()["total_score"] != -1.0
        assert world.count(Evaluation, owner_user_id=world.people.a.id) == 2
        assert client.post("/api/v1/evaluate?save=true", json=PROJECT).status_code == 401
    finally:
        cache.clear()


def test_the_same_payload_from_two_owners_is_two_rows(client, world):
    ra = client.post("/api/v1/evaluate?save=true", json=PROJECT, headers=H(world.people.a))
    rb = client.post("/api/v1/evaluate?save=true", json=PROJECT, headers=H(world.people.b))
    a_id, b_id = ra.json()["evaluation_id"], rb.json()["evaluation_id"]
    assert a_id != b_id
    assert a_id in _ids(client.get("/api/v1/history", headers=H(world.people.a)))
    assert b_id not in _ids(client.get("/api/v1/history", headers=H(world.people.a)))
    assert b_id in _ids(client.get("/api/v1/history", headers=H(world.people.b)))


# ------------------------------------- 15-17: shared figures ignore pilot


def _strip(body):
    """A response without the fields that move with the clock."""
    if isinstance(body, dict):
        return {k: _strip(v) for k, v in body.items()
                if k not in ("generated_at", "timestamp", "last_at")}
    if isinstance(body, list):
        return [_strip(v) for v in body]
    return body


SHARED_READS = [
    ("GET", "/api/v1/trends", None),
    ("GET", "/api/v1/analytics/data-health", None),
    ("GET", "/api/v1/analytics/metrics/model-health", "apikey"),
    ("GET", "/api/v1/analytics/summary", "apikey"),
    ("GET", "/api/v1/admin/diagnostics", "admin"),
    ("POST", "/api/v1/admin/ai/report", "admin"),
]


@pytest.mark.parametrize("method,path,auth", SHARED_READS)
def test_a_shared_figure_is_unchanged_by_pilot_rows(client, world, method, path, auth):
    """The same request, answered before and after more pilot rows arrive --
    with a null budget and category and an out-of-range CO2, so the null and
    out-of-range numerators of data health and the summary would move if any
    of their separate queries missed the filter.
    An administrator's Bearer must not widen a shared figure either."""
    headers = {"admin": H(world.people.admin),
               "apikey": {"X-API-Key": os.environ.get("SORA_ADMIN_TOKEN", "ci-test-admin")},
               None: H(world.people.admin)}[auth]

    def ask():
        r = client.request(method, path, headers=headers)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        return _strip(r.json())

    before = ask()
    for i in range(4):
        world.add_pilot(world.people.b, name=f"more-pilot-{i}")
    assert ask() == before


def test_the_drift_window_is_the_newest_public_rows(world):
    """Pilot rows hold the highest ids. A LIMIT applied before the filter would
    take them and leave fewer public rows -- or none -- for the KS test."""
    from app.api import drift

    with world.session() as s:
        newest_public = [_log(s, scope="public", budget=777.0) for _ in range(3)]
        for _ in range(6):
            _log(s, scope="pilot", owner=world.people.a.id, budget=-1.0)
        s.commit()
    frame = drift._recent_predictions(window=3)
    assert len(frame) == 3
    assert list(frame["budget"]) == [777.0, 777.0, 777.0], newest_public


def test_the_drift_analyses_read_public_rows_only(world, monkeypatch, tmp_path):
    """load_training_data decides between the database and the CSV fallback on
    the public count: 3 public and 25 pilot rows is not enough."""
    from app import training

    monkeypatch.setattr(training, "DATA_DIR", str(tmp_path))
    (tmp_path / "sora_dataset.csv").write_text(
        "budget,co2_reduction,social_impact,duration_months,total_score\n1,2,3,4,5\n")
    for i in range(25):
        world.add_pilot(world.people.a, name=f"drift-pilot-{i}")
    df = training.load_training_data()
    assert len(df) == 1, "the CSV fallback was not taken; pilot rows made the database look sufficient"


def test_compare_periods_counts_public_rows_only(world):
    from fastapi import HTTPException
    from app.api.drift_monitor import compare_periods

    for i in range(25):
        world.add_pilot(world.people.a, name=f"cmp-pilot-{i}")
    with pytest.raises(HTTPException) as refused:
        compare_periods()
    assert refused.value.status_code == 400
    assert refused.value.detail == "Not enough data"


def test_the_forecast_series_is_built_from_public_rows(world):
    from app.services.forecasting.data_loader import load_time_series

    with world.session() as s:
        before = load_time_series(s, "score")
    for i in range(5):
        world.add_pilot(world.people.b, name=f"fc-{i}", score=999.0,
                        when=datetime.utcnow() + timedelta(days=10 + i))
    with world.session() as s:
        after = load_time_series(s, "score")
    assert after.equals(before)
    assert int(after["is_observed"].sum()) == 3


def test_the_scheduled_pretrain_counts_what_it_would_train_on(world, monkeypatch):
    """Three public rows and a dozen pilot ones: insufficient, as three is."""
    from app import scheduler

    class _Lock:
        def __init__(self, *a, **k): pass
        def acquire(self): return True
        def release(self): pass

    monkeypatch.setattr("app.locks.RedisLock", _Lock)
    for i in range(12):
        world.add_pilot(world.people.a, name=f"pre-{i}")
    assert scheduler.scheduled_pretrain_forecast_models() == {
        "status": "skipped", "reason": "insufficient_data"}


# ------------------------------------------------ 18: the side channels


@pytest.fixture
def telemetry(monkeypatch):
    from app import mlflow_tracking

    events = []

    class _Fake:
        @staticmethod
        def submit(name, fn, *args, **kwargs):
            events.append(name)
            return True

    monkeypatch.setattr(mlflow_tracking, "telemetry", _Fake())
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    return events


@pytest.mark.parametrize("save", [False, True])
def test_evaluate_feeds_no_shared_channel(client, world, telemetry, monkeypatch, save):
    """No MLflow evaluation event, no drift observation, no platform average --
    for a calculation or a save alike."""
    from app.drift_detection import drift_detector
    from app.middleware import METRICS

    observed = []
    monkeypatch.setattr(drift_detector, "add_observation", lambda f: observed.append(f))
    before = (METRICS.get("evaluations_total"), METRICS.get("evaluations_avg_score"))

    url = "/api/v1/evaluate?save=true" if save else "/api/v1/evaluate"
    r = client.post(url, json=PROJECT, headers=H(world.people.a))
    assert r.status_code in (200, 201), r.text

    assert telemetry == []
    assert observed == []
    assert (METRICS.get("evaluations_total"), METRICS.get("evaluations_avg_score")) == before


@pytest.mark.parametrize("path", ["/api/v1/predict", "/api/v1/predict/stacking"])
def test_a_signed_in_prediction_stays_out_of_every_shared_store(client, world, monkeypatch, path):
    import app.api.predict as predict

    calls = []
    monkeypatch.setattr(predict, "cache_get", lambda k: calls.append("cache_get"))
    monkeypatch.setattr(predict, "cache_set", lambda k, v: calls.append("cache_set"))
    monkeypatch.setattr(predict, "log_prediction", lambda *a, **k: calls.append("mlflow"))
    monkeypatch.setattr(predict, "_log_csv", lambda p: calls.append("csv"))

    r = client.post(path, json=PROJECT, headers=H(world.people.a))
    assert r.status_code == 200, r.text
    assert calls == []

    # The control: the same request without a token still uses all of them,
    # so the empty list above is the gate and not a broken spy.
    client.post(path, json=PROJECT)
    assert "cache_get" in calls and "cache_set" in calls and "mlflow" in calls
    if path == "/api/v1/predict":
        assert "csv" in calls


@pytest.mark.parametrize("path,body", [
    ("/api/v1/what-if", PROJECT),
    ("/api/v1/evaluate/monte-carlo",
     {"project_name": "p", "region": "Sweden", "budget_usd": 100000,
      "co2_reduction_tons_per_year": 150, "social_impact_score": 7,
      "project_duration_months": 24, "n": 50}),
    ("/api/v1/evaluate/ranking", PROJECT),
])
def test_a_signed_in_simulation_sends_nothing_to_the_shared_experiment(
        client, world, telemetry, path, body):
    r = client.post(path, json=body, headers=H(world.people.a))
    assert r.status_code == 200, r.text
    assert telemetry == []
    client.post(path, json=body)
    assert telemetry == ["log_simulation"], "the anonymous control did not log"


def test_a_signed_in_ab_prediction_leaves_the_public_stats_alone(client, world):
    """/ab/stats is public and serves averages of every counted call, so one
    counted signed-in call is that call's exact probability."""
    body = {"budget": 250000, "co2_reduction": 120, "social_impact": 7, "duration_months": 24}
    before = client.get("/api/v1/ab/stats").json()
    r = client.post("/api/v1/ab/predict", json=body, headers=H(world.people.a))
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/ab/stats").json() == before

    assert client.post("/api/v1/ab/predict", json=body,
                       headers={"Authorization": "Bearer not.a.token"}).status_code == 401
    assert client.get("/api/v1/ab/stats").json() == before

    # The control: an anonymous call is still counted, so the equality above
    # is the gate and not a counter that never moves.
    client.post("/api/v1/ab/predict", json=body)
    assert client.get("/api/v1/ab/stats").json() != before


def test_a_signed_in_v2_prediction_is_not_written_to_the_shared_request_log(
        client, world, monkeypatch):
    """The JSONL request log is read back by the public /api/v2/drift routes."""
    import numpy as np

    import app.ml.routes as v2

    class _Model:
        def predict_proba(self, X):
            return np.array([[0.3, 0.7]])

    monkeypatch.setattr(v2, "get_bundle", lambda: {"model": _Model(), "version": 3})
    monkeypatch.setattr(v2, "build_features", lambda payload, bundle: [[0.0]])
    written = []
    monkeypatch.setattr(v2, "log_prediction", lambda **kw: written.append(kw))
    body = {"budget": 250000, "co2_reduction": 120, "social_impact": 7, "duration_months": 24}

    r = client.post("/api/v2/predict", json=body, headers=H(world.people.a))
    assert r.status_code == 200, r.text
    assert written == []
    assert client.post("/api/v2/predict", json=body,
                       headers={"Authorization": "Bearer not.a.token"}).status_code == 401
    assert written == []

    assert client.post("/api/v2/predict", json=body).status_code == 200
    assert len(written) == 1, "the anonymous control was not logged"


# ------------------------------------------- 19: surfaces outside ownership


SPECIALIST_REFUSED = [
    ("POST", "/api/v1/copilot/explain", {"probability": 0.7, "features": {}}),
    ("POST", "/api/v1/copilot/explain/stream", {"probability": 0.7, "features": {}}),
    ("POST", "/api/v1/copilot/qa", {"session_id": "x", "question": "q"}),
    ("GET", "/api/v1/copilot/sessions", None),
    ("GET", "/api/v1/copilot/sessions/x", None),
    ("DELETE", "/api/v1/copilot/sessions/x", None),
    ("POST", "/api/v1/batch/evaluate", {"projects": [PROJECT]}),
    ("GET", "/api/v1/batch", None),
    ("GET", "/api/v1/batch/x", None),
    ("GET", "/api/v1/predictions/history", None),
    ("GET", "/api/v1/predictions/export/csv", None),
]


@pytest.mark.parametrize("method,path,body", SPECIALIST_REFUSED)
def test_a_specialist_is_kept_off_storage_without_an_owner(client, world, method, path, body):
    r = client.request(method, path, json=body, headers=H(world.people.a))
    assert r.status_code == 403, (path, r.status_code)


@pytest.mark.parametrize("path", ["/api/v1/predictions/history", "/api/v1/batch",
                                  "/api/v1/copilot/sessions"])
def test_other_roles_keep_those_surfaces(client, world, path):
    """The control: the refusal is about the specialist role, not a new lock."""
    assert client.get(path, headers=H(world.people.viewer)).status_code != 403


# ---------------------------------------------------- the table refuses


@pytest.mark.parametrize("scope,owner", [("pilot", None), ("public", "someone"),
                                         ("shared", None), ("shared", "someone")])
@pytest.mark.parametrize("model", [Evaluation, PredictionLog])
def test_the_table_refuses_an_owner_and_scope_that_do_not_pair(world, model, scope, owner):
    with world.session() as s:
        s.add(model(data_scope=scope,
                    owner_user_id=world.people.a.id if owner else None))
        with pytest.raises(sa.exc.IntegrityError):
            s.flush()


@pytest.mark.parametrize("model", [Evaluation, PredictionLog])
def test_a_writer_that_names_no_scope_is_refused_not_published(world, model):
    """The default is `pilot`, and pilot needs an owner."""
    with world.session() as s:
        row = model()
        s.add(row)
        with pytest.raises(sa.exc.IntegrityError):
            s.flush()


# ----------------------------------------------------- maintenance writers


SCRIPTS = ["seed_data.py", "scripts/seed_data.py", "scripts/generate_synthetic_forecast_data.py",
           "scripts/cleanup_synthetic_data.py", "scripts/add_historical_evaluations.py",
           "scripts/add_historical_evaluations_remote.sh", "scripts/insert_historical_evals.sh",
           "scripts/populate_forecast_history.py", "scripts/train_forecast_models.py"]


def _python_statement(source, pos):
    """The logical Python statement containing `pos`: from its line's start to
    the newline at which every bracket opened since has closed."""
    start = source.rfind("\n", 0, pos) + 1
    depth = 0
    for i in range(start, len(source)):
        ch = source[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "\n" and i > pos and depth <= 0:
            return source[start:i]
    return source[start:]


def _sql_statement(source, pos):
    """From `pos` to the end of the SQL statement: a `;` or the closing quotes."""
    ends = [i for i in (source.find(";", pos), source.find('"""', pos)) if i != -1]
    return source[pos:min(ends) if ends else pos + 700]


@pytest.mark.parametrize("path", SCRIPTS)
def test_every_maintenance_query_names_the_public_scope(path):
    """Each statement that reads, counts, deletes or inserts evaluations in a
    maintenance script says `public` explicitly. Checked per statement: one
    filtered query in a file proves nothing about the next."""
    source = open(os.path.join(REPO_ROOT, path), encoding="utf-8").read()
    statements = [_sql_statement(source, m.start())
                  for m in re.finditer(r"(FROM|INTO) evaluations\b", source)]
    statements += [_python_statement(source, m.start())
                   for m in re.finditer(r"\.query\(Evaluation\)|\bEvaluation\(", source)]
    assert statements, f"{path} no longer touches evaluations; drop it from this list"
    unscoped = [st.strip().split("\n", 1)[0] for st in statements
                if "data_scope" not in st and "public" not in st]
    assert unscoped == [], f"{path}: statements without the public scope: {unscoped}"


@pytest.mark.parametrize("path", ["scripts/seed_data_v2.py", "scripts/add_data_via_api.sh",
                                  "scripts/add_more_historical_data.sh"])
def test_the_scripts_that_counted_http_200_as_a_row_are_retired(path):
    source = open(os.path.join(REPO_ROOT, path), encoding="utf-8").read()
    assert "RETIRED" in source


def test_the_synthetic_cleanup_spares_a_pilot_project_with_the_same_name(world, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cleanup_synthetic_data", os.path.join(REPO_ROOT, "scripts", "cleanup_synthetic_data.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "SessionLocal", world.Session)
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    with world.session() as s:
        _evaluation(s, scope="public", name="Synthetic Project 1", score=1.0, when=datetime.utcnow())
        mine = _evaluation(s, scope="pilot", owner=world.people.a.id, name="Synthetic Project 2",
                           score=1.0, when=datetime.utcnow())
        s.commit()
    module.cleanup_synthetic_data()
    with world.session() as s:
        names = {(e.name, e.data_scope) for e in s.query(Evaluation).filter(
            Evaluation.name.like("Synthetic Project%"))}
        assert s.get(Evaluation, mine) is not None
    assert names == {("Synthetic Project 2", "pilot")}


# ----------------------------------------------------- the deploy marker


def test_the_marker_holds_back_the_deployment_and_says_why():
    """tests/test_deploy_production.sh proves the refusal; this pins that the
    file is present and its first line is a reason, which is what the refusal
    quotes."""
    with open(os.path.join(REPO_ROOT, ".deploy-blocked"), encoding="utf-8") as fh:
        first = fh.readline().strip()
    assert first == "ownership cutover incomplete; requires PR2c"


def test_a_signed_in_waterfall_neither_reads_nor_writes_the_shared_png_cache(
        client, world, monkeypatch, tmp_path):
    """data/explanations/wf_<hash>.png is served to any later anonymous request
    with the same payload, and its Last-Modified dates the earlier computation."""
    monkeypatch.setenv("SORA_DATA_DIR", str(tmp_path / "data"))
    cache = tmp_path / "data" / "explanations"
    body = {"budget": 250000, "co2_reduction": 120, "social_impact": 7, "duration_months": 24}

    r = client.post("/api/v1/predict/explain/waterfall", json=body, headers=H(world.people.a))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert "no-store" in r.headers["cache-control"]
    assert not cache.exists() or list(cache.iterdir()) == []

    # Nor does it read the cache: a planted file for the same payload is not
    # what a signed-in caller gets back.
    client.post("/api/v1/predict/explain/waterfall", json=body)
    planted = list(cache.iterdir())
    assert len(planted) == 1, "the anonymous control did not write the cache"
    planted[0].write_bytes(b"\x89PNG planted")
    r = client.post("/api/v1/predict/explain/waterfall", json=body, headers=H(world.people.a))
    assert r.status_code == 200 and r.content != b"\x89PNG planted"

    assert client.post("/api/v1/predict/explain/waterfall", json=body,
                       headers={"Authorization": "Bearer not.a.token"}).status_code == 401


# ------------------------------------------------- state shared between threads
#
# Sync handlers run on a thread pool, so two requests in one worker can be in
# the same module-level object at once. Each case below observes the guard at
# the moment the shared object is used: a race reproduced by timing would pass
# or fail by luck.


@pytest.mark.parametrize("signed_in", [True, False])
def test_the_waterfall_draws_only_while_holding_the_pyplot_lock(
        client, world, monkeypatch, tmp_path, signed_in):
    """An anonymous waterfall was measured saving a signed-in caller's budget
    into the shared PNG cache: both drew onto pyplot's one current figure."""
    import matplotlib.pyplot as plt
    import shap

    from app.pyplot_lock import PYPLOT_LOCK

    monkeypatch.setenv("SORA_DATA_DIR", str(tmp_path / "data"))
    real, seen = shap.plots.waterfall, []

    def watched(*args, **kwargs):
        seen.append((PYPLOT_LOCK.locked(), list(plt.get_fignums())))
        return real(*args, **kwargs)

    monkeypatch.setattr(shap.plots, "waterfall", watched)
    headers = H(world.people.a) if signed_in else {}
    r = client.post("/api/v1/predict/explain/waterfall", headers=headers,
                    json={"budget": 98765, "co2_reduction": 120, "social_impact": 7,
                          "duration_months": 24})
    assert r.status_code == 200, r.text
    assert seen == [(True, [])], "drew outside the lock, or onto a figure already open"
    assert plt.get_fignums() == [], "a figure was left open for the next request to draw on"


@pytest.mark.parametrize("path", ["/api/v1/model/reliability-diagram",
                                  "/api/v1/model/ab-comparison/plot"])
def test_the_other_charts_never_touch_pyplot(client, monkeypatch, path):
    """They need no lock because they never register a figure with pyplot,
    where a concurrent shap plot's gcf() could find it."""
    import matplotlib.pyplot as plt

    def refused(*args, **kwargs):
        raise AssertionError("pyplot used outside app/pyplot_lock.py")

    for name in ("subplots", "figure", "savefig", "tight_layout", "gcf"):
        monkeypatch.setattr(plt, name, refused)
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"\x89PNG"


@pytest.mark.parametrize("route", ["local", "global"])
def test_the_shared_kernel_explainer_runs_one_call_at_a_time(client, monkeypatch, route):
    """KernelExplainer keeps a call's samples and outputs on the instance; two
    calls at once were measured handing one caller's contributions to the other."""
    import app.api.explainability as xp

    expl, _kind, _bg = xp._get_explainer()
    real, seen = expl.shap_values, []

    def watched(*args, **kwargs):
        seen.append(xp._EXPLAINER_LOCK.locked())
        return real(*args, **kwargs)

    monkeypatch.setattr(expl, "shap_values", watched)
    if route == "local":
        r = client.post("/api/v1/explain/local?nsamples=20", json={
            "budget": 98765, "co2_reduction": 120, "social_impact": 7, "duration_months": 24})
    else:
        r = client.get("/api/v1/explain/global?nsamples=10")
    assert r.status_code == 200, r.text
    assert seen == [True]


def _sentry_events(options, monkeypatch):
    """Run one failing request through a real Sentry client; return what it sent."""
    import json

    import sentry_sdk
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sentry_sdk.transport import Transport

    sent = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                sent.append(item.payload.get_bytes().decode("utf-8", "replace"))

    class Body(BaseModel):
        budget: float

    probe = FastAPI()

    @probe.post("/boom")
    def boom(body: Body):
        # Built at run time: Sentry also sends the source lines around the
        # failure, and a literal here would be found there, not in the locals.
        probability = "-".join(["PROBABILITY", str(31337)])  # noqa: F841
        raise RuntimeError("boom")

    sentry_sdk.init(dsn="https://key@o0.ingest.sentry.io/1", transport=Capture,
                    default_integrations=True, **options)
    try:
        TestClient(probe, raise_server_exceptions=False).post(
            "/boom", json={"budget": 4242.4242},
            headers={"Authorization": "Bearer SECRET-BEARER"})
        sentry_sdk.flush()
    finally:
        sentry_sdk.init()  # no DSN: a disabled client for the rest of the session
    return "\n".join(sent)


def test_sentry_sends_no_body_no_locals_no_credentials(monkeypatch):
    from app.sentry_options import SENTRY_OPTIONS

    # The control: Sentry's permissive settings do carry all three, so their
    # absence below is the options and not a client that captured nothing.
    loose = _sentry_events({"send_default_pii": True, "include_local_variables": True,
                            "max_request_body_size": "always"}, monkeypatch)
    assert "boom" in loose and "4242.4242" in loose and "PROBABILITY-31337" in loose

    sent = _sentry_events(SENTRY_OPTIONS, monkeypatch)
    assert "boom" in sent, "nothing was reported at all"
    for leaked in ("4242.4242", "PROBABILITY-31337", "SECRET-BEARER"):
        assert leaked not in sent, leaked


def test_the_application_initialises_sentry_with_those_options(monkeypatch):
    from app.sentry_options import SENTRY_OPTIONS, sentry_init_kwargs

    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")
    kwargs = sentry_init_kwargs()
    assert {k: kwargs[k] for k in SENTRY_OPTIONS} == SENTRY_OPTIONS
    import inspect

    import app.main as main
    assert "sentry_sdk.init(**sentry_init_kwargs())" in inspect.getsource(main)
