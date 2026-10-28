"""Step-up check on service-request submission.

Commit 8c6f94e routed every form straight to completion, so a form marked
requires_step_up completed without its credential check. A step-up form must
stay pending at /submit and complete only through a passed /credential-check.
The address change needs step-up (MAS TRM 2021 para 14.2.3).
Runs on a temporary database; no model calls.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import backend.app.api_gateway as G
import backend.app.services.service_request_service as S
from backend.app.orchestration.service_request_registry import get_service_request_contract

S.DB_PATH = str(Path(tempfile.mkdtemp()) / "ledger.db")
S.log_event = lambda *a, **k: None
G.log_event = lambda *a, **k: None

checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


def register(service_request_id):
    contract = get_service_request_contract(service_request_id)
    return S.register("sess", "USR_TEST", service_request_id, "need", [], [],
                      contract["risk_level"], contract["requires_step_up"])["special_id"]


def submit(special_id, data):
    return asyncio.run(G.submit_service_request(G.ServiceRequestSubmitPayload(
        special_id=special_id, user_id="USR_TEST", session_id="sess", submitted_data=data)))


def credential(special_id, passed):
    return asyncio.run(G.credential_check_service_request(G.ServiceRequestCredentialCheckPayload(
        special_id=special_id, user_id="USR_TEST", session_id="sess", passed=passed)))


address = get_service_request_contract("profile-address-update")
check("address change is intermediate risk with step-up", address["risk_level"] == "intermediate" and address["requires_step_up"])

ADDRESS = {"line1": "21 Example Road", "postal": "999999", "confirm": True}
sid = register("profile-address-update")
out = submit(sid, ADDRESS)
check("step-up form: /submit asks for the credential check", out == {"status": "credential_check_required", "special_id": sid})
held = S.get(sid)
check("step-up form: held pending with its payload", held["status"] == "pending" and held["submitted_data"] == ADDRESS)

out = credential(sid, False)
held = S.get(sid)
check("failed check: not completed, payload dropped", out["status"] == "credential_check_failed" and held["status"] == "pending" and not held["submitted_data"])
check("failed check: a second check without a resubmission is refused", credential(sid, True)["status"] == "error")

submit(sid, ADDRESS)
out = credential(sid, True)
done = S.get(sid)
check("passed check: completed with the held payload", out["status"] == "completed" and done["status"] == "completed" and done["submitted_data"] == ADDRESS)
check("passed check: one-time grant used up", done["grant_consumed"] and not done["grant_id"])
check("completed form: a further submission is refused", submit(sid, ADDRESS)["status"] == "error")

kyc = register("kyc-document-resubmission")
check("identity-document form also needs the check", submit(kyc, {"doc": "x"})["status"] == "credential_check_required")

card = register("card-replacement-request")
check("low-risk form completes on /submit", submit(card, {"reason": "lost"})["status"] == "completed")
check("low-risk form: /credential-check is refused", credential(register("card-replacement-request"), True)["status"] == "error")

print(f"{sum(checks)}/{len(checks)}")
sys.exit(0 if all(checks) else 1)
