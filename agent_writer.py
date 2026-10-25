import json
import sys
from model_client import ask_chain, env_list, ModelError, redact

DEFAULT_WRITER_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
MAX_MODEL_CALLS = 4

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


def writer_models(env=None):
    return env_list("WRITER_MODELS", DEFAULT_WRITER_MODELS, env=env)


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


def write(topic, notes, chain_fn=None, models=None):
    chain_fn = chain_fn or ask_chain
    models = models or writer_models()
    prompt = "TOPIC: " + topic + "\n\nSOURCE NOTES:\n" + notes
    try:
        res = chain_fn(prompt, system_prompt=WRITER_PROMPT, models=models, max_calls=MAX_MODEL_CALLS)
    except ModelError as e:
        return {"error": "Writer model call failed: " + redact(str(e))}
    except Exception as e:
        return {"error": "Writer model call failed: " + redact(type(e).__name__ + ": " + str(e))}
    reply = str(res.get("text", ""))
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
        "model": res.get("model", ""),
        "attempts": res.get("attempts", []),
        "calls": res.get("calls", 0),
    }


NOTES = "Tool X is a free note-taking app. Tool X works on Windows and phone. It was released in 2024."


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    def fake_chain(text, model="m1", attempts=None, record=None):
        def chain(prompt, system_prompt=None, models=None, max_calls=None, **kw):
            if record is not None:
                record["models"] = models
                record["max_calls"] = max_calls
                record["prompt"] = prompt
            return {"text": text, "model": model, "calls": 1,
                    "attempts": attempts or [{"model": model, "result": "ok"}]}
        return chain

    def failing_chain(exc):
        def chain(prompt, system_prompt=None, models=None, max_calls=None, **kw):
            raise exc
        return chain

    good = json.dumps({
        "title": "T",
        "script": "Tool X is a free note-taking app for Windows and phone, released in 2024.",
        "claims": [
            {"claim": "free app", "quote": "Tool X is a free note-taking app."},
            {"claim": "released in 2024", "quote": "It was released in 2024."},
        ],
    })
    r = write("Tool X", NOTES, chain_fn=fake_chain(good, model="m9"))
    check("valid quotes are verified and the model is recorded",
          r.get("verified") == 2 and r.get("not_found") == 0 and r.get("model") == "m9")
    check("a clean script reports no repeats", r.get("repeats") == 0)

    bad = json.dumps({"title": "T", "script": "x", "claims": [{"claim": "price", "quote": "Tool X costs 5 euros a month."}]})
    r = write("Tool X", NOTES, chain_fn=fake_chain(bad))
    check("an invented quote is marked NOT FOUND",
          r.get("not_found") == 1 and r["claims"][0]["source"] == "NOT FOUND")

    empty = json.dumps({"title": "T", "script": "x", "claims": [{"claim": "c", "quote": ""}]})
    r = write("Tool X", NOTES, chain_fn=fake_chain(empty))
    check("an empty quote is NOT FOUND", r.get("not_found") == 1)

    loose = json.dumps({"title": "T", "script": "x", "claims": [{"claim": "c", "quote": "tool x   WORKS on windows and phone."}]})
    r = write("Tool X", NOTES, chain_fn=fake_chain(loose))
    check("quotes match ignoring case and spacing", r.get("verified") == 1)

    padded = json.dumps({
        "title": "T",
        "script": "Tool X is a free note-taking app. It works on Windows. Remember that Tool X is a free note-taking app. Thanks for watching.",
        "claims": [],
    })
    r = write("Tool X", NOTES, chain_fn=fake_chain(padded))
    check("a repeated phrase is reported", r.get("repeats", 0) > 0)

    r = write("Tool X", NOTES, chain_fn=fake_chain("not json at all"))
    check("invalid JSON gives an error", "error" in r)

    r = write("Tool X", NOTES, chain_fn=failing_chain(ModelError("All models failed (a: HTTP 502)")))
    check("a model failure gives an error, not a crash", "error" in r and "All models failed" in r["error"])

    r = write("Tool X", NOTES, chain_fn=failing_chain(ValueError("odd")))
    check("an unexpected exception gives an error, not a crash", "error" in r)

    tries = [{"model": "a", "result": "HTTP 429"}, {"model": "b", "result": "ok"}]
    r = write("Tool X", NOTES, chain_fn=fake_chain(good, model="b", attempts=tries))
    check("the fallback attempts are passed through", r.get("model") == "b" and len(r.get("attempts", [])) == 2)

    seen = {}
    write("Tool X", NOTES, chain_fn=fake_chain(good, record=seen), models=["x", "y"])
    check("the model list, call cap and notes reach the chain",
          seen.get("models") == ["x", "y"] and seen.get("max_calls") == 4 and "SOURCE NOTES" in seen.get("prompt", "") and "free note-taking" in seen.get("prompt", ""))

    check("the default Writer models are Gemini only, so the Checker's provider stays different",
          DEFAULT_WRITER_MODELS[0] == "gemini-3.8-flash" and all(m.startswith("gemini") for m in DEFAULT_WRITER_MODELS))

    check("a Writer model list can be set with a comma list",
          writer_models(env={"WRITER_MODELS": "a, b"}) == ["a", "b"] and writer_models(env={}) == DEFAULT_WRITER_MODELS)

    r = write("Tool X", NOTES, chain_fn=failing_chain(ModelError("HTTP 502 freellmapi-0123456789abcdef")))
    check("keys are redacted from error text",
          "[redacted]" in r.get("error", "") and "0123456789abcdef" not in r.get("error", ""))

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


def run_live():
    print("Live test, model list: " + ", ".join(writer_models()))
    r1 = write("Tool X", NOTES)
    ok1 = "error" not in r1 and r1["verified"] >= 1 and len(r1["script"]) > 0
    print(("PASS" if ok1 else "FAIL") + " | Tool X has verified claims | model " + str(r1.get("model")) + " | "
          + str(r1.get("verified")) + " verified, " + str(r1.get("not_found")) + " not found, repeats " + str(r1.get("repeats")) + " | " + str(r1.get("error", "")))
    print("attempts: " + str(r1.get("attempts")))
    r2 = write("Tool Y pricing in euros", NOTES)
    ok2 = "error" not in r2 and r2["verified"] == 0
    print(("PASS" if ok2 else "FAIL") + " | Tool Y has no verified claims | " + str(r2.get("verified")) + " verified | " + str(r2.get("error", "")))
    print(str(int(ok1) + int(ok2)) + " / 2 live passed")


if __name__ == "__main__":
    run_tests()
    if "--live" in sys.argv:
        run_live()