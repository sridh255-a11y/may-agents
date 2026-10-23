import json
from model_client import ask

ROUTES = ["writer", "analytics", "seo", "video"]

MANAGER_PROMPT = (
    "You are the Manager of a control system. Read the request and choose exactly one route. "
    "writer = scripts, ideas, text content. "
    "analytics = questions about channel numbers or CSV data. "
    "seo = titles, tags, descriptions, keywords. "
    "video = editing, captions, rendering, thumbnails. "
    "If the request fits none of them, use unknown. "
    'Reply with JSON only, in this shape: {"route": "writer", "reason": "one short sentence"}'
)


def route(request):
    reply = ask(request, system_prompt=MANAGER_PROMPT)
    start = reply.find("{")
    end = reply.rfind("}")
    try:
        data = json.loads(reply[start:end + 1])
    except ValueError:
        return {"route": "unknown", "reason": "Manager reply was not valid JSON"}
    chosen = str(data.get("route", "")).strip().lower()
    if chosen not in ROUTES:
        return {"route": "unknown", "reason": "Route not on the allowed list"}
    return {"route": chosen, "reason": str(data.get("reason", ""))}


TESTS = [
    ("Write a script about 5 free AI tools", "writer"),
    ("Show my views trend from the YouTube Studio CSV", "analytics"),
    ("Give me a title and tags for my video", "seo"),
    ("Add captions and cut the silence in my screen recording", "video"),
]

if __name__ == "__main__":
    passed = 0
    for text, expected in TESTS:
        got = route(text)["route"]
        ok = got == expected
        passed += ok
        print(("PASS" if ok else "FAIL"), "|", expected, "->", got)
    print(passed, "/", len(TESTS), "passed")
