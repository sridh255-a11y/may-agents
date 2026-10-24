import hmac
import http.client
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from http import cookies

sys.path.append(os.getcwd())
from dashboard_api import DashboardApi
from model_client import load_env

HOST = "127.0.0.1"
PORT = 8000
NO_LOGIN = "--no-login" in sys.argv
DASHBOARD_PASSWORD = ""
API = None
MAX_BODY = 200000
MIN_PASSWORD = 8

SESSIONS = set()
FAILED_ATTEMPTS = {}
LOCK = threading.Lock()

HTML_ENTRY = ("dashboard.html", "text/html; charset=utf-8")
STATIC = {
    "/": HTML_ENTRY,
    "/dashboard.html": HTML_ENTRY,
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
}
BAD_CHARS = ("..", "\\", "%")


def check_config(password, no_login, host):
    if no_login:
        if host != "127.0.0.1":
            return "ERROR: --no-login is only allowed when HOST is 127.0.0.1"
        return None
    if not password or len(password) < MIN_PASSWORD:
        return "ERROR: DASHBOARD_PASSWORD is missing or shorter than 8 characters."
    return None


def is_rate_limited(ip):
    now = time.time()
    with LOCK:
        attempts = [t for t in FAILED_ATTEMPTS.get(ip, []) if now - t < 60]
        FAILED_ATTEMPTS[ip] = attempts
        return len(attempts) >= 5


def record_failed_attempt(ip):
    with LOCK:
        FAILED_ATTEMPTS.setdefault(ip, []).append(time.time())


def cookie_token(header):
    try:
        jar = cookies.SimpleCookie(header or "")
    except cookies.CookieError:
        return ""
    morsel = jar.get("session_id")
    return morsel.value if morsel else ""


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MAY"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_json(self, data, status=200, extra=None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        for key, value in (extra or []):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filename, content_type):
        if not os.path.isfile(filename):
            return self.send_json({"error": "Not found"}, 404)
        with open(filename, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(content)

    def host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port)

    def authed(self):
        if NO_LOGIN:
            return True
        token = cookie_token(self.headers.get("Cookie", ""))
        return bool(token) and token in SESSIONS

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, 400
        if length < 0:
            return None, 400
        if length > MAX_BODY:
            return None, 413
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return None, 400
        if not isinstance(data, dict):
            return None, 400
        return data, None

    def guard(self, fn):
        try:
            fn()
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception:
            try:
                self.send_json({"error": "Server error"}, 500)
            except Exception:
                pass

    def do_GET(self):
        self.guard(self._get)

    def do_HEAD(self):
        self.guard(self._get)

    def do_POST(self):
        self.guard(self._post)

    def _get(self):
        if not self.host_ok():
            return self.send_json({"error": "Forbidden"}, 403)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if any(c in path for c in BAD_CHARS):
            return self.send_json({"error": "Not found"}, 404)
        if path.startswith("/api/"):
            if path == "/api/session":
                return self.send_json({"logged_in": self.authed(), "no_login": NO_LOGIN})
            if not self.authed():
                return self.send_json({"error": "Login required"}, 401)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            code, body = API.handle("GET", path, query)
            return self.send_json(body, code)
        entry = STATIC.get(path)
        if entry is None:
            return self.send_json({"error": "Not found"}, 404)
        return self.serve_file(entry[0], entry[1])

    def _post(self):
        if not self.host_ok() or not self.origin_ok():
            return self.send_json({"error": "Forbidden"}, 403)
        path = urllib.parse.urlparse(self.path).path
        if any(c in path for c in BAD_CHARS) or not path.startswith("/api/"):
            return self.send_json({"error": "Not found"}, 404)
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self.send_json({"error": "Content-Type must be application/json"}, 415)
        data, problem = self.read_body()
        if problem:
            return self.send_json({"error": "Bad request body"}, problem)
        ip = self.client_address[0]
        if path == "/api/login":
            return self.login(data, ip)
        if path == "/api/logout":
            return self.logout()
        if not self.authed():
            return self.send_json({"error": "Login required"}, 401)
        code, body = API.handle("POST", path, data)
        return self.send_json(body, code)

    def login(self, data, ip):
        if NO_LOGIN:
            return self.send_json({"status": "success", "no_login": True})
        if is_rate_limited(ip):
            return self.send_json({"error": "Too many failed attempts, wait a minute"}, 429)
        password = data.get("password", "")
        if not isinstance(password, str):
            password = ""
        good = bool(DASHBOARD_PASSWORD) and hmac.compare_digest(
            password.encode("utf-8"), DASHBOARD_PASSWORD.encode("utf-8"))
        if not good:
            record_failed_attempt(ip)
            return self.send_json({"error": "Invalid password"}, 401)
        token = secrets.token_hex(16)
        with LOCK:
            SESSIONS.add(token)
            FAILED_ATTEMPTS.pop(ip, None)
        cookie = "session_id=%s; HttpOnly; SameSite=Strict; Path=/" % token
        return self.send_json({"status": "success"}, 200, [("Set-Cookie", cookie)])

    def logout(self):
        token = cookie_token(self.headers.get("Cookie", ""))
        with LOCK:
            SESSIONS.discard(token)
        expire = "session_id=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        return self.send_json({"status": "logged out"}, 200, [("Set-Cookie", expire)])


