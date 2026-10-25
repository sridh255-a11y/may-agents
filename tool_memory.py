import os
import re
import sqlite3
import threading

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "may_memory.db")
MAX_TEXT = 1000
MAX_PER_PROJECT = 500
KEY_PREFIX = re.compile(r"(?<![A-Za-z0-9])(freellmapi-|sk-|gsk_|ghp_|github_pat_|xoxb-|AIza)[A-Za-z0-9_\-]{8,}")
LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{32,}")


def clean_text(text):
    text = "".join(" " if ord(ch) < 32 else ch for ch in str(text))
    return " ".join(text.split())


def looks_like_secret(text):
    return bool(KEY_PREFIX.search(text) or LONG_TOKEN.search(text))


class Memory:
    def __init__(self, db_path=None):
        self.path = db_path or DEFAULT_DB
        self.max_per_project = MAX_PER_PROJECT
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "project TEXT NOT NULL, "
                "text TEXT NOT NULL, "
                "created TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            self.conn.commit()

    def remember(self, project, text):
        project = clean_text(project)[:60]
        text = clean_text(text)
        if not project:
            return None, "Project is empty"
        if not text:
            return None, "Nothing to remember"
        if len(text) > MAX_TEXT:
            return None, "Too long (max 1000 characters)"
        if looks_like_secret(text):
            return None, "This looks like a key or password. Not stored."
        with self.lock:
            count = self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE project = ?", (project,)
            ).fetchone()[0]
            if count >= self.max_per_project:
                return None, "Memory is full for this project"
            cur = self.conn.execute(
                "INSERT INTO memories (project, text) VALUES (?, ?)", (project, text)
            )
            self.conn.commit()
            return cur.lastrowid, None

    def list_items(self, project=None):
        with self.lock:
            if project is None:
                rows = self.conn.execute(
                    "SELECT id, project, text, created FROM memories ORDER BY id"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT id, project, text, created FROM memories WHERE project = ? ORDER BY id",
                    (clean_text(project)[:60],),
                ).fetchall()
        return [dict(r) for r in rows]

    def _scope_rows(self, project):
        project = clean_text(project)[:60]
        with self.lock:
            return self.conn.execute(
                "SELECT id, project, text FROM memories WHERE project IN (?, 'ALL') ORDER BY id",
                (project,),
            ).fetchall()

    def search(self, project, words):
        needle = clean_text(words).lower()
        if not needle:
            return []
        return [dict(r) for r in self._scope_rows(project) if needle in r["text"].lower()]

    def forget(self, mem_id):
        try:
            mem_id = int(mem_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def as_notes(self, project, max_chars=20000):
        lines = ["[#%d] %s" % (r["id"], r["text"]) for r in self._scope_rows(project)]
        kept = []
        total = 0
        for line in reversed(lines):
            add = len(line) + (1 if kept else 0)
            if total + add > max_chars:
                break
            kept.append(line)
            total += add
        return "\n".join(reversed(kept))

    def close(self):
        with self.lock:
            self.conn.close()


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    m = Memory(":memory:")
    i1, err = m.remember("Ebooks", "Target audience is students in India")
    check("remember stores a fact and returns an id", i1 == 1 and err is None)

    i, err = m.remember("Ebooks", "   ")
    check("empty text is refused", i is None and bool(err))

    i, err = m.remember("Ebooks", "x" * 1001)
    check("text over 1000 characters is refused", i is None and bool(err))

    i, err = m.remember("Ebooks", "my key is freellmapi-0123456789abcdef")
    check("key-like text is refused", i is None and bool(err))

    i, err = m.remember("Ebooks", "token abcdefghijklmnopqrstuvwxyz0123456789")
    check("a long token is refused", i is None and bool(err))

    i2, err = m.remember("Ebooks", "The task-force disk-based plan is fine")
    check("normal words containing sk- are allowed", i2 == 2 and err is None)

    m.remember("ALL", "Never publish without approval")
    m.remember("Money-Making App", "Use Razorpay")

    check("list shows only that project", len(m.list_items("Ebooks")) == 2)

    notes = m.as_notes("Ebooks")
    check("notes include the project and ALL, not other projects",
          "students in India" in notes and "Never publish" in notes and "Razorpay" not in notes)
    check("notes are numbered like [#1]", notes.startswith("[#1] "))

    check("search is case-insensitive", len(m.search("Ebooks", "STUDENTS")) == 1)

    check("forget removes one item", m.forget(1) is True and len(m.list_items("Ebooks")) == 1)
    check("forget with an unknown or bad id returns False", m.forget(999) is False and m.forget("abc") is False)

    m.remember("Ebooks", "a\x00b\x07c")
    check("control characters are cleaned", m.list_items("Ebooks")[-1]["text"] == "a b c")

    m2 = Memory(":memory:")
    for k in range(5):
        m2.remember("P", "fact number %d" % k)
    short = m2.as_notes("P", max_chars=40)
    check("a size limit keeps the newest facts", "fact number 4" in short and "fact number 0" not in short)

    m3 = Memory(":memory:")
    m3.max_per_project = 3
    ids = [m3.remember("P", "fact %d" % k)[0] for k in range(4)]
    check("a full project refuses new facts", ids[:3] == [1, 2, 3] and ids[3] is None)

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


if __name__ == "__main__":
    run_tests()