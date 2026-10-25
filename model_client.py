
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

SYSTEM_RULE = (
    "Answer only with information you can support. "
    "If a claim has no source, write NOT FOUND instead of guessing."
)

RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_WAIT = 20.0
DEFAULT_TIMEOUT = 120
KEY_PATTERN = re.compile(r"(freellmapi-|sk-|gsk_|ghp_|github_pat_|xoxb-|AIza)[A-Za-z0-9_\-]{8,}")


class ModelError(RuntimeError):
    def __init__(self, message, status=None, retry_after=None, retryable=False):
        RuntimeError.__init__(self, message)
        self.status = status
        self.retry_after = retry_after
        self.retryable = retryable


class BudgetExceeded(ModelError):
    pass


class Budget:
    def __init__(self, max_calls):
        self.max_calls = max_calls
        self.used = 0

    def spend(self):
        if self.used >= self.max_calls:
            raise BudgetExceeded("Model call budget of %d used up" % self.max_calls)
        self.used += 1


def redact(text):
    return KEY_PATTERN.sub("[redacted]", str(text))


def load_env():
    values = {}
    with open(ENV_FILE, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def env_list(key, default, env=None):
    try:
        source = env if env is not None else load_env()
        raw = str(source.get(key, ""))
    except Exception:
        raw = ""
    items = [part.strip() for part in raw.split(",") if part.strip()]
    return items or list(default)


def _post(url, api_key, body, timeout):
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        retry_after = None
        try:
            retry_after = float(e.headers.get("Retry-After"))
        except (TypeError, ValueError):
            retry_after = None
        raise ModelError("HTTP " + str(e.code), status=e.code, retry_after=retry_after,
                         retryable=e.code in RETRY_STATUS)
    except TimeoutError:
        raise ModelError("Timeout", retryable=True)
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise ModelError("Timeout", retryable=True)
        raise ModelError("Connection failed: check FreeLLMAPI is running")
    except ValueError:
        raise ModelError("Bad model response")


def _parse(data):
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ModelError("Bad model response")
    if not isinstance(text, str) or not text.strip():
        raise ModelError("Empty model reply")
    return text


def ask(user_prompt, system_prompt=SYSTEM_RULE, model=None, retries=2, budget=None,
        post_fn=None, sleep_fn=None, env=None):
    post_fn = post_fn or _post
    sleep_fn = sleep_fn or time.sleep
    env = env if env is not None else load_env()
    base_url = env["MODEL_BASE_URL"].rstrip("/")
    api_key = env["MODEL_API_KEY"]
    model_name = model or env["MODEL_NAME"]
    body = json.dumps({
        "model": model_name,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }).encode("utf-8")
    url = base_url + "/chat/completions"
    attempt = 0
    while True:
        if budget is not None:
            budget.spend()
        try:
            return _parse(post_fn(url, api_key, body, DEFAULT_TIMEOUT))
        except ModelError as e:
            if not e.retryable or attempt >= retries:
                raise
            wait = e.retry_after if e.retry_after is not None else 2.0 * (2 ** attempt)
            sleep_fn(min(wait, MAX_WAIT))
            attempt += 1


def ask_chain(user_prompt, system_prompt=SYSTEM_RULE, models=None, budget=None, max_calls=4,
              post_fn=None, sleep_fn=None, env=None):
    env = env if env is not None else load_env()
    names = [m for m in (models or []) if m] or [env["MODEL_NAME"]]
    budget = budget or Budget(max_calls)
    attempts = []
    for name in names:
        try:
            text = ask(user_prompt, system_prompt, model=name, retries=0, budget=budget,
                       post_fn=post_fn, sleep_fn=sleep_fn, env=env)
            attempts.append({"model": name, "result": "ok"})
            return {"text": text, "model": name, "calls": budget.used, "attempts": attempts}
        except BudgetExceeded as e:
            attempts.append({"model": name, "result": str(e)})
            break
        except ModelError as e:
            attempts.append({"model": name, "result": str(e)})
    summary = "; ".join("%s: %s" % (a["model"], a["result"]) for a in attempts)
    raise ModelError("All models failed (" + summary + ")")


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    env = {"MODEL_BASE_URL": "http://x/v1", "MODEL_API_KEY": "k", "MODEL_NAME": "auto"}

    def ok(text="hi"):
        return {"choices": [{"message": {"content": text}}]}

    def err(status, retry_after=None):
        return ModelError("HTTP %d" % status, status=status, retry_after=retry_after,
                          retryable=status in RETRY_STATUS)

    def scripted(items, repeat_last=False):
        state = {"i": 0, "bodies": []}

        def post(url, key, body, timeout):
            state["bodies"].append(json.loads(body.decode("utf-8")))
            i = state["i"]
            state["i"] += 1
            item = items[min(i, len(items) - 1)] if repeat_last else items[i]
            if isinstance(item, Exception):
                raise item
            return item
        return post, state

    def run(fn):
        try:
            return fn(), None
        except ModelError as e:
            return None, e

    sleeps = []
    post, st = scripted([ok("hello")])
    r = ask("q", env=env, post_fn=post, sleep_fn=sleeps.append)
    body = st["bodies"][0]
    check("a good call returns the text and sends model, temperature 0 and a system prompt",
          r == "hello" and body["model"] == "auto" and body["temperature"] == 0
          and body["messages"][0]["role"] == "system")

    sleeps = []
    post, st = scripted([err(502), ok("x")])
    r = ask("q", env=env, post_fn=post, sleep_fn=sleeps.append)
    check("a 502 is retried once after a 2 second wait", r == "x" and st["i"] == 2 and sleeps == [2.0])

    sleeps = []
    post, st = scripted([err(429, retry_after=3.0), ok("x")])
    ask("q", env=env, post_fn=post, sleep_fn=sleeps.append)
    check("a 429 waits for the Retry-After time", sleeps == [3.0])

    sleeps = []
    post, st = scripted([err(429, retry_after=500.0), ok("x")])
    ask("q", env=env, post_fn=post, sleep_fn=sleeps.append)
    check("a huge Retry-After is capped at 20 seconds", sleeps == [20.0])

    sleeps = []
    post, st = scripted([err(404)])
    r, e = run(lambda: ask("q", env=env, post_fn=post, sleep_fn=sleeps.append))
    check("a 404 is not retried", e is not None and e.status == 404 and st["i"] == 1 and sleeps == [])

    sleeps = []
    post, st = scripted([err(502)], repeat_last=True)
    r, e = run(lambda: ask("q", env=env, post_fn=post, sleep_fn=sleeps.append))
    check("retries are bounded: 3 calls, waits of 2 and 4 seconds, then an error",
          e is not None and str(e) == "HTTP 502" and st["i"] == 3 and sleeps == [2.0, 4.0])

    sleeps = []
    post, st = scripted([ModelError("Connection failed: check FreeLLMAPI is running")])
    r, e = run(lambda: ask("q", env=env, post_fn=post, sleep_fn=sleeps.append))
    check("a connection failure is not retried", e is not None and st["i"] == 1 and sleeps == [])

    post, st = scripted([err(502)], repeat_last=True)
    r, e = run(lambda: ask("q", env=env, post_fn=post, sleep_fn=lambda s: None, retries=5, budget=Budget(2)))
    check("a call budget of 2 stops after 2 calls", isinstance(e, BudgetExceeded) and st["i"] == 2)

    post, st = scripted([err(429), ok("B")])
    r = ask_chain("q", models=["a", "b"], env=env, post_fn=post, sleep_fn=lambda s: None)
    check("the chain falls back to the next model",
          r["text"] == "B" and r["model"] == "b" and r["calls"] == 2
          and [b["model"] for b in st["bodies"]] == ["a", "b"])

    post, st = scripted([err(502), err(429)])
    r, e = run(lambda: ask_chain("q", models=["a", "b"], env=env, post_fn=post, sleep_fn=lambda s: None))
    check("if every model fails the chain raises one clear error",
          e is not None and "All models failed" in str(e) and "a: HTTP 502" in str(e))

    post, st = scripted([err(502)], repeat_last=True)
    r, e = run(lambda: ask_chain("q", models=["a", "b", "c", "d", "e"], max_calls=3, env=env,
                                 post_fn=post, sleep_fn=lambda s: None))
    check("the chain stops at its call budget", e is not None and st["i"] == 3)

    post, st = scripted([ok("D")])
    r = ask_chain("q", env=env, post_fn=post, sleep_fn=lambda s: None)
    check("with no model list the chain uses the default model", r["model"] == "auto" and r["text"] == "D")

    check("a comma list is parsed and blanks are dropped",
          env_list("W", ["d"], env={"W": " a, b ,,c"}) == ["a", "b", "c"])
    check("a missing list falls back to the default", env_list("W", ["d"], env={}) == ["d"])

    shown = redact("my key freellmapi-0123456789abcdef is here")
    check("key-like text is redacted", "[redacted]" in shown and "0123456789abcdef" not in shown)

    post, st = scripted([{"choices": []}])
    r, e = run(lambda: ask("q", env=env, post_fn=post, sleep_fn=lambda s: None))
    check("a malformed reply is an error and is not retried", e is not None and st["i"] == 1)

    post, st = scripted([ok("   ")])
    r, e = run(lambda: ask("q", env=env, post_fn=post, sleep_fn=lambda s: None))
    check("an empty reply is an error", e is not None)

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


def run_live():
    reply = ask("Reply with the single word OK.", system_prompt="You are a connection test. Follow the user instruction exactly.")
    print("OK" if "OK" in reply.upper() else redact(reply))


if __name__ == "__main__":
    run_tests()
    if "--live" in sys.argv:
        run_live()