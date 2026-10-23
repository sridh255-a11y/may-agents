import json, os, urllib.request

def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def ask(prompt, system="You are accurate. If unsure, say NOT FOUND. Never guess."):
    load_env()
    url = os.environ["MODEL_BASE_URL"].rstrip("/") + "/chat/completions"
    body = json.dumps({"model": os.environ["MODEL_NAME"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": 0}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("MODEL_API_KEY", "")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]

if __name__ == "__main__":
    print(ask("Reply with the single word OK"))
