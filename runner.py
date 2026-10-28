import os
import sqlite3
import sys
import tempfile
from datetime import datetime

from model_client import redact
from pipeline import verdict_text, family_of

BUILT = {"write": "Writer", "check": "Checker"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _path(db_path):
    if db_path:
        return db_path
    import approval_gate
    return approval_gate.DB_PATH


def _conn(db_path):
    conn = sqlite3.connect(_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL UNIQUE, status TEXT NOT NULL, started_at TEXT, finished_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS run_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, step_id INTEGER, agent TEXT, title TEXT, status TEXT NOT NULL, detail TEXT, at TEXT)")
    return conn


def _rows(db_path, sql, args=()):
    conn = _conn(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _exec(db_path, sql, args=()):
    conn = _conn(db_path)
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid, cur.rowcount
    finally:
        conn.close()


def load_request(request_id, db_path=None):
    import json
    rows = _rows(db_path, "SELECT id, project, request_type, payload, status FROM requests WHERE id = ?", (request_id,))
    if not rows:
        return None
    row = rows[0]
    try:
        row["payload"] = json.loads(row["payload"])
    except Exception:
        row["payload"] = {}
    return row


def start_run(request_id, db_path=None):
    try:
        return _exec(db_path, "INSERT INTO runs (request_id, status, started_at) VALUES (?, 'running', ?)", (request_id, _now()))[0]
    except sqlite3.IntegrityError:
        return None


def set_run(run_id, status, db_path=None):
    _exec(db_path, "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?", (status, _now(), run_id))


def log_step(run_id, step, status, detail, db_path=None):
    _exec(db_path, "INSERT INTO run_steps (run_id, step_id, agent, title, status, detail, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
          (run_id, step.get("id", 0), str(step.get("agent", "")), str(step.get("title", "")), status, detail, _now()))


def cancel_run(run_id, db_path=None):
    return _exec(db_path, "UPDATE runs SET status = 'cancel_requested' WHERE id = ? AND status = 'running'", (run_id,))[1] == 1


def _cancelled(run_id, db_path):
    rows = _rows(db_path, "SELECT status FROM runs WHERE id = ?", (run_id,))
    return bool(rows) and rows[0]["status"] in ("cancel_requested", "cancelled")


def recover(db_path=None):
    return _exec(db_path, "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status IN ('running', 'cancel_requested')", (_now(),))[1]


def _real_writer(topic, notes):
    import agent_writer
    return agent_writer.write(topic, notes)


def _real_checker(writer_result, notes):
    import agent_checker
    return agent_checker.check_writer_output(writer_result, notes)


def _real_gate(project, request_type, payload):
    import approval_gate
    return approval_gate.add_request(project, request_type, payload)


def run_approved_plan(request_id, notes, writer_fn=None, checker_fn=None, gate_fn=None, load_fn=None, db_path=None):
    writer_fn = writer_fn or _real_writer
    checker_fn = checker_fn or _real_checker
    gate_fn = gate_fn or _real_gate
    load_fn = load_fn or load_request
    out = {"ok": False, "request_id": request_id, "run_id": None, "status": "refused", "message": "", "gate_id": None, "steps": []}
    if not str(notes or "").strip():
        out["message"] = "No source notes. Nothing was run."
        return out
    try:
        row = load_fn(request_id, db_path)
    except Exception as e:
        out["message"] = "Could not read the request: " + redact(type(e).__name__ + ": " + str(e))
        return out
    if not row:
        out["message"] = "No request with that id."
        return out
    if row.get("request_type") != "Plan":
        out["message"] = "That request is not a Plan. Nothing was run."
        return out
    if row.get("status") != "approved":
        out["message"] = "That plan is not approved (status: " + str(row.get("status")) + "). Nothing was run."
        return out
    payload = row.get("payload") or {}
    plan = payload.get("plan") or {}
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps:
        out["message"] = "The plan has no steps. Nothing was run."
        return out
    run_id = start_run(request_id, db_path)
    if run_id is None:
        out["message"] = "This plan already has a run. A plan runs once."
        return out
    out["run_id"] = run_id
    request = str(payload.get("request", ""))
    project = row.get("project", "")
    done = set()
    unbuilt = set()
    written = None
    verdict = None
    final = "done"
    for step in sorted(steps, key=lambda s: s.get("id", 0)):
        sid = step.get("id", 0)
        stype = step.get("type")
        if final == "cancelled" or _cancelled(run_id, db_path):
            final = "cancelled"
            log_step(run_id, step, "cancelled", "Run cancelled.", db_path)
            continue
        if final != "done":
            log_step(run_id, step, "skipped", "Run stopped earlier.", db_path)
            continue
        if stype == "approve":
            log_step(run_id, step, "skipped", "Only you approve. The Runner never approves.", db_path)
        elif stype not in BUILT or BUILT[stype] != step.get("agent"):
            log_step(run_id, step, "skipped", "Not built yet.", db_path)
            unbuilt.add(sid)
        elif any(d not in done and d not in unbuilt for d in (step.get("depends_on") or [])):
            log_step(run_id, step, "skipped", "A step it depends on did not finish.", db_path)
        elif stype == "write":
            try:
                written = writer_fn(request, notes)
            except Exception as e:
                written = {"error": redact(type(e).__name__ + ": " + str(e))}
            if not isinstance(written, dict) or "error" in written:
                detail = str(written.get("error")) if isinstance(written, dict) else "bad result"
                written = None
                final = "failed"
                log_step(run_id, step, "failed", "Writer failed: " + detail, db_path)
            else:
                done.add(sid)
                log_step(run_id, step, "done", "Script written.", db_path)
        elif stype == "check":
            if written is None:
                log_step(run_id, step, "skipped", "No Writer result to check.", db_path)
            else:
                try:
                    verdict = checker_fn(written, notes)
                except Exception as e:
                    verdict = None
                    final = "failed"
                    log_step(run_id, step, "failed", "Checker failed: " + redact(type(e).__name__ + ": " + str(e)), db_path)
                else:
                    label = verdict_text(verdict)
                    if label == "UNCLEAR":
                        final = "failed"
                        log_step(run_id, step, "failed", "Checker gave no clear verdict.", db_path)
                    else:
                        done.add(sid)
                        log_step(run_id, step, "done", "Verdict: " + label, db_path)
    if final != "done":
        out["status"] = final
        out["message"] = "Run cancelled. Nothing was added to the approval gate." if final == "cancelled" else "Run stopped. Nothing was added to the approval gate. See the step log."
    elif written is None or verdict is None:
        final = "failed"
        out["status"] = final
        out["message"] = "The plan had no finished Writer and Checker steps. Nothing was added to the approval gate."
    else:
        label = verdict_text(verdict)
        wf = family_of(written)
        cf = family_of(verdict)
        independent = bool(wf) and bool(cf) and wf != cf
        if label == "APPROVE" and not independent:
            final = "blocked"
            out["status"] = final
            out["message"] = "Approval blocked: the Writer and Checker are not confirmed to be different providers. Nothing was added to the approval gate."
        else:
            item = {"request": request, "route": "runner", "reason": str(plan.get("goal", "")),
                    "writer_result": written, "checker_verdict": verdict,
                    "writer_family": wf, "checker_family": cf, "independent": independent,
                    "plan_request_id": request_id}
            try:
                out["gate_id"] = gate_fn(project, "Writer", item)
            except Exception as e:
                final = "failed"
                out["status"] = final
                out["message"] = "Could not add to the approval gate: " + redact(type(e).__name__ + ": " + str(e))
            else:
                out["ok"] = True
                out["status"] = "done"
                out["message"] = "Verdict: " + label + ". Added to the approval gate as pending. Nothing is published or sent."
    set_run(run_id, final, db_path)
    out["steps"] = _rows(db_path, "SELECT step_id, agent, title, status, detail FROM run_steps WHERE run_id = ? ORDER BY id", (run_id,))
    return out


def run_tests():
    results = []

    def check(name, ok):
        results.append(bool(ok))
        if not ok:
            print("FAIL: " + name)

    def plan(steps=None):
        if steps is None:
            steps = [
                {"id": 1, "type": "write", "agent": "Writer", "title": "Draft", "input": "", "depends_on": [], "available": True},
                {"id": 2, "type": "research", "agent": "Research", "title": "Find", "input": "", "depends_on": [1], "available": False},
                {"id": 3, "type": "check", "agent": "Checker", "title": "Review", "input": "", "depends_on": [1], "available": True},
                {"id": 4, "type": "approve", "agent": "Approval gate", "title": "Approve", "input": "", "depends_on": [3], "available": True},
            ]
        return {"goal": "g", "steps": steps}

    def row(rid=1, rtype="Plan", status="approved", p=None):
        return {"id": rid, "project": "P", "request_type": rtype, "status": status,
                "payload": {"request": "make video", "plan": p if p is not None else plan()}}

    class Gate:
        def __init__(self):
            self.calls = []

        def __call__(self, project, request_type, payload):
            self.calls.append((project, request_type, payload))
            return 99

    def good_w(t, n):
        return {"title": "T", "script": "s", "claims": [], "family": "google"}

    def good_c(w, n):
        return {"verdict": "APPROVE", "family": "groq"}

    with tempfile.TemporaryDirectory() as d:
        counter = [0]

        def fresh():
            counter[0] += 1
            return os.path.join(d, "t" + str(counter[0]) + ".db")

        def go(r, notes="n", writer=good_w, checker=good_c, db=None):
            g = Gate()
            db = db or fresh()
            out = run_approved_plan(r["id"], notes, writer_fn=writer, checker_fn=checker, gate_fn=g,
                                    load_fn=lambda rid, dbp: r if rid == r["id"] else None, db_path=db)
            return out, g, db

        out, g, db = go(row())
        check("happy path is done", out["ok"] and out["status"] == "done")
        check("gate called once", len(g.calls) == 1 and out["gate_id"] == 99)
        check("gate type is Writer with payload", g.calls[0][1] == "Writer" and g.calls[0][2]["request"] == "make video" and g.calls[0][2]["checker_verdict"]["verdict"] == "APPROVE")
        check("step statuses", {s["step_id"]: s["status"] for s in out["steps"]} == {1: "done", 2: "skipped", 3: "done", 4: "skipped"})
        again = run_approved_plan(1, "n", writer_fn=good_w, checker_fn=good_c, gate_fn=g, load_fn=lambda rid, dbp: row(), db_path=db)
        check("second run refused", again["status"] == "refused" and len(g.calls) == 1)
        out, g, db = go(row(rtype="Writer"))
        check("not a Plan refused", out["status"] == "refused" and not g.calls)
        out, g, db = go(row(status="pending"))
        check("not approved refused", out["status"] == "refused" and not g.calls)
        g = Gate()
        out = run_approved_plan(7, "n", writer_fn=good_w, checker_fn=good_c, gate_fn=g, load_fn=lambda rid, dbp: None, db_path=fresh())
        check("missing row refused", out["status"] == "refused")
        out, g, db = go(row(), notes="   ")
        check("empty notes refused, no run recorded", out["status"] == "refused" and _rows(db, "SELECT id FROM runs") == [])
        out, g, db = go(row(), checker=lambda w, n: {"verdict": "APPROVE", "family": "google"})
        check("same provider blocked", out["status"] == "blocked" and not g.calls)
        called = []
        out, g, db = go(row(), writer=lambda t, n: {"error": "boom"}, checker=lambda w, n: called.append(1))
        check("writer error fails, checker not called", out["status"] == "failed" and not g.calls and not called)

        def bad_writer(t, n):
            raise RuntimeError("down")

        out, g, db = go(row(), writer=bad_writer)
        check("writer exception fails", out["status"] == "failed" and not g.calls)
        out, g, db = go(row(), checker=lambda w, n: {"verdict": "maybe", "family": "groq"})
        check("unclear verdict fails", out["status"] == "failed" and not g.calls)
        out, g, db = go(row(), checker=lambda w, n: {"verdict": "REJECT", "family": "groq"})
        check("REJECT still goes to gate as pending", out["ok"] and len(g.calls) == 1)
        db = fresh()

        def cancel_writer(t, n):
            rid = _rows(db, "SELECT id FROM runs ORDER BY id DESC LIMIT 1")[0]["id"]
            cancel_run(rid, db)
            return good_w(t, n)

        out, g, db = go(row(), writer=cancel_writer, db=db)
        check("cancel stops later steps", out["status"] == "cancelled" and not g.calls and [s["status"] for s in out["steps"]] == ["done", "cancelled", "cancelled", "cancelled"])
        dep = plan([
            {"id": 1, "type": "write", "agent": "Writer", "title": "W", "input": "", "depends_on": [], "available": True},
            {"id": 2, "type": "research", "agent": "Research", "title": "R", "input": "", "depends_on": [1], "available": False},
            {"id": 3, "type": "check", "agent": "Checker", "title": "C", "input": "", "depends_on": [2], "available": True},
        ])
        out, g, db = go(row(p=dep))
        check("unbuilt dependency does not block the check", out["steps"][2]["status"] == "done" and len(g.calls) == 1)
        db = fresh()
        rid = start_run(5, db)
        check("recover marks interrupted", recover(db) == 1 and _rows(db, "SELECT status FROM runs WHERE id = ?", (rid,))[0]["status"] == "interrupted")
        out, g, db = go(row(p={"goal": "x", "steps": []}))
        check("plan with no steps refused", out["status"] == "refused" and not g.calls)
    print(str(sum(results)) + " / " + str(len(results)) + " passed")
    return all(results)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "run":
        with open(sys.argv[3], "r", encoding="utf-8") as f:
            text = f.read()
        res = run_approved_plan(int(sys.argv[2]), text)
        print(res["status"] + ": " + res["message"])
        for s in res["steps"]:
            print(str(s["step_id"]) + " " + s["agent"] + " " + s["status"] + " - " + str(s["detail"]))
    elif len(sys.argv) == 2 and sys.argv[1] == "recover":
        print(str(recover()) + " run(s) marked interrupted")
    else:
        sys.exit(0 if run_tests() else 1)