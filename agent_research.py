import json
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from model_client import load_env, redact

BASE = "https://www.googleapis.com/youtube/v3/"
MAX_TOPIC = 100


def _real_fetch(path, params):
    env = load_env()
    key = str(env.get("YOUTUBE_API_KEY", "")).strip() if isinstance(env, dict) else ""
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not set")
    q = dict(params)
    q["key"] = key
    with urllib.request.urlopen(BASE + path + "?" + urllib.parse.urlencode(q), timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _seconds(iso):
    import re
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", str(iso or ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _age_days(published, now):
    try:
        dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max((now - dt).total_seconds() / 86400.0, 1.0)


def title_patterns(titles):
    if not titles:
        return {}
    n = len(titles)
    return {
        "count": n,
        "avg_length": round(sum(len(t) for t in titles) / n),
        "with_number_pct": round(100 * sum(any(c.isdigit() for c in t) for t in titles) / n),
        "question_pct": round(100 * sum("?" in t for t in titles) / n),
    }


def analyze(items, now):
    rows = []
    for it in items:
        views = it.get("views")
        subs = it.get("subs")
        age = _age_days(it.get("published"), now)
        if views is None or age is None:
            continue
        outlier = round(views / subs, 2) if subs and subs > 0 else None
        rows.append({
            "id": it.get("id"), "title": it.get("title", ""), "channel": it.get("channel", ""),
            "views": views, "subs": subs, "uploaded": str(it.get("published", ""))[:10], "age_days": round(age, 1), "seconds": it.get("seconds"), "speed": round(views / age),
            "outlier": outlier,
            "small_win": bool(subs is not None and 0 < subs < 100000 and views >= 10 * subs),
        })
    rows.sort(key=lambda r: (r["small_win"], r["outlier"] if r["outlier"] is not None else 0, r["speed"]), reverse=True)
    return rows


def trend_report(topic, region="", days=30, fetch_fn=None, now=None, min_sec=None, max_sec=None):
    if not isinstance(topic, str) or not topic.strip():
        return {"error": "Topic is empty."}
    topic = topic.strip()
    if len(topic) > MAX_TOPIC:
        return {"error": "Topic is too long."}
    region = (region or "").strip().upper()
    if region and not (len(region) == 2 and region.isalpha()):
        return {"error": "Region must be 2 letters, like US or IN."}
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
        return {"error": "Days must be 1 to 365."}
    for lim in (min_sec, max_sec):
        if lim is not None and (not isinstance(lim, int) or isinstance(lim, bool) or lim < 0):
            return {"error": "Length limits must be whole seconds."}
    fetch_fn = fetch_fn or _real_fetch
    now = now or datetime.now(timezone.utc)
    base = {"topic": topic, "region": region, "days": days}
    try:
        params = {"part": "snippet", "q": topic, "type": "video", "order": "viewCount", "maxResults": 50,
                  "publishedAfter": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if region:
            params["regionCode"] = region
        if min_sec is not None and max_sec is not None and min_sec >= 240 and max_sec <= 1200:
            params["videoDuration"] = "medium"
        found = fetch_fn("search", params)
        ids = [i.get("id", {}).get("videoId") for i in found.get("items", []) if isinstance(i, dict)]
        ids = [i for i in ids if i]
        if not ids:
            return dict(base, videos=[], summary={"videos_analyzed": 0}, patterns={},
                        message="No videos found for this topic in that period.")
        vids = fetch_fn("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)})
        vitems = [v for v in vids.get("items", []) if isinstance(v, dict)]
        cids = sorted({v.get("snippet", {}).get("channelId") for v in vitems if v.get("snippet", {}).get("channelId")})
        subs = {}
        if cids:
            ch = fetch_fn("channels", {"part": "statistics", "id": ",".join(cids[:50])})
            for c in ch.get("items", []):
                st = c.get("statistics", {})
                subs[c.get("id")] = None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount"))
    except Exception as e:
        return {"error": "YouTube call failed: " + redact(type(e).__name__ + ": " + str(e))}
    items = []
    for v in vitems:
        sn = v.get("snippet", {})
        st = v.get("statistics", {})
        items.append({"id": v.get("id"), "title": str(sn.get("title", "")), "channel": str(sn.get("channelTitle", "")),
                      "published": sn.get("publishedAt"), "views": _int(st.get("viewCount")),
                      "subs": subs.get(sn.get("channelId")),
                      "seconds": _seconds(v.get("contentDetails", {}).get("duration"))})
    rows = analyze(items, now)
    if min_sec is not None or max_sec is not None:
        rows = [r for r in rows if r.get("seconds") is not None and (min_sec is None or r["seconds"] >= min_sec) and (max_sec is None or r["seconds"] <= max_sec)]
    views = [r["views"] for r in rows]
    summary = {
        "videos_analyzed": len(rows),
        "median_views": int(statistics.median(views)) if views else 0,
        "over_100k": sum(1 for x in views if x >= 100000),
        "small_channel_wins": sum(1 for r in rows if r["small_win"]),
    }
    return dict(base, videos=rows[:10], summary=summary, patterns=title_patterns([r["title"] for r in rows]))


def run_tests():
    results = []

    def check(name, ok):
        results.append(bool(ok))
        if not ok:
            print("FAIL: " + name)

    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    calls = []

    def fake(path, params):
        calls.append((path, dict(params)))
        if path == "search":
            return {"items": [{"id": {"videoId": "a"}}, {"id": {"videoId": "b"}}, {"id": {"videoId": "c"}}]}
        if path == "videos":
            return {"items": [
                {"id": "a", "snippet": {"title": "5 AI tools I use", "channelId": "c1", "channelTitle": "Small", "publishedAt": "2026-10-30T00:00:00Z"}, "statistics": {"viewCount": "50000"}},
                {"id": "b", "snippet": {"title": "AI tools explained", "channelId": "c2", "channelTitle": "Big", "publishedAt": "2026-10-01T00:00:00Z"}, "statistics": {"viewCount": "900000"}},
                {"id": "c", "snippet": {"title": "AI news today", "channelId": "c3", "channelTitle": "Hidden", "publishedAt": "2026-11-02T12:00:00Z"}, "statistics": {"viewCount": "1000"}},
            ]}
        return {"items": [
            {"id": "c1", "statistics": {"subscriberCount": "2000"}},
            {"id": "c2", "statistics": {"subscriberCount": "5000000"}},
            {"id": "c3", "statistics": {"hiddenSubscriberCount": True}},
        ]}

    r = trend_report("ai tools", region="us", fetch_fn=fake, now=now)
    v = r.get("videos", [])
    check("small channel outlier ranks first", len(v) == 3 and v[0]["id"] == "a")
    check("then big channel, then hidden", len(v) == 3 and v[1]["id"] == "b" and v[2]["id"] == "c")
    check("outlier value", len(v) == 3 and v[0]["outlier"] == 25.0)
    check("hidden subs gives no outlier or win", len(v) == 3 and v[2]["outlier"] is None and v[2]["small_win"] is False)
    check("age is at least one day", len(v) == 3 and v[2]["speed"] == 1000)
    check("upload date kept", len(v) == 3 and v[0]["uploaded"] == "2026-10-30" and v[1]["uploaded"] == "2026-10-01")
    check("summary", r.get("summary") == {"videos_analyzed": 3, "median_views": 50000, "over_100k": 1, "small_channel_wins": 1})
    check("title patterns", r.get("patterns", {}).get("avg_length") == 16 and r["patterns"]["with_number_pct"] == 33)
    check("region and date sent", calls[0][1].get("regionCode") == "US" and calls[0][1].get("publishedAfter") == "2026-10-04T00:00:00Z")
    calls.clear()
    trend_report("ai tools", fetch_fn=fake, now=now)
    check("no region means no regionCode", "regionCode" not in calls[0][1])
    check("empty topic", "error" in trend_report("  ", fetch_fn=fake))
    check("long topic", "error" in trend_report("x" * 101, fetch_fn=fake))
    check("bad region", "error" in trend_report("t", region="USA", fetch_fn=fake))
    check("bad days", all("error" in trend_report("t", days=d, fetch_fn=fake) for d in (0, 366, True, "7")))
    empty = trend_report("t", fetch_fn=lambda p, q: {"items": []}, now=now)
    check("no results is not an error", "error" not in empty and empty["videos"] == [])

    def boom(p, q):
        raise RuntimeError("boom")

    check("fetch failure gives an error", "error" in trend_report("t", fetch_fn=boom, now=now))
    print(str(sum(results)) + " / " + str(len(results)) + " passed")
    return all(results)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "trend":
        res = trend_report(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        if "error" in res:
            print(res["error"])
        else:
            print(res.get("message", "") or str(res["summary"]))
            for i, x in enumerate(res["videos"][:5], 1):
                print(str(i) + ". " + x["title"][:60] + " | " + str(x["views"]) + " views | uploaded " + str(x["uploaded"]) + " (" + str(round(x["age_days"])) + "d ago) | subs " + str(x["subs"])
                      + " | " + str(x["speed"]) + "/day | outlier " + str(x["outlier"]) + (" | SMALL WIN" if x["small_win"] else ""))
    else:
        sys.exit(0 if run_tests() else 1)