import re
import sys

MAX_AUTO = 5
_calls = [0]
INTENT = re.compile(r"\b(next video|trending topic|trending video|what (should|to) (i |we )?(make|post|create)|plan (the |my |a )?video|auto video)\b", re.I)


def is_auto(request):
    return isinstance(request, str) and bool(INTENT.search(request))


def _real_brief(project):
    import agent_trendbrief
    return agent_trendbrief.brief_with_outline(project)


def notes_from_brief(b):
    pick = b.get("pick") if isinstance(b, dict) else None
    if not isinstance(pick, dict) or not pick.get("videos"):
        return ""
    lines = ["TOPIC: " + str(pick.get("topic", ""))]
    for v in pick["videos"]:
        s = "VIDEO: %s | %s views | uploaded %s | channel %s | channel subscribers %s" % (
            v.get("title", ""), v.get("views"), v.get("uploaded", ""), v.get("channel", ""), v.get("subs"))
        g = v.get("growth")
        if isinstance(g, dict):
            s += " | gained %s views over %s days" % (g.get("delta"), g.get("days"))
        lines.append(s)
    sm = pick.get("summary") or {}
    if sm:
        lines.append("SEARCH SUMMARY: %s videos analyzed, median %s views, %s over 100000 views" % (
            sm.get("videos_analyzed"), sm.get("median_views"), sm.get("over_100k")))
    return "\n".join(lines)


def prepare(project, brief_fn=None):
    if _calls[0] >= MAX_AUTO:
        return {"error": "Auto research limit reached for this session. Restart the dashboard to reset."}
    _calls[0] += 1
    brief_fn = brief_fn or _real_brief
    try:
        b = brief_fn(project)
    except Exception as e:
        return {"error": "Research failed: " + type(e).__name__}
    if not isinstance(b, dict):
        return {"error": "Research returned a bad result."}
    if "error" in b:
        return {"error": str(b["error"])}
    if not b.get("pick"):
        return {"error": str(b.get("message") or "No strong topic found.")}
    notes = notes_from_brief(b)
    topic = str(b["pick"].get("topic", ""))
    return {"topic": topic, "notes": notes, "brief": b,
            "request": "Write a short script about what is working on YouTube for the topic '" + topic + "', using only the notes."}


def run_tests():
    results = []

    def check(name, ok):
        results.append(bool(ok))
        if not ok:
            print("FAIL: " + name)

    pick = {"topic": "alpha", "summary": {"videos_analyzed": 20, "median_views": 5000, "over_100k": 2},
            "videos": [{"title": "A build", "views": 90000, "uploaded": "2026-10-30", "channel": "Chan", "subs": 4000,
                        "growth": {"delta": 500, "days": 2.0}},
                       {"title": "B tools", "views": 20000, "uploaded": "2026-10-29", "channel": "Other", "subs": 9000}]}
    good = {"pick": pick, "outline": {"angle": "x"}}
    check("intent: next video", is_auto("make the next video for this channel"))
    check("intent: what should I make", is_auto("What should I make today?"))
    check("intent: trending topic", is_auto("find a trending topic"))
    check("no intent: normal script", not is_auto("Write a script about 5 free AI tools"))
    check("no intent: not a string", not is_auto(None) and not is_auto(5))
    n = notes_from_brief(good)
    check("notes have video lines", "VIDEO: A build | 90000 views | uploaded 2026-10-30 | channel Chan" in n and "TOPIC: alpha" in n)
    check("notes have growth only when known", "gained 500 views over 2.0 days" in n and n.count("gained") == 1)
    check("notes have the summary", "median 5000 views" in n)
    check("notes empty without a pick", notes_from_brief({"pick": None}) == "" and notes_from_brief("x") == "")
    _calls[0] = 0
    p = prepare("P", brief_fn=lambda proj: good)
    check("prepare ok", p.get("topic") == "alpha" and "alpha" in p["request"] and "VIDEO:" in p["notes"])
    check("prepare passes the error", prepare("P", brief_fn=lambda proj: {"error": "No seeds"}) == {"error": "No seeds"})
    check("prepare uses the message", prepare("P", brief_fn=lambda proj: {"pick": None, "message": "None strong"}) == {"error": "None strong"})
    check("prepare bad result", "error" in prepare("P", brief_fn=lambda proj: "x"))

    def boom(proj):
        raise RuntimeError("x")

    check("prepare exception", "error" in prepare("P", brief_fn=boom))
    _calls[0] = 0
    codes = ["error" in prepare("P", brief_fn=lambda proj: good) for _ in range(6)]
    check("limit of 5 then error", codes == [False] * 5 + [True])
    _calls[0] = 0
    print(str(sum(results)) + " / " + str(len(results)) + " passed")
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)