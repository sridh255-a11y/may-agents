import json
from model_client import ask

WRITER_PROMPT = (
    "You are the Writer. Write a short video script (about 120 words) using ONLY the SOURCE NOTES given. "
    "Do not add facts from memory. If the notes do not cover the topic, write NOT FOUND as the script. "
    "List every factual claim with an exact quote copied from the notes. "
    'Reply with JSON only: {"title": "...", "script": "...", "claims": [{"claim": "...", "quote": "..."}]}'
)


def clean(text):
    return " ".join(str(text).lower().split())


def write(topic, notes):
    prompt = "TOPIC: " + topic + "\n\nSOURCE NOTES:\n" + notes
    reply = ask(prompt, system_prompt=WRITER_PROMPT)
    start = reply.find("{")
    end = reply.rfind("}")
    try:
        data = json.loads(reply[start:end + 1])
    except ValueError:
        return {"error": "Writer reply was not valid JSON"}
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        claims = []
    notes_clean = clean(notes)
    verified = 0
    not_found = 0
    checked = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        quote = clean(c.get("quote", ""))
        found = len(quote) > 0 and quote in notes_clean
        verified += found
        not_found += not found
        checked.append({
            "claim": c.get("claim", ""),
            "quote": c.get("quote", ""),
            "source": "FOUND" if found else "NOT FOUND",
        })
    return {
        "title": data.get("title", ""),
        "script": data.get("script", ""),
        "claims": checked,
        "verified": verified,
        "not_found": not_found,
    }


NOTES = "Tool X is a free note-taking app. Tool X works on Windows and phone. It was released in 2024."

if __name__ == "__main__":
    r1 = write("Tool X", NOTES)
    ok1 = "error" not in r1 and r1["verified"] >= 1 and len(r1["script"]) > 0
    print(("PASS" if ok1 else "FAIL"), "| Tool X has verified claims |", r1.get("verified"), "verified,", r1.get("not_found"), "not found")
    r2 = write("Tool Y pricing in euros", NOTES)
    ok2 = "error" not in r2 and r2["verified"] == 0
    print(("PASS" if ok2 else "FAIL"), "| Tool Y has no verified claims |", r2.get("verified"), "verified")
    print(int(ok1) + int(ok2), "/ 2 passed")