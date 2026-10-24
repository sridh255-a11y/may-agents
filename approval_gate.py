import sqlite3
import json
import os

DB_PATH = "may_agents.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            request_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_request(project, request_type, payload_dict):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    payload_str = json.dumps(payload_dict)
    cursor.execute(
        "INSERT INTO requests (project, request_type, payload, status) VALUES (?, ?, ?, 'pending')",
        (project, request_type, payload_str)
    )
    req_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO audit_log (request_id, action, details) VALUES (?, ?, ?)",
        (req_id, "CREATE", f"Created {request_type} request for project {project}")
    )
    conn.commit()
    conn.close()
    return req_id

def list_pending():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM requests WHERE status = 'pending' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "project": r["project"],
            "request_type": r["request_type"],
            "payload": json.loads(r["payload"]),
            "status": r["status"],
            "created_at": r["created_at"]
        })
    return result

def approve_request(req_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE requests SET status = 'approved' WHERE id = ?", (req_id,))
    cursor.execute(
        "INSERT INTO audit_log (request_id, action, details) VALUES (?, ?, ?)",
        (req_id, "APPROVE", f"Request {req_id} approved")
    )
    conn.commit()
    conn.close()
    return True

def reject_request(req_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE requests SET status = 'rejected' WHERE id = ?", (req_id,))
    cursor.execute(
        "INSERT INTO audit_log (request_id, action, details) VALUES (?, ?, ?)",
        (req_id, "REJECT", f"Request {req_id} rejected")
    )
    conn.commit()
    conn.close()
    return True

def get_audit_log():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_log ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "request_id": r["request_id"],
            "action": r["action"],
            "details": r["details"],
            "timestamp": r["timestamp"]
        })
    return result

def test_approval_gate():
    print("Running approval_gate tests...")
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except Exception:
            pass

    init_db()

    # Test 1: Add request
    req_id = add_request("YouTube RankPeak", "Research", {"query": "Top AI Trends"})
    pending = list_pending()
    assert len(pending) == 1, "Should have 1 pending request"
    assert pending[0]["id"] == req_id, "ID should match"
    print("Test 1 (Add & List): PASS")

    # Test 2: Approve request
    approve_request(req_id)
    pending_after = list_pending()
    assert len(pending_after) == 0, "No pending requests after approval"
    print("Test 2 (Approve): PASS")

    # Test 3: Reject request flow
    req_id_2 = add_request("Money-Making App", "Create", {"title": "App Plan"})
    reject_request(req_id_2)
    logs = get_audit_log()
    # req1 had CREATE + APPROVE (2 logs), req2 had CREATE + REJECT (2 logs) -> Total 4 logs
    assert len(logs) == 4, f"Should have 4 logs total, got {len(logs)}"
    print("Test 3 (Reject & Audit): PASS")

    print("approval_gate.py tests: ALL PASS")

if __name__ == "__main__":
    test_approval_gate()
