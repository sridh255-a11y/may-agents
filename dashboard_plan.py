import sys
from model_client import redact

SAMPLE_PLAN = {
    "goal": "Script about Tool X",
    "steps": [
        {"id": 1, "type": "write", "agent": "Writer", "title": "Draft the script", "input": "", "depends_on": [], "available": True},
        {"id": 2, "type": "research", "agent": "Research", "title": "Find sources", "input": "", "depends_on": [1], "available": False},
        {"id": 3, "type": "check", "agent": "Checker", "title": "Review the draft", "input": "", "depends_on": [1], "available": True},
        {"id": 4, "type": "approve", "agent": "Approval gate", "title": "Your approval", "input": "", "depends_on": [3], "available": True},
    ],
}


def plan_lines(plan):
    lines = []
    for s in plan.get("steps", []):
        flag = "" if s.get("available") else " (not built yet)"
        lines.append("%d. [%s] %s%s" % (s.get("id", 0), s.get("agent", ""), s.get("title", ""), flag))
    return "\n".join(lines)


def plan_payload(payload):
    plan = payload.get("plan") or {"goal": "", "steps": []}
    return {
        "request": payload.get("request", ""),
        "route": "director",
        "reason": plan.get("goal", ""),
        "writer_result": {"title": plan.get("goal", ""), "script": plan_lines(plan)},
        "checker_verdict": {"verdict": "PLAN", "model": payload.get("model", ""), "family": payload.get("family", "")},
        "plan": plan,
        "executed": False,
    }


def plan_result(project, request, res):
    plan = res.get("plan")
    out = {
        "project": project,
        "request": request,
        "route": "director",
        "reason": "",
        "writer_result": None,
        "checker_verdict": None,
        "gate_id": res.get("gate_id"),
        "error": None,
        "message": "",
        "executed": False,
    }
    if plan:
        out["reason"] = plan.get("goal", "")
        out["writer_result"] = {"title": plan.get("goal", ""), "script": plan_lines(plan)}
        out["checker_verdict"] = {"verdict": "PLAN", "model": res.get("model", ""), "family": res.get("family", "")}
    if res.get("status") == "awaiting_approval":
        out["message"] = "Plan ready. Added to the approval gate as pending. Approving records your approval only. Nothing runs yet."
    else:
        out["error"] = "Plan failed: " + "; ".join(res.get("errors") or ["unknown problem"])
    return out


def run_plan(project, request, notes, gate_fn=None, plan_fn=None):
    if plan_fn is None:
        import agent_director
        plan_fn = agent_director.plan_request
    if gate_fn is None:
        import approval_gate
        gate_fn = approval_gate.add_request

    def shaped_gate(proj, request_type, payload):
        return gate_fn(proj, request_type, plan_payload(payload))

    try:
        res = plan_fn(project, request, notes, gate_fn=shaped_gate, store=True)
    except Exception as e:
        return {
            "project": project, "request": request, "route": "director", "reason": "",
            "writer_result": None, "checker_verdict": None, "gate_id": None,
            "error": "Plan failed: " + redact(type(e).__name__ + ": " + str(e)),
            "message": "", "executed": False,
        }
    if not isinstance(res, dict):
        res = {"status": "error", "errors": ["The Director returned a bad result."]}
    return plan_result(project, request, res)


def run_tests():
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    lines = plan_lines(SAMPLE_PLAN).split("\n")
    check("plan lines are numbered with the agent, and unbuilt agents are flagged",
          len(lines) == 4 and lines[0] == "1. [Writer] Draft the script" and lines[1].endswith("(not built yet)"))

    payload = plan_payload({"request": "r", "plan": SAMPLE_PLAN, "executed": False, "model": "m", "family": "groq"})
    check("the stored payload looks like a pipeline item, so the dashboard can show it",
          set(payload) == {"request", "route", "reason", "writer_result", "checker_verdict", "plan", "executed"}
          and payload["route"] == "director" and payload["checker_verdict"]["verdict"] == "PLAN"
          and payload["executed"] is False and "Draft the script" in payload["writer_result"]["script"])

    ok_res = {"status": "awaiting_approval", "plan": SAMPLE_PLAN, "errors": [], "model": "m", "family": "groq", "gate_id": 7}
    r3 = plan_result("P", "req", ok_res)
    check("a ready plan gives a message, a PLAN verdict and the gate id, with no error",
          r3["error"] is None and "Nothing runs yet" in r3["message"] and r3["gate_id"] == 7
          and r3["checker_verdict"]["verdict"] == "PLAN" and r3["writer_result"]["title"] == "Script about Tool X")

    r4 = plan_result("P", "req", {"status": "invalid", "plan": None, "errors": ["The plan has no steps."]})
    check("an invalid plan gives an error with the reason, and nothing to approve",
          str(r4["error"]).startswith("Plan failed") and "no steps" in r4["error"]
          and r4["writer_result"] is None and r4["gate_id"] is None)

    r5 = plan_result("P", "req", {"status": "error", "plan": SAMPLE_PLAN, "errors": ["Could not save the plan"]})
    check("a failure after planning keeps the plan visible", r5["error"] is not None and r5["writer_result"] is not None)

    seen = {}

    def gate(project, request_type, payload):
        seen["args"] = (project, request_type, payload)
        return 11

    def director(project, request, notes, gate_fn=None, store=False):
        seen["store"] = store
        gid = gate_fn(project, "Plan", {"request": request, "plan": SAMPLE_PLAN, "executed": False,
                                        "model": "m", "family": "groq"}) if store else None
        return {"status": "awaiting_approval", "plan": SAMPLE_PLAN, "errors": [], "model": "m",
                "provider": "groq/m", "family": "groq", "calls": 1, "executed": False, "gate_id": gid}

    r6 = run_plan("P", "req", "n", gate_fn=gate, plan_fn=director)
    check("the gate receives the dashboard-shaped payload and returns its id",
          r6["gate_id"] == 11 and seen["args"][1] == "Plan" and seen["args"][2]["route"] == "director"
          and seen["args"][2]["checker_verdict"]["verdict"] == "PLAN")
    check("plans are always stored through the gate", seen["store"] is True)

    def boom(project, request, notes, gate_fn=None, store=False):
        raise RuntimeError("down freellmapi-0123456789abcdef")

    r8 = run_plan("P", "req", "n", gate_fn=gate, plan_fn=boom)
    check("an exception becomes an error result, not a crash", r8["error"] is not None and r8["gate_id"] is None)
    check("keys are redacted from error text", "[redacted]" in r8["error"] and "0123456789abcdef" not in r8["error"])

    r11 = run_plan("P", "req", "n", gate_fn=gate, plan_fn=lambda *a, **k: "oops")
    check("a bad result from the Director is an error", r11["error"] is not None and "bad result" in r11["error"])

    check("nothing is ever marked as executed",
          all(x["executed"] is False for x in (r3, r4, r5, r6, r8, r11)))

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