def main():
    global DASHBOARD_PASSWORD, API
    if not NO_LOGIN:
        try:
            DASHBOARD_PASSWORD = str(load_env().get("DASHBOARD_PASSWORD", ""))
        except Exception:
            DASHBOARD_PASSWORD = ""
    problem = check_config(DASHBOARD_PASSWORD, NO_LOGIN, HOST)
    if problem:
        print(problem)
        sys.exit(1)
    if NO_LOGIN:
        print("WARNING: --no-login is on. Anyone using this laptop can use the dashboard.")
    API = DashboardApi()
    try:
        server = Server((HOST, PORT), Handler)
    except OSError:
        print("ERROR: could not start on port " + str(PORT) + ". Is the old dashboard still running? Press Ctrl+C in its window first.")
        sys.exit(1)
    print("MAY running on http://" + HOST + ":" + str(PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()


def make_test_api(pending=None):
    store = list(pending or [])
    log = {"approve": [], "reject": [], "add": 0}

    def run(project, request, notes, gate_fn=None):
        gate_id = gate_fn(project, "Writer", {})
        return {"route": "writer", "writer_result": {"title": "T"},
                "checker_verdict": {"verdict": "APPROVE"}, "gate_id": gate_id, "error": None}

    def add(project, request_type, payload):
        log["add"] += 1
        return 7

    def lst():
        return [{"id": i} for i in store]

    def approve(i):
        log["approve"].append(i)
        return True

    def reject(i):
        log["reject"].append(i)
        return True

    api = DashboardApi(run_fn=run, add_fn=add, list_fn=lst, approve_fn=approve,
                       reject_fn=reject, link_fn=lambda: True, background=False)
    return api, log


def call(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or ("127.0.0.1:%d" % port))
    hdrs = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    if method == "POST" and "Content-Length" not in hdrs:
        hdrs["Content-Length"] = str(len(payload or b""))
    for key, value in hdrs.items():
        conn.putheader(key, value)
    conn.endheaders(payload)
    resp = conn.getresponse()
    raw = resp.read()
    status = resp.status
    cookie = resp.getheader("Set-Cookie")
    conn.close()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError:
        parsed = None
    return status, parsed, cookie


def run_tests():
    global DASHBOARD_PASSWORD, NO_LOGIN, API
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    check("missing password refuses to start", check_config("", False, "127.0.0.1") is not None)
    check("short password refuses to start", check_config("short", False, "127.0.0.1") is not None)
    check("--no-login with another host is refused", check_config("", True, "0.0.0.0") is not None)
    check("--no-login on 127.0.0.1 is allowed", check_config("", True, "127.0.0.1") is None)
    check("a good password is allowed", check_config("longenough1", False, "127.0.0.1") is None)

    saved = (DASHBOARD_PASSWORD, NO_LOGIN, API)
    DASHBOARD_PASSWORD = "test-password-123"
    NO_LOGIN = False
    API, log = make_test_api(pending=[5])
    FAILED_ATTEMPTS.clear()
    server = Server((HOST, 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code, body, cookie = call(port, "GET", "/api/status")
        check("API without login gives 401", code == 401)

        code, body, cookie = call(port, "POST", "/api/login", {"password": "wrong"})
        check("wrong password gives 401", code == 401)

        code, body, cookie = call(port, "POST", "/api/login", {"password": "h\u00e9llo"})
        check("non-English wrong password gives 401, no crash", code == 401)

        code, body, cookie = call(port, "POST", "/api/login", {"password": "test-password-123"})
        check("right password gives 200 and a session cookie", code == 200 and bool(cookie) and "HttpOnly" in cookie)
        session = {"Cookie": (cookie or "").split(";")[0]}

        code, body, cookie2 = call(port, "GET", "/api/status", headers=session)
        check("status with a session works and lists 7 projects", code == 200 and len(body["projects"]) == 7)

        code, body, cookie2 = call(port, "POST", "/api/submit", {"project": "Ebooks", "request": "Write a script", "notes": "n"}, headers=session)
        task_id = body.get("task_id") if isinstance(body, dict) else None
        check("submit returns a task id", code == 200 and task_id is not None)

        code, body, cookie2 = call(port, "GET", "/api/task?id=" + str(task_id), headers=session)
        check("the task finishes as done", code == 200 and body["status"] == "done")

        code, body, cookie2 = call(port, "POST", "/api/approve", {"id": 99}, headers=session)
        check("approving an unknown id gives 404 and changes nothing", code == 404 and log["approve"] == [])

        code, body, cookie2 = call(port, "POST", "/api/approve", {"id": 5}, headers=session)
        check("approving a pending item works", code == 200 and log["approve"] == [5])

        code, body, cookie2 = call(port, "POST", "/api/logout", {}, headers=session)
        code2, body2, cookie3 = call(port, "GET", "/api/status", headers=session)
        check("after logout the old session gives 401", code == 200 and code2 == 401)

        for bad in ["/.env", "/model_client.py", "/dashboard.py", "/dashboard_api.py", "/.git/config",
                    "/README.md", "/may_agents.db", "/..%2f..%2fetc%2fpasswd", "/dashboard.html/../secret"]:
            code, body, cookie2 = call(port, "GET", bad)
            check("blocked path " + bad + " gives 404", code == 404)

        code, body, cookie2 = call(port, "GET", "/dashboard.html")
        check("dashboard.html is served", code == 200)

        code, body, cookie2 = call(port, "GET", "/api/session", host="evil.example")
        check("a wrong Host header gives 403", code == 403)

        code, body, cookie2 = call(port, "POST", "/api/login", {"password": "x"}, headers={"Origin": "http://evil.example"})
        check("a wrong Origin header gives 403", code == 403)

        code, body, cookie2 = call(port, "POST", "/api/login", None, headers={"Content-Type": "text/plain"})
        check("a non-JSON POST gives 415", code == 415)

        code, body, cookie2 = call(port, "POST", "/api/login", None, headers={"Content-Type": "application/json", "Content-Length": "9999999"})
        check("an oversized body gives 413", code == 413)

        FAILED_ATTEMPTS.clear()
        for i in range(5):
            call(port, "POST", "/api/login", {"password": "wrong"})
        code, body, cookie2 = call(port, "POST", "/api/login", {"password": "test-password-123"})
        check("after 5 wrong tries even the right password gets 429", code == 429)

        NO_LOGIN = True
        code, body, cookie2 = call(port, "GET", "/api/status")
        check("with --no-login the API is open", code == 200)
    finally:
        server.shutdown()
        server.server_close()
        DASHBOARD_PASSWORD, NO_LOGIN, API = saved
        FAILED_ATTEMPTS.clear()

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_tests()
    else:
        main()