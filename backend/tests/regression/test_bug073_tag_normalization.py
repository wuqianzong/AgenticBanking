"""BUG-073: a valid product/intent pair written in the other spelling style
("General_Inquiries" for "General Inquiries") was rejected by an exact match,
so the search never ran (h1_contactless_not_working_1, 15 and 16 Oct).
Tags are now matched spelling-insensitively and rewritten in the matrix's own
spelling; invalid pairs and unknown tags are still rejected."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N

checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


# The task the query step actually wrote for "Should I uninstall the app before I try it again?"
recorded = {
    "task_id": "task_1",
    "customer_component": "Should I uninstall the app before I try it again?",
    "search_query": "uninstall the app",
    "procedural": "Follow the troubleshooting steps to reinstall the application properly.",
    "policy": "Ensure proper network connection and credentials before reinstalling.",
    "conceptual": "Uninstalling the app removes local cached data and resets application settings.",
    "target_commercial_product": ["Horizon_APP"],
    "target_intent": ["General_Inquiries"],
}
out = N._normalize_search_task_scope(dict(recorded))
check("recorded Horizon_APP + General_Inquiries task is accepted", out is not None)
check("its intent is rewritten in the matrix spelling", bool(out) and out["target_intent"] == ["General Inquiries"])
check("its search filter uses the matrix spelling",
      N._valid_product_intent_scope_pairs(recorded) == [{"product": "Horizon_APP", "intents": ["General Inquiries"]}])

disputes = {**recorded, "target_commercial_product": ["Deposit_Account"], "target_intent": ["Transaction_Disputes"]}
check("Transaction_Disputes is accepted as Transaction Disputes",
      (N._normalize_search_task_scope(dict(disputes)) or {}).get("target_intent") == ["Transaction Disputes"])

spaced = {**recorded, "target_commercial_product": ["horizon app"], "target_intent": ["general inquiries"]}
out = N._normalize_search_task_scope(dict(spaced))
check("product and intent in another case and spacing are accepted",
      bool(out) and out["target_commercial_product"] == "Horizon_APP" and out["target_intent"] == ["General Inquiries"])

canonical = {**recorded, "target_intent": ["General Inquiries"]}
check("an already-canonical task is unchanged",
      N._normalize_search_task_scope(dict(canonical)) == N._normalize_search_task_scope(dict(recorded)))

invalid_pair = {**recorded, "target_commercial_product": ["Stocks"], "target_intent": ["Application"]}
check("a pair the matrix does not allow is still rejected", "Application" not in N.PRODUCT_INTENT_MATRIX["Stocks"]
      and N._normalize_search_task_scope(dict(invalid_pair)) is None)

unknown = {**recorded, "target_intent": ["General_Questions"]}
check("an unknown intent is still rejected", N._normalize_search_task_scope(dict(unknown)) is None)

check("an unknown product is dropped", N._normalized_product_tags(["Horizon_Watch"]) == [])
check("intent tags are de-duplicated across spellings",
      N._normalized_intent_tags(["General_Inquiries", "General Inquiries", "general inquiries"]) == ["General Inquiries"])

print(f"{sum(checks)}/{len(checks)}")
sys.exit(0 if all(checks) else 1)
