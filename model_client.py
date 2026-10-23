import json
import os
import urllib.request
import urllib.error

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

SYSTEM_RULE = "Answer only with information you can support. If a claim has no source, write NOT FOUND instead of guessing."


def load_env():
    values = {}
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def ask(user_prompt, system_prompt=SYSTEM_RULE, model=None):
    env = load_env()
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
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP " + str(e.code))
    except urllib.error.URLError:
        raise RuntimeError("Connection failed: check FreeLLMAPI is running")
    return data["choices"][0]["message"]["content"]


if __name__ == "__main__":
    reply = ask("Reply with the single word OK.")
    print("OK" if "OK" in reply.upper() else reply)
