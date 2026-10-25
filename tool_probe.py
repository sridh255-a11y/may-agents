import json
import sys
import time
import urllib.error
import urllib.request
from model_client import load_env, redact

DEFAULT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-it",
    "llama-3.3-70b-fp8-fast",
    "qwen3-30b-a3b-fp8",
    "mistral-small-3.1-24b",
    "llama-4-scout",
    "glm-4.7-flash",
    "nemotron-3-super-120b",
    "gpt-oss-20b",
    "kilo-auto",
    "gpt-oss-120b",
]


def probe(model, env):
    url = env["MODEL_BASE_URL"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "max_tokens": 5,
        "temperature": 0,
        "messages": [{"role": "user", "content": "Say OK"}],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + env["MODEL_API_KEY"],
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get("X-Routed-Via"), ""
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("X-Routed-Via"), ""
    except Exception as e:
        return None, None, redact(type(e).__name__)


def main():
    env = load_env()
    models = sys.argv[1:] or DEFAULT_MODELS
    print("Probing %d models, one tiny call each." % len(models))
    for m in models:
        status, via, err = probe(m, env)
        print("%-26s status=%s  via=%s  %s" % (m, status, via, err))
        time.sleep(1)
    print("done")


if __name__ == "__main__":
    main()