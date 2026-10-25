import json
import sys
from model_client import ask, load_env

DEFAULT_WRITER_MODEL = "gemini-3.8-flash"

WRITER_PROMPT = (
    "You are the Writer. Write a short video script using ONLY the SOURCE NOTES given. "
    "Do not add facts from memory. If the notes do not cover the topic, write NOT FOUND as the script. "
    "State each fact once. Never repeat a fact or a sentence. "
    "Keep the script short: about 25 words per fact in the notes, and never more than 120 words in total. "
    "List every factual claim with an exact quote copied from the notes. "
    'Reply with JSON only: {"title": "...", "script": "...", "claims": [{"claim": "...", "quote": "..."}]}'
)


def clean(text):
    return " ".join(str(text).lower().split())


def writer_model():
    try:
        value = str(load_env().get("WRITER_MODEL", "")).strip()
    except Exception:
        value = ""
    return value or DEFAULT_WRITER_MODEL


def repeated_phrases(text, n=5):
    words = clean(text).split()
    seen = set()
    repeats = 0
    for i in range(len(words) - n + 1):
        gram = " ".join(words[i:i + n])
        if gram in seen:
            repeats += 1
        else:
            seen.add(gram)
    return repeats


def write(topic, notes, ask_fn=None, model=None):
    ask_fn = ask_fn or ask
    model = model or writer_model()
    prompt = "TOPIC: " + topic + "\n\nSOURCE NOTES:\n" + notes
    try:
        reply = ask_fn(prompt, system_prompt=WRITER_PROMPT, model=model)
    except Exception as e:
        return {"error": "Writer model call failed: " + str(e)}
    start = reply.find("{")
    end = reply.rfind("}")
    try:
        data = json.loads(reply[start:end + 1])
    except ValueError:
        return {"error": "Writer reply was not valid JSON"}
    if not isinstance(data, dict):
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
    script = str(data.get("script", ""))
    return {
        "title": data.get("title", ""),
        "script": script,
        "claims": checked,
        "verified": verified,
        "not_found": not_found,
        "repeats": repeated_phrases(script),
        "model": model,
    }


NOTES = "Tool X is a free note-taking app. Tool X works on Windows and phone. It was released in 2024."


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    def stub(text):
        def fake(prompt, system_prompt=None, model=None):
            return text
        return fake

    def boom(prompt, system_prompt=None, model=None):
        raise RuntimeError("down")

    good = json.dumps({
        "title": "T",
        "script": "Tool X is a free note-taking app for Windows and phone, released in 2024.",
        "claims": [
            {"claim": "free app", "quote": "Tool X is a free note-taking app."},
            {"claim": "released in 2024", "quote": "It was released in 2024."},
        ],
    })
    r = write("Tool X", NOTES, ask_fn=stub(good), model="test")
    check("valid quotes are verified and the model is recorded",
          r.get("verified") == 2 and r.get("not_found") == 0 and r.get("model") == "test")
    check("a clean script reports no repeats", r.get("repeats") == 0)

    bad = json.dumps({"title": "T", "script": "x", "claims": [{"claim": "price", "quote": "Tool X costs 5 euros a month."}]})
    r = write("Tool X", NOTES, ask_fn=stub(bad), model="test")
    check("an invented quote is marked NOT FOUND",
          r.get("not_found") == 1 and r["claims"][0]["source"] == "NOT FOUND")

    empty = json.dumps({"title": "T", "script": "x", "claims": [{"claim": "c", "quote": ""}]})
    r = write("Tool X", NOTES, ask_fn=stub(empty), model="test")
    check("an empty quote is NOT FOUND", r.get("not_found") == 1)

    loose = json.dumps({"title": "T", "script": "x", "claims": [{"claim": "c", "quote": "tool x   WORKS on windows and phone."}]})
    r = write("Tool X", NOTES, ask_fn=stub(loose), model="test")
    check("quotes match ignoring case and spacing", r.get("verified") == 1)

    padded = json.dumps({
        "title": "T",
        "script": "Tool X is a free note-taking app. It works on Windows. Remember that Tool X is a free note-taking app. Thanks for watching.",
        "claims": [],
    })
    r = write("Tool X", NOTES, ask_fn=stub(padded), model="test")
    check("a repeated phrase is reported", r.get("repeats", 0) > 0)

    r = write("Tool X", NOTES, ask_fn=stub("not json at all"), model="test")
    check("invalid JSON gives an error", "error" in r)

    r = write("Tool X", NOTES, ask_fn=boom, model="test")
    check("a model failure gives an error, not a crash", "error" in r)

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


def run_live():
    print("Live test with model: " + writer_model())
    r1 = write("Tool X", NOTES)
    ok1 = "error" not in r1 and r1["verified"] >= 1 and len(r1["script"]) > 0
    print(("PASS" if ok1 else "FAIL") + " | Tool X has verified claims | " + str(r1.get("verified")) + " verified, " + str(r1.get("not_found")) + " not found, repeats " + str(r1.get("repeats")) + " | " + str(r1.get("error", "")))
    r2 = write("Tool Y pricing in euros", NOTES)
    ok2 = "error" not in r2 and r2["verified"] == 0
    print(("PASS" if ok2 else "FAIL") + " | Tool Y has no verified claims | " + str(r2.get("verified")) + " verified | " + str(r2.get("error", "")))
    print(str(int(ok1) + int(ok2)) + " / 2 live passed")


if __name__ == "__main__":
    run_tests()
    if "--live" in sys.argv:
        run_live()