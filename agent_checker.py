import json
from model_client import ask

CHECKER_MODEL = "gpt-oss-120b"


def check_writer_output(writer_result, source_notes):
    missing_quotes = []

    claims = writer_result.get("claims", [])
    for claim in claims:
        quote = claim.get("quote", "")
        if quote and quote != "NOT FOUND" and quote not in source_notes:
            missing_quotes.append(claim)

    prompt = f"""
Source Notes:
{source_notes}

Writer Result:
{json.dumps(writer_result, indent=2)}

Please review the Writer Result against the Source Notes.
Return a valid JSON object with the following fields:
- verdict: "APPROVE" or "REJECT"
- issues: a list of string issues found
- unsupported_claims: a list of claims that are not fully supported by the notes
"""

    system_prompt = (
        "You are a strict factual Checker. Review the Writer output against the source notes. "
        "Output strictly valid JSON with keys: verdict, issues, unsupported_claims."
    )

    llm_raw = ask(prompt, system_prompt=system_prompt, model=CHECKER_MODEL)

    try:
        start_idx = llm_raw.find("{")
        end_idx = llm_raw.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            llm_json = json.loads(llm_raw[start_idx:end_idx])
        else:
            llm_json = json.loads(llm_raw)
    except Exception as e:
        llm_json = {
            "verdict": "REJECT",
            "issues": [f"Failed to parse LLM JSON output: {str(e)}"],
            "unsupported_claims": []
        }

    if missing_quotes:
        llm_json["verdict"] = "REJECT"
        if "issues" not in llm_json or not isinstance(llm_json["issues"], list):
            llm_json["issues"] = []
        llm_json["issues"].append("Code check failed: Quoted text does not exist verbatim in source notes.")

    return llm_json


def test_checker():
    source_notes = "Python was created by Guido van Rossum and released in 1991."

    good_writer_result = {
        "title": "Python History",
        "script": "Python was created by Guido van Rossum.",
        "claims": [
            {"claim": "Guido created Python", "quote": "Guido van Rossum"}
        ]
    }

    bad_writer_result = {
        "title": "Python History",
        "script": "Python was created by James Gosling in 1995.",
        "claims": [
            {"claim": "James Gosling created Python", "quote": "James Gosling"}
        ]
    }

    print("Running Test 1 (Good Input)...")
    try:
        res1 = check_writer_output(good_writer_result, source_notes)
        if res1.get("verdict") == "APPROVE":
            print("Test 1: PASS")
        else:
            print(f"Test 1: FAIL ({res1})")
    except Exception as e:
        print(f"Test 1: FAIL (Exception: {e})")

    print("\nRunning Test 2 (Bad Input)...")
    try:
        res2 = check_writer_output(bad_writer_result, source_notes)
        if res2.get("verdict") == "REJECT":
            print("Test 2: PASS")
        else:
            print(f"Test 2: FAIL ({res2})")
    except Exception as e:
        print(f"Test 2: FAIL (Exception: {e})")


if __name__ == "__main__":
    test_checker()
