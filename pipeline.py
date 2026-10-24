import agent_manager
import agent_writer
import agent_checker
import approval_gate

def run_request(project, request, notes, manager_fn=None, writer_fn=None, checker_fn=None, gate_fn=None):
    manager_fn = manager_fn or agent_manager.route
    writer_fn = writer_fn or agent_writer.write
    checker_fn = checker_fn or agent_checker.check_writer_output
    gate_fn = gate_fn or approval_gate.add_request

    try:
        route_data = manager_fn(request)
        route = route_data.get("route")

        if route != "writer":
            return {
                "route": route,
                "message": f"Agent for route '{route}' is not built yet."
            }

        writer_result = writer_fn(request, notes)
        if "error" in writer_result:
            return writer_result

        checker_verdict = checker_fn(writer_result, notes)

        gate_id = gate_fn(project, "Writer", {
            "writer_result": writer_result,
            "checker_verdict": checker_verdict
        })

        return {
            "route": route,
            "writer_result": writer_result,
            "checker_verdict": checker_verdict,
            "gate_id": gate_id
        }
    except Exception as e:
        return {"error": str(e)}

def test_pipeline():
    # Mocks
    def mock_manager(req): return {"route": "writer"}
    def mock_writer(topic, notes): return {"title": "T", "script": "S", "claims": []}
    def mock_checker(res, notes): return {"verdict": "APPROVE"}
    def mock_gate(proj, rtype, payload): return 123

    # Case 1: Writer route APPROVE
    res1 = run_request("P", "R", "N", mock_manager, mock_writer, mock_checker, mock_gate)
    print("Test 1 (Writer APPROVE):", "PASS" if res1.get("checker_verdict", {}).get("verdict") == "APPROVE" else "FAIL")

    # Case 2: Writer route REJECT
    def mock_checker_rej(res, notes): return {"verdict": "REJECT"}
    res2 = run_request("P", "R", "N", mock_manager, mock_writer, mock_checker_rej, mock_gate)
    print("Test 2 (Writer REJECT):", "PASS" if res2.get("checker_verdict", {}).get("verdict") == "REJECT" else "FAIL")

    # Case 3: Non-writer route
    def mock_manager_non(req): return {"route": "analytics"}
    res3 = run_request("P", "R", "N", mock_manager_non)
    print("Test 3 (Non-writer):", "PASS" if res3.get("route") == "analytics" else "FAIL")

    # Case 4: Writer error
    def mock_writer_err(topic, notes): return {"error": "Failed"}
    res4 = run_request("P", "R", "N", mock_manager, mock_writer_err)
    print("Test 4 (Writer error):", "PASS" if res4.get("error") == "Failed" else "FAIL")

    # Case 5: Exception
    def mock_manager_exc(req): raise Exception("Boom")
    res5 = run_request("P", "R", "N", mock_manager_exc)
    print("Test 5 (Exception):", "PASS" if res5.get("error") == "Boom" else "FAIL")

if __name__ == "__main__":
    test_pipeline()
