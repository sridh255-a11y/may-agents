import re
import sys

LIMITS = {"title": 100, "description": 5000, "tags": 500, "hashtags": 60}


def count_hashtags(text):
    return len(re.findall(r"(?<!\w)#\w+", text or ""))


def tags_length(tags):
    clean = [t.strip() for t in (tags or []) if t and t.strip()]
    return sum(len(t) for t in clean) + max(len(clean) - 1, 0)


def check_seo(title, description, tags=None):
    title = title or ""
    description = description or ""
    errors = []
    t_len = len(title.strip())
    d_len = len(description)
    g_len = tags_length(tags)
    h_count = count_hashtags(title) + count_hashtags(description)
    if t_len == 0:
        errors.append("Title is empty.")
    if t_len > LIMITS["title"]:
        errors.append("Title is " + str(t_len) + " characters. Limit is 100.")
    if d_len > LIMITS["description"]:
        errors.append("Description is " + str(d_len) + " characters. Limit is 5000.")
    if g_len > LIMITS["tags"]:
        errors.append("Tags are " + str(g_len) + " characters in total. Limit is 500.")
    if h_count > LIMITS["hashtags"]:
        errors.append(str(h_count) + " hashtags. More than 60 are ignored.")
    return {"ok": not errors, "errors": errors, "title_len": t_len,
            "description_len": d_len, "tags_len": g_len, "hashtags": h_count}


def run_tests():
    many = " ".join("#t" + str(i) for i in range(60))
    cases = [
        ("normal input passes", check_seo("Good title", "Short text", ["a", "b"])["ok"]),
        ("title 100 ok", check_seo("a" * 100, "d")["ok"]),
        ("title 101 fails", not check_seo("a" * 101, "d")["ok"]),
        ("empty title fails", not check_seo("   ", "d")["ok"]),
        ("description 5000 ok", check_seo("t", "a" * 5000)["ok"]),
        ("description 5001 fails", not check_seo("t", "a" * 5001)["ok"]),
        ("tags 500 ok", check_seo("t", "d", ["a" * 500])["ok"]),
        ("tags 501 fails", not check_seo("t", "d", ["a" * 501])["ok"]),
        ("commas are counted", not check_seo("t", "d", ["a" * 250, "a" * 250])["ok"]),
        ("60 hashtags ok", check_seo("t", many)["ok"]),
        ("61 hashtags fails", not check_seo("t", many + " #extra")["ok"]),
        ("tags None ok", check_seo("t", "d", None)["ok"]),
        ("hashtags counted in title and description", check_seo("x #a", "#b #c")["hashtags"] == 3),
        ("C# is not a hashtag", check_seo("C# language", "d")["hashtags"] == 0),
    ]
    results = []
    for name, ok in cases:
        results.append(bool(ok))
        if not ok:
            print("FAIL: " + name)
    print(str(sum(results)) + " / " + str(len(results)) + " passed")
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)