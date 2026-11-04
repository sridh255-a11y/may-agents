import threading
import time
import urllib.request

PROJECTS = [
    "YouTube RankPeak",
    "The Inside Story",
    "Modern Leverage AI",
    "AI Resume Website",
    "Money-Making App",
    "Affiliate Marketing",
    "Ebooks",
]

AGENTS = [
    {"name": "Manager", "built": True, "job": "Routes each request to one agent"},
    {"name": "Writer", "built": True, "job": "Drafts scripts from your source notes only"},
    {"name": "Checker", "built": True, "job": "Reviews the Writer output on a different provider"},
    {"name": "Analytics", "built": False, "job": "Not built yet"},
    {"name": "SEO", "built": False, "job": "Not built yet"},
    {"name": "Voice", "built": False, "job": "Not built yet"},
    {"name": "Video", "built": False, "job": "Not built yet"},
    {"name": "Design", "built": False, "job": "Not built yet"},
    {"name": "Animation", "built": False, "job": "Not built yet"},
]

MAX_RUNNING = 2
MAX_REQUEST_CHARS = 2000
MAX_NOTES_CHARS = 20000
KEEP_TASKS = 50


def _real_run(project, request, notes, gate_fn=None):
    import pipeline
    return pipeline.run_request(project, request, notes, gate_fn=gate_fn)


def _real_plan(project, request, notes, gate_fn=None):
    import dashboard_plan
    return dashboard_plan.run_plan(project, request, notes, gate_fn=gate_fn)


def _real_add(project, request_type, payload):
    import approval_gate
    return approval_gate.add_request(project, request_type, payload)


def _real_list():
    import approval_gate
    return approval_gate.list_pending()


def _real_approve(req_id):
    import approval_gate
    return approval_gate.approve_request(req_id)


def _real_reject(req_id):
    import approval_gate
    return approval_gate.reject_request(req_id)


def _real_link():
    from model_client import load_env
    env = load_env()
    url = env["MODEL_BASE_URL"].rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + env["MODEL_API_KEY"]})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status == 200


def _real_channel_get(project):
    import channel_settings
    return channel_settings.get_channel(project)


def _real_channel_set(project, settings):
    import channel_settings
    return channel_settings.set_channel(project, settings)


def _real_seo(project, script, title):
    import agent_seo
    import channel_settings
    return agent_seo.seo_pack(script, title=title, style=channel_settings.style_text(project))


def _real_trends(topic, region, days, min_sec=None, max_sec=None):
    import agent_research
    return agent_research.trend_report(topic, region=region, days=days, min_sec=min_sec, max_sec=max_sec)


MAX_TRENDS_PER_RUN = 20
MAX_BRIEFS_PER_RUN = 5


