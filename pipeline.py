ROUTES_NOT_BUILT = ("analytics", "seo", "video")


def _real_manager(request):
    import agent_manager
    return agent_manager.route(request)


def _real_writer(topic, notes, style=None):
    import agent_writer
    return agent_writer.write(topic, notes, style=style)


def _real_style(project):
    try:
        import channel_settings
        return channel_settings.style_text(project)
    except Exception:
        return ""


def _real_checker(writer_result, notes):
    import agent_checker
    return agent_checker.check_writer_output(writer_result, notes)


def _real_gate(project, request_type, payload):
    import approval_gate
    return approval_gate.add_request(project, request_type, payload)


def verdict_text(verdict):
    if isinstance(verdict, dict):
        text = verdict.get("verdict", "")
    else:
        text = verdict
    text = str(text).strip().upper()
    if text.startswith("APPROVE"):
        return "APPROVE"
    if text.startswith("REJECT"):
        return "REJECT"
    return "UNCLEAR"


def family_of(item):
    if isinstance(item, dict):
        return str(item.get("family", "") or "").strip().lower()
    return ""


def run_request(project, request, notes, manager_fn=None, writer_fn=None, checker_fn=None, gate_fn=None, style_fn=None, auto_fn=None):
    use_auto = auto_fn is not None or manager_fn is None
    manager_fn = manager_fn or _real_manager
    use_style = writer_fn is None or style_fn is not None
    writer_fn = writer_fn or _real_writer
    style_fn = style_fn or _real_style
    checker_fn = checker_fn or _real_checker
    gate_fn = gate_fn or _real_gate
    out = {
        "project": project,
        "request": request,
        "route": None,
        "reason": "",
        "writer_result": None,
        "checker_verdict": None,
        "writer_family": "",
        "checker_family": "",
        "independent": None,
        "gate_id": None,
        "error": None,
        "message": "",
    }
    try:
        auto_info = None
        if use_auto:
            import agent_auto
            if agent_auto.is_auto(request):
                prep = (auto_fn or agent_auto.prepare)(project)
                if not isinstance(prep, dict) or "error" in prep:
                    out["route"] = "auto"
                    out["error"] = str(prep.get("error")) if isinstance(prep, dict) else "Research returned a bad result."
                    return out
                own = str(notes or "").strip()
                if own:
                    request = "Write a script about the topic '" + str(prep["topic"]) + "' using only the notes. The YouTube data lines show what is working, so use them only to choose the angle."
                    notes = own + "\n\nYOUTUBE DATA:\n" + prep["notes"]
                else:
                    request = prep["request"]
                    notes = prep["notes"]
                auto_info = prep
        routed = manager_fn(request) if auto_info is None else {"route": "writer", "reason": "Trending topic: " + str(auto_info["topic"])}
        if isinstance(routed, dict):
            out["route"] = routed.get("route")
            out["reason"] = str(routed.get("reason", ""))
        route_name = out["route"]
        if route_name != "writer":
            if route_name in ROUTES_NOT_BUILT:
                out["message"] = "The " + str(route_name) + " agent is not built yet."
            else:
                out["message"] = "The Manager could not route this request."
            return out
        style = style_fn(project) if use_style else ""
        written = writer_fn(request, notes, style=style) if style else writer_fn(request, notes)
        out["writer_result"] = written
        if not isinstance(written, dict):
            out["error"] = "Writer failed: bad result"
            return out
        if "error" in written:
            out["error"] = "Writer failed: " + str(written.get("error"))
            return out
        verdict = checker_fn(written, notes)
        out["checker_verdict"] = verdict
        label = verdict_text(verdict)
        if label == "UNCLEAR":
            out["error"] = "Checker gave no clear verdict. Nothing was added to the approval gate."
            return out
        writer_family = family_of(written)
        checker_family = family_of(verdict)
        independent = bool(writer_family) and bool(checker_family) and writer_family != checker_family
        out["writer_family"] = writer_family
        out["checker_family"] = checker_family
        out["independent"] = independent
        if label == "APPROVE" and not independent:
            out["error"] = (
                "Approval blocked: the Writer (" + (writer_family or "unknown") + ") and the Checker ("
                + (checker_family or "unknown") + ") are not confirmed to be different providers. "
                "Nothing was added to the approval gate."
            )
            return out
        payload = {
            "request": request,
            "route": route_name,
            "reason": out["reason"],
            "writer_result": written,
            "checker_verdict": verdict,
            "writer_family": writer_family,
            "checker_family": checker_family,
            "independent": independent,
        }
        if auto_info is not None:
            payload["auto"] = {"topic": auto_info["topic"], "brief": auto_info["brief"]}
        out["gate_id"] = gate_fn(project, "Writer", payload)
        out["message"] = "Checker verdict: " + label + ". Added to the approval gate as pending."
    except Exception as e:
        out["error"] = "Pipeline error: " + type(e).__name__ + ": " + str(e)
    return out


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    def stubs(route="writer", writer_result=None, verdict=None, manager_boom=False,
              checker_boom=False, gate_boom=False, manager_result=None, wf="google", cf="groq"):
        calls = {"writer": 0, "checker": 0, "gate": 0}
        seen = {}

        def m(request):
            if manager_boom:
                raise RuntimeError("manager down")
            if manager_result is not None:
                return manager_result
            return {"route": route, "reason": "stub"}

        def w(topic, notes):
            calls["writer"] += 1
            if writer_result is not None:
                return writer_result
            return {"title": "T", "script": "S", "claims": [], "verified": 1, "not_found": 0, "family": wf}

        def c(written, notes):
            calls["checker"] += 1
            if checker_boom:
                raise RuntimeError("checker down")
            if verdict is not None:
                return verdict
            return {"verdict": "APPROVE", "issues": [], "family": cf}

        def g(project, request_type, payload):
            calls["gate"] += 1
            if gate_boom:
                raise RuntimeError("gate down")
            seen["payload"] = payload
            return 42

        return m, w, c, g, calls, seen

    none_called = {"writer": 0, "checker": 0, "gate": 0}

    m, w, c, g, calls, seen = stubs()
    r = run_request("P", "req", "notes", m, w, c, g)
    check("writer route with APPROVE and different providers reaches the gate",
          calls == {"writer": 1, "checker": 1, "gate": 1} and r["gate_id"] == 42 and r["error"] is None
          and "APPROVE" in r["message"] and r["independent"] is True)

    m, w, c, g, calls, seen = stubs(verdict={"verdict": "REJECT", "issues": ["bad claim"], "family": "groq"})
    r = run_request("P", "req", "notes", m, w, c, g)
    check("REJECT is stored, returned and visible",
          calls["gate"] == 1 and verdict_text(r["checker_verdict"]) == "REJECT" and "REJECT" in r["message"]
          and seen["payload"]["checker_verdict"]["verdict"] == "REJECT")

    m, w, c, g, calls, seen = stubs(route="seo")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("unbuilt agent says not built yet and calls nothing",
          calls == none_called and "not built yet" in r["message"] and r["route"] == "seo")

    m, w, c, g, calls, seen = stubs(route="banana")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("unknown route says could not route and calls nothing",
          calls == none_called and "could not route" in r["message"])

    m, w, c, g, calls, seen = stubs(manager_result="writer")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("non-dict Manager result is not trusted", calls == none_called and "could not route" in r["message"])

    m, w, c, g, calls, seen = stubs(writer_result={"error": "bad"})
    r = run_request("P", "req", "notes", m, w, c, g)
    check("Writer error stops before the Checker and keeps the route",
          calls == {"writer": 1, "checker": 0, "gate": 0} and str(r["error"]).startswith("Writer failed") and r["route"] == "writer")

    m, w, c, g, calls, seen = stubs(verdict={"verdict": "MAYBE", "family": "groq"})
    r = run_request("P", "req", "notes", m, w, c, g)
    check("unclear Checker verdict adds nothing to the gate", calls["gate"] == 0 and "no clear verdict" in str(r["error"]))

    m, w, c, g, calls, seen = stubs(checker_boom=True)
    r = run_request("P", "req", "notes", m, w, c, g)
    check("Checker exception adds nothing and keeps the draft visible",
          calls["gate"] == 0 and "checker down" in str(r["error"]) and r["writer_result"] is not None)

    m, w, c, g, calls, seen = stubs(manager_boom=True)
    r = run_request("P", "req", "notes", m, w, c, g)
    check("Manager exception becomes an error, not a crash", calls == none_called and "manager down" in str(r["error"]))

    m, w, c, g, calls, seen = stubs(gate_boom=True)
    r = run_request("P", "req", "notes", m, w, c, g)
    check("gate exception becomes an error and keeps the verdict visible",
          "gate down" in str(r["error"]) and r["checker_verdict"] is not None)

    m, w, c, g, calls, seen = stubs()
    r = run_request("P", "req", "notes", m, w, c, g)
    check("gate payload has the expected keys",
          set(seen["payload"]) == {"request", "route", "reason", "writer_result", "checker_verdict",
                                   "writer_family", "checker_family", "independent"})

    m, w, c, g, calls, seen = stubs(wf="groq", cf="groq")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("APPROVE from the same provider is blocked and nothing is queued",
          calls["gate"] == 0 and "not confirmed" in str(r["error"]) and r["writer_result"] is not None
          and r["checker_verdict"] is not None and r["independent"] is False)

    m, w, c, g, calls, seen = stubs(wf="", cf="groq")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("APPROVE with an unknown Writer provider is blocked", calls["gate"] == 0 and "unknown" in str(r["error"]))

    m, w, c, g, calls, seen = stubs(wf="google", cf="")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("APPROVE with an unknown Checker provider is blocked", calls["gate"] == 0 and "unknown" in str(r["error"]))

    m, w, c, g, calls, seen = stubs(wf="Groq", cf="groq")
    r = run_request("P", "req", "notes", m, w, c, g)
    check("the provider comparison ignores case", calls["gate"] == 0 and r["independent"] is False)

    m, w, c, g, calls, seen = stubs(wf="groq", cf="groq", verdict={"verdict": "REJECT", "issues": ["x"], "family": "groq"})
    r = run_request("P", "req", "notes", m, w, c, g)
    check("a REJECT from the same provider is still queued so you can see it",
          calls["gate"] == 1 and r["independent"] is False and verdict_text(r["checker_verdict"]) == "REJECT")

    m, w, c, g, calls, seen = stubs(verdict={"verdict": "REJECT", "issues": ["code"], "model": "code checks only", "family": ""})
    r = run_request("P", "req", "notes", m, w, c, g)
    check("a code-only REJECT with no provider is still queued", calls["gate"] == 1 and r["error"] is None)

    m, w, c, g, calls, seen = stubs()
    r = run_request("P", "req", "notes", m, w, c, g)
    check("the result reports both providers",
          r["writer_family"] == "google" and r["checker_family"] == "groq" and seen["payload"]["independent"] is True)

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


if __name__ == "__main__":
    run_tests()