def run_api_tests():
    import dashboard_api
    results = []

    def check(name, ok):
        print(("PASS" if ok else "FAIL") + " | " + name)
        results.append(bool(ok))

    def director_ok(project, request, notes, gate_fn=None, store=False):
        gid = gate_fn(project, "Plan", {"request": request, "plan": SAMPLE_PLAN, "executed": False,
                                        "model": "m", "family": "groq"}) if store else None
        return {"status": "awaiting_approval", "plan": SAMPLE_PLAN, "errors": [], "model": "m",
                "provider": "groq/m", "family": "groq", "calls": 1, "executed": False, "gate_id": gid}

    def director_invalid(project, request, notes, gate_fn=None, store=False):
        return {"status": "invalid", "plan": None, "errors": ["The plan has 7 steps. The limit is 6."],
                "model": "m", "provider": "", "family": "", "calls": 2, "executed": False, "gate_id": None}

    def make(director=director_ok):
        log = {"added": [], "ran": 0}

        def run(project, request, notes, gate_fn=None):
            log["ran"] += 1
            gid = gate_fn(project, "Writer", {})
            return {"route": "writer", "error": None, "gate_id": gid}

        def add(project, request_type, payload):
            log["added"].append((project, request_type, payload))
            return 5

        def plan(project, request, notes, gate_fn=None):
            return run_plan(project, request, notes, gate_fn=gate_fn, plan_fn=director)

        api = dashboard_api.DashboardApi(run_fn=run, plan_fn=plan, add_fn=add, list_fn=lambda: [],
                                         approve_fn=lambda i: True, reject_fn=lambda i: True,
                                         link_fn=lambda: True, background=False)
        return api, log

    good = {"project": "Ebooks", "request": "Make a script", "notes": "n", "mode": "plan"}

    api, log = make()
    code, body = api.handle("POST", "/api/submit", good)
    code2, task = api.handle("GET", "/api/task", {"id": body.get("task_id")})
    check("plan mode makes a done task from the Director and does not run the pipeline",
          code == 200 and task["status"] == "done" and task["result"]["route"] == "director"
          and log["ran"] == 0 and len(log["added"]) == 1 and log["added"][0][1] == "Plan")
    stored = log["added"][0][2]
    check("the stored plan is shaped like a pipeline item with a PLAN verdict",
          stored["checker_verdict"]["verdict"] == "PLAN" and "writer_result" in stored and stored["executed"] is False)

    api, log = make()
    code, body = api.handle("POST", "/api/submit", {"project": "Ebooks", "request": "Make a script", "notes": "n"})
    check("the default mode still runs the pipeline and not the planner",
          code == 200 and log["ran"] == 1 and log["added"][0][1] == "Writer")

    api, log = make()
    code, body = api.handle("POST", "/api/submit", {"project": "Ebooks", "request": "x", "notes": "", "mode": "bogus"})
    check("an unknown mode gives 400 and runs nothing", code == 400 and log["ran"] == 0 and log["added"] == [])

    api, log = make(director_invalid)
    code, body = api.handle("POST", "/api/submit", good)
    code2, task = api.handle("GET", "/api/task", {"id": body.get("task_id")})
    check("an invalid plan makes an error task and adds nothing to the gate",
          task["status"] == "error" and "Plan failed" in task["result"]["error"] and log["added"] == [])

    api, log = make()

    def plan_cancel(project, request, notes, gate_fn=None):
        api.cancel({"id": 1})
        return run_plan(project, request, notes, gate_fn=gate_fn, plan_fn=director_ok)

    api.plan_fn = plan_cancel
    api.handle("POST", "/api/submit", good)
    code, task = api.handle("GET", "/api/task", {"id": 1})
    check("a plan cancelled while planning is never added to the gate",
          task["status"] == "cancelled" and log["added"] == [])

    api, log = make()
    code, body = api.handle("GET", "/api/status")
    check("the status endpoint still lists 7 projects", code == 200 and len(body["projects"]) == 7)

    print(str(sum(results)) + " / " + str(len(results)) + " passed")


if __name__ == "__main__":
    if "--api" in sys.argv:
        run_api_tests()
    else:
        run_tests()
