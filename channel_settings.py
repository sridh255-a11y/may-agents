import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

FIELDS = {"tone": 300, "style": 300, "audience": 300, "language": 60,
          "caption_style": 300, "colors": 200, "rules": 600}
ORDER = ["tone", "style", "audience", "language", "caption_style", "colors", "rules", "topics"]
LABELS = {"tone": "Tone", "style": "Style", "audience": "Audience", "language": "Language",
          "caption_style": "Caption style", "colors": "Colors", "rules": "Rules", "topics": "Topics"}
MAX_TOPICS = 50
MAX_TOPIC_LEN = 200


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _path(db_path):
    if db_path:
        return db_path
    import approval_gate
    return approval_gate.DB_PATH


def _conn(db_path):
    conn = sqlite3.connect(_path(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS channels (project TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT)")
    return conn


def clean_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Settings must be a dict.")
    out = {}
    for k, v in data.items():
        if k == "topics":
            if not isinstance(v, list):
                raise ValueError("topics must be a list.")
            if len(v) > MAX_TOPICS:
                raise ValueError("Too many topics.")
            items = []
            for t in v:
                if not isinstance(t, str):
                    raise ValueError("Each topic must be text.")
                t = t.strip()
                if len(t) > MAX_TOPIC_LEN:
                    raise ValueError("A topic is too long.")
                if t:
                    items.append(t)
            out[k] = items
        elif k in FIELDS:
            if not isinstance(v, str):
                raise ValueError(str(k) + " must be text.")
            v = v.strip()
            if len(v) > FIELDS[k]:
                raise ValueError(str(k) + " is too long.")
            out[k] = v
        else:
            raise ValueError("Unknown field: " + str(k))
    return out


def get_channel(project, db_path=None):
    conn = _conn(db_path)
    try:
        row = conn.execute("SELECT data FROM channels WHERE project = ?", (project,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def set_channel(project, data, db_path=None):
    if not isinstance(project, str) or not project.strip() or len(project.strip()) > 100:
        raise ValueError("Project name is not valid.")
    project = project.strip()
    new = clean_settings(data)
    cur = get_channel(project, db_path)
    for k, v in new.items():
        if v == "" or v == []:
            cur.pop(k, None)
        else:
            cur[k] = v
    conn = _conn(db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO channels (project, data, updated_at) VALUES (?, ?, ?)",
                     (project, json.dumps(cur), _now()))
        conn.commit()
    finally:
        conn.close()
    return cur


def list_channels(db_path=None):
    conn = _conn(db_path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT project FROM channels").fetchall())
    finally:
        conn.close()


def style_text(project, db_path=None):
    s = get_channel(project, db_path)
    lines = []
    for k in ORDER:
        v = s.get(k)
        if not v:
            continue
        if isinstance(v, list):
            v = "; ".join(v)
        lines.append(LABELS[k] + ": " + str(v))
    return "\n".join(lines)


def run_tests():
    results = []

    def check(name, ok):
        results.append(bool(ok))
        if not ok:
            print("FAIL: " + name)

    def raises(fn):
        try:
            fn()
        except ValueError:
            return True
        except Exception:
            return False
        return False

    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "t.db")
        check("empty get", get_channel("P", a) == {})
        set_channel("P", {"tone": "calm", "style": "dark"}, a)
        check("set and get", get_channel("P", a) == {"tone": "calm", "style": "dark"})
        set_channel("P", {"tone": "bold"}, a)
        check("merge keeps older", get_channel("P", a) == {"tone": "bold", "style": "dark"})
        set_channel("P", {"style": ""}, a)
        check("empty removes", get_channel("P", a) == {"tone": "bold"})
        check("unknown rejected", raises(lambda: set_channel("P", {"password": "x"}, a)))
        check("too long rejected", raises(lambda: set_channel("P", {"tone": "a" * 301}, a)))
        check("non-string rejected", raises(lambda: set_channel("P", {"tone": 5}, a)))
        set_channel("P", {"topics": [" a ", "", "b"]}, a)
        check("topics cleaned", get_channel("P", a)["topics"] == ["a", "b"])
        check("topics not list", raises(lambda: set_channel("P", {"topics": "a"}, a)))
        check("too many topics", raises(lambda: set_channel("P", {"topics": ["t"] * 51}, a)))
        check("topic item not string", raises(lambda: set_channel("P", {"topics": [1]}, a)))
        check("empty project", raises(lambda: set_channel("  ", {"tone": "x"}, a)))
        set_channel("Q", {"tone": "other"}, a)
        check("projects isolated", get_channel("P", a)["tone"] == "bold" and get_channel("Q", a)["tone"] == "other")
        check("list channels", list_channels(a) == ["P", "Q"])
        check("style text", "Tone: bold" in style_text("P", a))
        check("style text empty", style_text("Z", a) == "")
    print(str(sum(results)) + " / " + str(len(results)) + " passed")
    return all(results)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "show":
        print(style_text(sys.argv[2]) or "(no settings saved)")
    elif len(sys.argv) == 5 and sys.argv[1] == "set":
        val = sys.argv[4].split(";") if sys.argv[3] == "topics" else sys.argv[4]
        set_channel(sys.argv[2], {sys.argv[3]: val})
        print("saved")
    else:
        sys.exit(0 if run_tests() else 1)