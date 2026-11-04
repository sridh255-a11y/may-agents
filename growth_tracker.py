import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

KEEP_DAYS = 14
MIN_GAP_HOURS = 6
MAX_IDS = 50
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now():
    return datetime.now(timezone.utc)


def _path(db_path):
    if db_path:
        return db_path
    import approval_gate
    return approval_gate.DB_PATH


def _conn(db_path):
    c = sqlite3.connect(_path(db_path))
    c.execute("CREATE TABLE IF NOT EXISTS snapshots (video_id TEXT NOT NULL, taken TEXT NOT NULL, views INTEGER NOT NULL, PRIMARY KEY (video_id, taken))")
    return c


def _parse(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def record(views_by_id, db_path=None, now=None):
    now = now or _now()
    stamp = now.strftime(FMT)
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime(FMT)
    c = _conn(db_path)
    try:
        for vid, views in views_by_id.items():
            if isinstance(vid, str) and vid and isinstance(views, int) and not isinstance(views, bool) and views >= 0:
                c.execute("INSERT OR REPLACE INTO snapshots (video_id, taken, views) VALUES (?, ?, ?)", (vid, stamp, views))
        c.execute("DELETE FROM snapshots WHERE taken < ?", (cutoff,))
        c.commit()
    finally:
        c.close()


def growth(video_ids, db_path=None):
    out = {}
    c = _conn(db_path)
    try:
        for vid in video_ids:
            rows = c.execute("SELECT taken, views FROM snapshots WHERE video_id = ? ORDER BY taken", (vid,)).fetchall()
            if len(rows) < 2:
                continue
            (t0, v0), (t1, v1) = rows[0], rows[-1]
            hours = (_parse(t1) - _parse(t0)).total_seconds() / 3600.0
            if hours < MIN_GAP_HOURS:
                continue
            days = hours / 24.0
            delta = v1 - v0
            out[vid] = {"delta": delta, "per_day": round(delta / days),
                        "pct": round(100.0 * delta / v0, 1) if v0 > 0 else None, "days": round(days, 1)}
    finally:
        c.close()
    return out


def fetch_current(ids, fetch_fn=None):
    ids = [i for i in ids if isinstance(i, str) and i][:MAX_IDS]
    if not ids:
        return {}
    if fetch_fn is None:
        import agent_research
        fetch_fn = agent_research._real_fetch
    data = fetch_fn("videos", {"part": "statistics", "id": ",".join(ids)})
    out = {}
    for it in data.get("items", []):
        try:
            out[it["id"]] = int(it["statistics"]["viewCount"])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def tracked_ids(db_path=None):
    c = _conn(db_path)
    try:
        return [r[0] for r in c.execute("SELECT DISTINCT video_id FROM snapshots ORDER BY video_id LIMIT ?", (MAX_IDS,)).fetchall()]
    finally:
        c.close()


def refresh(db_path=None, fetch_fn=None, now=None):
    ids = tracked_ids(db_path)
    if not ids:
        return 0
    cur = fetch_current(ids, fetch_fn)
    record(cur, db_path, now)
    return len(cur)


def run_tests():
    results = []

    def check(name, ok):
        results.append(bool(ok))
        if not ok:
            print("FAIL: " + name)

    base = datetime(2026, 11, 3, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        n = [0]

        def fresh():
            n[0] += 1
            return os.path.join(d, "t" + str(n[0]) + ".db")

        db = fresh()
        record({"v": 1000}, db, base)
        record({"v": 3000}, db, base + timedelta(days=2))
        check("growth values", growth(["v"], db) == {"v": {"delta": 2000, "per_day": 1000, "pct": 200.0, "days": 2.0}})
        db = fresh()
        record({"v": 1000}, db, base)
        check("one snapshot gives nothing", growth(["v"], db) == {})
        db = fresh()
        record({"v": 1000}, db, base)
        record({"v": 1100}, db, base + timedelta(hours=1))
        check("gap under 6 hours is skipped", growth(["v"], db) == {})
        db = fresh()
        record({"old": 1}, db, base - timedelta(days=20))
        record({"new": 2}, db, base)
        con = sqlite3.connect(db)
        rows = con.execute("SELECT video_id FROM snapshots").fetchall()
        con.close()
        check("old snapshots are deleted", rows == [("new",)])
        db = fresh()
        record({"": 5, "a": -1, "b": True, "c": "9", "ok": 7}, db, base)
        check("bad input ignored", tracked_ids(db) == ["ok"])
        got = fetch_current(["a", "b", "c"], lambda p, q: {"items": [
            {"id": "a", "statistics": {"viewCount": "10"}}, {"id": "b", "statistics": {}}, {"id": "c", "statistics": {"viewCount": "x"}}]})
        check("fetch parses and skips bad", got == {"a": 10})
        sent = []
        fetch_current(["i" + str(i) for i in range(80)], lambda p, q: sent.append(q["id"]) or {"items": []})
        check("at most 50 ids", len(sent) == 1 and len(sent[0].split(",")) == 50)
        db = fresh()
        record({"a": 100, "b": 200}, db, base)
        cnt = refresh(db, lambda p, q: {"items": [{"id": "a", "statistics": {"viewCount": "150"}}, {"id": "b", "statistics": {"viewCount": "300"}}]}, base + timedelta(days=1))
        check("refresh records and counts", cnt == 2 and growth(["a", "b"], db)["a"]["delta"] == 50)
        db = fresh()
        called = []
        check("refresh with nothing tracked", refresh(db, lambda p, q: called.append(1) or {"items": []}, base) == 0 and not called)
        db = fresh()
        record({"z": 0}, db, base)
        record({"z": 50}, db, base + timedelta(days=1))
        check("zero start gives no percent", growth(["z"], db)["z"]["pct"] is None)
    print(str(sum(results)) + " / " + str(len(results)) + " passed")
    return all(results)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "snap":
        print(str(refresh()) + " video(s) updated")
    elif len(sys.argv) == 2 and sys.argv[1] == "show":
        ids = tracked_ids()
        g = growth(ids)
        print(str(len(ids)) + " tracked, " + str(len(g)) + " with growth data")
        for vid, x in sorted(g.items(), key=lambda kv: kv[1]["per_day"], reverse=True)[:10]:
            print(vid + " | +" + str(x["delta"]) + " views | " + str(x["per_day"]) + "/day | " + str(x["pct"]) + "% | over " + str(x["days"]) + " days")
    else:
        sys.exit(0 if run_tests() else 1)