def _real_brief(project, region, days, length):
    import agent_trendbrief
    return agent_trendbrief.brief_with_outline(project, region=region, days=days, length=length)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class DashboardApi:
    def __init__(self, run_fn=None, add_fn=None, list_fn=None, approve_fn=None, reject_fn=None, link_fn=None, background=True, plan_fn=None, channel_get_fn=None, channel_set_fn=None, seo_fn=None, trends_fn=None, brief_fn=None):
        self.run_fn = run_fn or _real_run; self.plan_fn = plan_fn or _real_plan
        self.add_fn = add_fn or _real_add
        self.channel_get_fn = channel_get_fn or _real_channel_get
        self.channel_set_fn = channel_set_fn or _real_channel_set
        self.seo_fn = seo_fn or _real_seo
        self.trends_fn = trends_fn or _real_trends
        self.trend_calls = 0
        self.brief_fn = brief_fn or _real_brief
        self.brief_calls = 0
        self.list_fn = list_fn or _real_list
        self.approve_fn = approve_fn or _real_approve
        self.reject_fn = reject_fn or _real_reject
        self.link_fn = link_fn or _real_link
        self.background = background
        self.lock = threading.Lock()
        self.tasks = {}
        self.next_id = 1
        self.link_cache = (0.0, False)

    def _public(self, task):
        return {
            "id": task["id"],
            "project": task["project"],
            "request": task["request"],
            "status": task["status"],
            "result": task["result"],
            "created": task["created"],
            "finished": task["finished"],
        }

    def _link_ok(self):
        stamp, value = self.link_cache
        if time.time() - stamp < 10:
            return value
        try:
            value = bool(self.link_fn())
        except Exception:
            value = False
        self.link_cache = (time.time(), value)
        return value

    def status(self):
        try:
            pending = len(self.list_fn())
        except Exception:
            pending = None
        with self.lock:
            running = sum(1 for t in self.tasks.values() if t["status"] in ("queued", "running"))
        return 200, {
            "server": "ok",
            "model_link": self._link_ok(),
            "pending": pending,
            "running": running,
            "projects": PROJECTS,
            "agents": AGENTS,
            "time": time.time(),
        }

    def submit(self, data):
        project = data.get("project")
        request = data.get("request")
        notes = data.get("notes", ""); mode = data.get("mode", "run")
        if project not in PROJECTS or mode not in ("run", "plan"):
            return 400, {"error": "Unknown project"}
        if not isinstance(request, str) or not request.strip():
            return 400, {"error": "Request is empty"}
        if len(request) > MAX_REQUEST_CHARS:
            return 400, {"error": "Request is too long"}
        if not isinstance(notes, str) or len(notes) > MAX_NOTES_CHARS:
            return 400, {"error": "Source notes are invalid or too long"}
        with self.lock:
            running = sum(1 for t in self.tasks.values() if t["status"] in ("queued", "running"))
            if running >= MAX_RUNNING:
                return 429, {"error": "Too many tasks running, wait for one to finish"}
            task_id = self.next_id
            self.next_id += 1
            self.tasks[task_id] = {
                "id": task_id,
                "project": project,
                "request": request.strip(), "mode": mode,
                "notes": notes,
                "status": "queued",
                "cancelled": False,
                "result": None,
                "created": time.time(),
                "finished": None,
            }
            finished_ids = [k for k in sorted(self.tasks) if self.tasks[k]["status"] in ("done", "error", "cancelled")]
            for old in finished_ids[:-KEEP_TASKS]:
                del self.tasks[old]
        if self.background:
            threading.Thread(target=self._work, args=(task_id,), daemon=True).start()
        else:
            self._work(task_id)
        return 200, {"task_id": task_id}

    def _work(self, task_id):
        task = self.tasks[task_id]
        with self.lock:
            if task["cancelled"]:
                return
            task["status"] = "running"

        def gate(project, request_type, payload):
            if task["cancelled"]:
                raise RuntimeError("Task was cancelled, nothing added to the approval gate")
            return self.add_fn(project, request_type, payload)

        try:
            result = (self.plan_fn if task.get("mode") == "plan" else self.run_fn)(task["project"], task["request"], task["notes"], gate_fn=gate)
        except Exception as e:
            result = {"error": type(e).__name__ + ": " + str(e)}
        with self.lock:
            if task["cancelled"]:
                task["status"] = "cancelled"
            elif isinstance(result, dict) and result.get("error"):
                task["status"] = "error"
            else:
                task["status"] = "done"
            task["result"] = result
            task["finished"] = time.time()

    def cancel(self, data):
        task_id = _to_int(data.get("id"))
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return 404, {"error": "No such task"}
            if task["status"] not in ("queued", "running"):
                return 400, {"error": "Task already finished"}
            task["cancelled"] = True
            task["status"] = "cancelled"
        return 200, {"ok": True, "status": "cancelled"}

    def task_get(self, data):
        task_id = _to_int(data.get("id"))
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return 404, {"error": "No such task"}
            return 200, self._public(task)

    def tasks_list(self):
        with self.lock:
            return [self._public(self.tasks[k]) for k in sorted(self.tasks, reverse=True)]

    def decide(self, data, action):
        req_id = _to_int(data.get("id"))
        if req_id is None:
            return 400, {"error": "Bad id"}
        pending_ids = [p.get("id") for p in self.list_fn()]
        if req_id not in pending_ids:
            return 404, {"error": "No pending item with that id"}
        if action == "approve":
            self.approve_fn(req_id)
            return 200, {"ok": True, "status": "approved", "id": req_id}
        self.reject_fn(req_id)
        return 200, {"ok": True, "status": "rejected", "id": req_id}

    def seo(self, data):
        project = data.get("project")
        script = data.get("script")
        title = data.get("title", "")
        if project not in PROJECTS:
            return 400, {"error": "Unknown project"}
        if not isinstance(script, str) or not script.strip():
            return 400, {"error": "Script is empty"}
        if len(script) > MAX_NOTES_CHARS or not isinstance(title, str) or len(title) > 300:
            return 400, {"error": "Script or title is invalid or too long"}
        res = self.seo_fn(project, script, title)
        if not isinstance(res, dict) or "error" in res:
            return 502, {"error": str((res or {}).get("error", "SEO failed")) if isinstance(res, dict) else "SEO failed"}
        return 200, {"project": project, "seo": res}

    def trends(self, data):
        project = data.get("project")
        topic = data.get("topic")
        region = data.get("region", "")
        days = data.get("days", 30)
        if project not in PROJECTS:
            return 400, {"error": "Unknown project"}
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 100:
            return 400, {"error": "Topic is empty or too long"}
        if not isinstance(region, str) or not isinstance(days, int) or isinstance(days, bool):
            return 400, {"error": "Region or days is invalid"}
        length = data.get("length", "any")
        rng = {"any": (None, None), "2-5": (120, 300), "8-15": (480, 900), "15+": (900, None)}.get(length) if isinstance(length, str) else None
        if rng is None:
            return 400, {"error": "Length is invalid"}
        with self.lock:
            if self.trend_calls >= MAX_TRENDS_PER_RUN:
                return 429, {"error": "Trend search limit reached for this session. Restart the dashboard to reset."}
            self.trend_calls += 1
        res = self.trends_fn(topic, region, days, rng[0], rng[1])
        if not isinstance(res, dict) or "error" in res:
            return 502, {"error": str(res.get("error", "Trend search failed")) if isinstance(res, dict) else "Trend search failed"}
        return 200, {"project": project, "trends": res}

    def brief(self, data):
        project = data.get("project")
        region = data.get("region", "")
        days = data.get("days", 30)
        length = data.get("length", "8-15")
        if project not in PROJECTS:
            return 400, {"error": "Unknown project"}
        if not isinstance(region, str) or len(region) > 2:
            return 400, {"error": "Region is invalid"}
        if days not in (30, 90, 365) or isinstance(days, bool):
            return 400, {"error": "Days is invalid"}
        if length not in ("any", "2-5", "8-15", "15+"):
            return 400, {"error": "Length is invalid"}
        with self.lock:
            if self.brief_calls >= MAX_BRIEFS_PER_RUN:
                return 429, {"error": "Brief limit reached for this session. Restart the dashboard to reset."}
            self.brief_calls += 1
        res = self.brief_fn(project, region, days, length)
        if not isinstance(res, dict) or "error" in res:
            return 502, {"error": str(res.get("error", "Brief failed")) if isinstance(res, dict) else "Brief failed"}
        return 200, {"project": project, "brief": res}

    def channel_get(self, data):
        project = data.get("project")
        if project not in PROJECTS:
            return 400, {"error": "Unknown project"}
        return 200, {"project": project, "settings": self.channel_get_fn(project)}

    def channel_set(self, data):
        project = data.get("project")
        settings = data.get("settings")
        if project not in PROJECTS:
            return 400, {"error": "Unknown project"}
        if not isinstance(settings, dict):
            return 400, {"error": "Settings must be an object"}
        try:
            saved = self.channel_set_fn(project, settings)
        except ValueError as e:
            return 400, {"error": str(e)}
        return 200, {"ok": True, "project": project, "settings": saved}

    def handle(self, method, path, data=None):
        data = data if isinstance(data, dict) else {}
        try:
            if method == "GET":
                if path == "/api/status":
                    return self.status()
                if path == "/api/pending":
                    return 200, {"items": self.list_fn()}
                if path == "/api/tasks":
                    return 200, {"tasks": self.tasks_list()}
                if path == "/api/task":
                    return self.task_get(data)
                if path == "/api/channel":
                    return self.channel_get(data)
            if method == "POST":
                if path == "/api/submit":
                    return self.submit(data)
                if path == "/api/cancel":
                    return self.cancel(data)
                if path == "/api/approve":
                    return self.decide(data, "approve")
                if path == "/api/reject":
                    return self.decide(data, "reject")
                if path == "/api/channel":
                    return self.channel_set(data)
                if path == "/api/seo":
                    return self.seo(data)
                if path == "/api/trends":
                    return self.trends(data)
                if path == "/api/brief":
                    return self.brief(data)
        except Exception as e:
            return 500, {"error": "Server error: " + type(e).__name__}
        return 404, {"error": "Not found"}


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    def make(verdict="APPROVE", pending=None):
        calls = {"add": 0, "approve": [], "reject": []}
        store = list(pending or [])

        def run(project, request, notes, gate_fn=None):
            gate_id = gate_fn(project, "Writer", {"x": 1})
            return {
                "route": {"route": "writer"},
                "writer_result": {"title": "T"},
                "checker_verdict": {"verdict": verdict},
                "gate_id": gate_id,
            }

        def add(project, request_type, payload):
            calls["add"] += 1
            return 7

        def lst():
            return [{"id": i} for i in store]

        def approve(i):
            calls["approve"].append(i)
            return True

        def reject(i):
            calls["reject"].append(i)
            return True

        api = DashboardApi(run_fn=run, add_fn=add, list_fn=lst, approve_fn=approve,
                           reject_fn=reject, link_fn=lambda: True, background=False)
        return api, calls

    good = {"project": "Ebooks", "request": "Write a script", "notes": "some notes"}

    api, calls = make()
    code, body = api.handle("POST", "/api/submit", good)
    code2, task = api.handle("GET", "/api/task", {"id": body.get("task_id")})
    check("submit runs the pipeline and returns a done task", code == 200 and code2 == 200 and task["status"] == "done" and task["result"]["gate_id"] == 7)

    api, calls = make("REJECT")
    code, body = api.handle("POST", "/api/submit", good)
    code2, task = api.handle("GET", "/api/task", {"id": body.get("task_id")})
    check("a REJECT verdict stays visible in the result", task["result"]["checker_verdict"]["verdict"] == "REJECT" and calls["add"] == 1)

    api, calls = make()
    code, body = api.handle("POST", "/api/submit", {"project": "Nope", "request": "x", "notes": ""})
    check("unknown project gives 400", code == 400)

    code, body = api.handle("POST", "/api/submit", {"project": "Ebooks", "request": "   ", "notes": ""})
    check("empty request gives 400", code == 400)

    code, body = api.handle("POST", "/api/submit", {"project": "Ebooks", "request": "x" * 3000, "notes": ""})
    check("too long request gives 400", code == 400)

    api, calls = make(pending=[5])
    code, body = api.handle("POST", "/api/approve", {"id": 99})
    check("approving an unknown id gives 404 and changes nothing", code == 404 and calls["approve"] == [])

    code, body = api.handle("POST", "/api/approve", {"id": 5})
    check("approve works on a pending item", code == 200 and calls["approve"] == [5])

    api, calls = make(pending=[6])
    code, body = api.handle("POST", "/api/reject", {"id": 6})
    check("reject works on a pending item", code == 200 and calls["reject"] == [6])

    api, calls = make()

    def run_cancel(project, request, notes, gate_fn=None):
        api.cancel({"id": 1})
        return {"gate_id": gate_fn(project, "Writer", {})}

    api.run_fn = run_cancel
    api.handle("POST", "/api/submit", good)
    code, task = api.handle("GET", "/api/task", {"id": 1})
    check("a cancelled task never reaches the approval gate", task["status"] == "cancelled" and calls["add"] == 0)

    api, calls = make()

    def run_boom(project, request, notes, gate_fn=None):
        raise RuntimeError("boom")

    api.run_fn = run_boom
    api.handle("POST", "/api/submit", good)
    code, task = api.handle("GET", "/api/task", {"id": 1})
    check("an exception becomes an error task, not a crash", task["status"] == "error")

    api, calls = make()
    code, body = api.handle("GET", "/api/nothing")
    check("unknown path gives 404", code == 404)

    code, body = api.handle("GET", "/api/status")
    check("status reports 7 projects and the model link", code == 200 and len(body["projects"]) == 7 and body["model_link"] is True)

    api, calls = make(pending=[3, 4])
    code, body = api.handle("GET", "/api/pending")
    check("pending list returns the items", code == 200 and len(body["items"]) == 2)

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


if __name__ == "__main__":
    run_tests()