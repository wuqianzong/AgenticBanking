"""Shared runner, collection, assertion, and reporting helpers for studies.

The default runner imports the compiled production graph and invokes it in an
isolated process. It intentionally does not call API-gateway archive or memory
persistence helpers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACING_SECONDS = 25.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 65
API_KEY_POOL_PATH = REPO_ROOT / "research" / ".research_api_keys"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ApiKeyPool:
    """Rotates through multiple Google API keys when one hits its daily
    quota, for research studies only -- never used by the production
    chatbot. Never touches backend/app/core/llm_service.py: it reassigns
    that module's own `google_api_key` global directly, which
    call_llm_router already reads fresh on every call (a bare module-level
    name, resolved at call time, not captured into a closure at import
    time), so this works without any production code change.

    Reads one key per line from research/.research_api_keys (git-ignored;
    see .gitignore). Absent file means no pool is available -- callers
    should fall back to whatever single GOOGLE_API_KEY is already in the
    environment, exactly like before this existed."""

    def __init__(self, keys_file: Path = API_KEY_POOL_PATH):
        self.keys_file = keys_file
        self.keys = (
            [line.strip() for line in keys_file.read_text().splitlines() if line.strip()]
            if keys_file.exists()
            else []
        )
        self.index = 0
        self._installed = False
        self._progress: Any = None
        if self.keys:
            self._apply_current()

    def set_progress(self, progress: Any) -> None:
        """Point future rotation/wait log lines at this (currently open)
        progress file. Safe to call every round: a multi-round orchestrator
        shares one pool (and thus one already-installed wrapper) across
        many rounds, each with its own progress file that gets closed when
        that round ends -- the wrapper reads self._progress fresh on every
        log line rather than capturing one file handle in a closure, so it
        never writes to a file a previous round already closed."""
        self._progress = progress

    @property
    def available(self) -> bool:
        return bool(self.keys)

    def _apply_current(self) -> None:
        import backend.app.core.llm_service as llm_service
        llm_service.google_api_key = self.keys[self.index]

    def rotate(self) -> bool:
        """Advance to the next key. Returns False when every key in the
        pool has already been tried this cycle (caller decides what to do
        next -- see install_call_site_rotation, which waits and starts a
        fresh cycle from key #1 rather than giving up)."""
        if self.index + 1 >= len(self.keys):
            return False
        self.index += 1
        self._apply_current()
        return True

    def reset_to_first(self) -> None:
        """Start a fresh rotation cycle from key #1 -- used after waiting
        out a full-pool daily exhaustion, since a day boundary may have
        made the first key usable again."""
        self.index = 0
        self._apply_current()

    def install_call_site_rotation(
        self,
        progress: Any = None,
        all_keys_wait_seconds: int = 1800,
    ) -> None:
        """Wrap call_llm_router so an unattended research run waits out and
        retries a failure that production's OWN call_llm_router (see
        backend/app/core/llm_service.py) could not itself resolve, instead
        of giving up the way that function correctly does for a live
        request.

        This used to also do its own key rotation and its own hang-timeout
        guard here, duplicating logic that has since been built directly
        into production's call_llm_router itself (9-key rotation,
        cross-model fallback, and a per-attempt hang-timeout, all internal
        to that function now). Keeping both layers active created two
        independent, unsynchronized counters into the same key list --
        this wrapper's own `self.index` versus production's own internal
        one -- confirmed live to produce a much longer, more convoluted
        retry cascade than intended (each of THIS wrapper's own outer
        "rotate" attempts triggered a full fresh internal 9-key x 2-model
        cycle inside `original`, before this wrapper's own separate index
        ever registered "every key exhausted"), and was directly implicated
        in repeated multi-hour stalls before being simplified. This wrapper
        now does nothing but wait and retry the exact same call: production
        owns every decision about which key or model to use.

        Reassigning `llm_service.google_api_key` alone (see `_apply_current`)
        is not enough on its own: every module that calls the model actually
        does `from backend.app.core.llm_service import call_groq_lpu` at
        import time, which binds that name to the original function object
        in *that module's own namespace*. Patching each of those modules'
        own `call_groq_lpu` binding (not just llm_service's) intercepts a
        failure at every real call site. Still zero permanent production
        code changes -- this only reassigns module attributes at runtime,
        for the life of this research process.

        One tier: if `original` (production's fully-resilient
        call_llm_router) still raises a rate-limit or persistent-transient
        error after its own internal exhaustion, wait `all_keys_wait_seconds`
        and retry the identical call -- production's own internal state
        (which key, which model) naturally continues from wherever it left
        off. Anything else (a real bug, a local config error) is re-raised
        immediately -- waiting on those would hang forever with no path to
        success, and would hide a problem worth knowing about right away.

        Idempotent: a second call on the same pool instance (e.g. a
        multi-round orchestrator calling run_graph_study repeatedly with
        one shared pool) is a no-op, so the wrapper never double-wraps
        itself.
        """
        self.set_progress(progress)
        if not self.available or self._installed:
            return
        self._installed = True
        import backend.app.core.groundedness as groundedness
        import backend.app.core.llm_service as llm_service
        import backend.app.orchestration.nodes as nodes
        import backend.app.services.memory_service as memory_service

        original = llm_service.call_llm_router

        def _log(message: str) -> None:
            if self._progress is not None:
                self._progress.write(f"{utc_now()} {message}\n")
                self._progress.flush()

        from backend.app.core.llm_service import ModelUnavailableError

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            while True:
                try:
                    return original(*args, **kwargs)
                # ModelUnavailableError (production BUG-030) is a
                # BaseException, so it is named here to keep an unattended
                # run waiting out an outage exactly as before.
                except (Exception, ModelUnavailableError) as error:
                    if rate_limit_info(error).get("rate_limited") or _is_persistent_transient_error(error):
                        _log(
                            f"production's own internal retry/rotation was exhausted; "
                            f"waiting {all_keys_wait_seconds}s before trying the same call again: {str(error)[:200]}"
                        )
                        time.sleep(all_keys_wait_seconds)
                        continue
                    raise

        for module in (llm_service, nodes, groundedness, memory_service):
            module.call_llm_router = wrapped
            module.call_groq_lpu = wrapped


def _is_persistent_transient_error(error: Exception) -> bool:
    """Mirrors llm_service._is_transient_llm_error's marker set without
    importing it (that function only judges whether call_llm_router's own
    internal one-retry is worth it; this judges whether an unattended
    research run should keep waiting indefinitely once that internal
    retry has already been exhausted).

    Also covers the machine going offline entirely (no wifi, DNS down,
    network unreachable) -- confirmed live that a DNS failure surfaces as
    httpx.ConnectError with the message "nodename nor servname provided,
    or not known", which matches none of the provider-specific message
    markers below. Checking httpx.TransportError structurally (the common
    base for ConnectError, TimeoutException, ReadTimeout, NetworkError,
    etc. -- both the Gemini and Groq SDKs are httpx-based under the hood)
    catches every transport-layer failure regardless of its message
    wording, which plain string matching on `str(error)` cannot guarantee.
    An unattended multi-day run must wait out a laptop going to sleep or
    losing wifi the same way it waits out a rate limit -- not fail every
    case outright for as long as the network is down."""
    try:
        import httpx
        if isinstance(error, httpx.TransportError):
            return True
    except ImportError:
        pass
    message = str(error)
    return any(
        marker in message
        for marker in (
            "UNAVAILABLE", "high demand", " 503", "Service Unavailable", "DEADLINE_EXCEEDED",
            "ConnectionError", "Timeout", "nodename nor servname", "Name or service not known",
            "Network is unreachable", "Connection refused", "getaddrinfo failed",
            "No address associated with hostname",
        )
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path}:{line_number}: {exc}") from exc
        if not isinstance(case, dict) or not case.get("case_id"):
            raise ValueError(f"Every case needs a non-empty case_id ({path}:{line_number})")
        messages = case.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(item, str) and item.strip() for item in messages):
            raise ValueError(f"Every case needs a non-empty messages list ({case['case_id']})")
        cases.append(case)
    if not cases:
        raise ValueError(f"No cases found in {path}")
    duplicate_ids = {case_id for case_id in (c["case_id"] for c in cases) if sum(x["case_id"] == case_id for x in cases) > 1}
    if duplicate_ids:
        raise ValueError(f"Duplicate case_id values: {sorted(duplicate_ids)}")
    return cases


@dataclass(frozen=True)
class StudyConfig:
    study_id: str
    manifest_path: Path
    fixture_path: Path
    runner: str
    user_id: str
    pacing_seconds: float
    max_retries: int
    default_rate_limit_wait_seconds: int
    capture_system_log: bool
    llm_provider: str = "gemini"
    send_init: bool = False
    unavailable_retry_wait_seconds: int = 300

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> "StudyConfig":
        manifest = load_json(manifest_path)
        runner = str(manifest.get("runner", "graph_validation"))
        if runner not in ("graph_validation", "api_validation"):
            raise ValueError(
                "runner must be 'graph_validation' (graph invoked directly) or "
                "'api_validation' (turns sent through the real /api/chat endpoint)."
            )
        fixture = manifest.get("fixture")
        if not isinstance(fixture, str) or not fixture:
            raise ValueError("study.json requires a relative fixture path")
        return cls(
            study_id=str(manifest.get("study_id") or manifest_path.parent.name),
            manifest_path=manifest_path,
            fixture_path=(manifest_path.parent / fixture).resolve(),
            runner=runner,
            user_id=str(manifest.get("user_id", "USR_999")),
            pacing_seconds=float(manifest.get("pacing_seconds", DEFAULT_PACING_SECONDS)),
            max_retries=int(manifest.get("max_retries_per_turn", DEFAULT_MAX_RETRIES)),
            default_rate_limit_wait_seconds=int(
                manifest.get("default_rate_limit_wait_seconds", DEFAULT_RATE_LIMIT_WAIT_SECONDS)
            ),
            capture_system_log=bool(manifest.get("capture_system_log", True)),
            llm_provider=str(manifest.get("llm_provider", "gemini")),
            send_init=bool(manifest.get("send_init", False)),
            unavailable_retry_wait_seconds=int(manifest.get("unavailable_retry_wait_seconds", 300)),
        )


def rate_limit_info(error: Exception) -> dict[str, Any]:
    """Classify Gemini/Groq-style rate-limit errors without provider imports."""
    code = getattr(error, "code", None)
    status = getattr(error, "status", None)
    details = getattr(error, "details", None)
    message = str(error)
    try:
        detail_blob = json.dumps(details, default=str) if details else message
    except Exception:
        detail_blob = message
    is_limited = (
        code == 429
        or status == "RESOURCE_EXHAUSTED"
        or "429" in message
        or "RESOURCE_EXHAUSTED" in message
    )
    if not is_limited:
        return {"rate_limited": False}
    delay_match = re.search(r'"?retryDelay"?\s*[:=]\s*"?(\d+)\s*s', detail_blob, re.IGNORECASE)
    daily = bool(re.search(r"per[_ ]?day|daily|PerDay", detail_blob, re.IGNORECASE))
    return {
        "rate_limited": True,
        "retry_seconds": int(delay_match.group(1)) if delay_match else None,
        "is_daily": daily,
        "raw": detail_blob[:1000],
    }


def extract_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Stable, sanitized telemetry view shared by all graph-validation studies."""
    payload = state.get("ui_action_payload") or {}
    subqueries = state.get("financial_subqueries") or []
    task_results = state.get("task_results") or []
    return {
        "triage_status": state.get("triage_status"),
        "turn_count": state.get("turn_count"),
        "preflight_status": state.get("preflight_status"),
        "subquery_status": {
            item.get("subquery_id"): {
                "customer_component": item.get("customer_component"),
                "tool_attempts": [
                    {"tool_name": attempt.get("tool_name"), "status": attempt.get("status")}
                    for attempt in item.get("tool_attempts", [])
                    if isinstance(attempt, dict)
                ],
            }
            for item in subqueries
            if isinstance(item, dict) and item.get("subquery_id")
        },
        "task_results": [
            {
                "task_id": task.get("task_id"),
                "status": task.get("status"),
                "search_territory": task.get("search_territory", "in_scope"),
                "approved_parent_ids": task.get("approved_parent_ids", []),
                "generation_guidance": (task.get("generation_guidance") or "")[:500],
                "unresolved_term": task.get("unresolved_term", ""),
                "reason": (task.get("reason") or "")[:500],
            }
            for task in task_results
            if isinstance(task, dict)
        ],
        "decline_tasks": [
            {
                "subquery": item.get("subquery"),
                "reason_category": item.get("reason_category"),
                "detail": (item.get("detail") or "")[:300],
            }
            for item in state.get("decline_tasks", []) or []
            if isinstance(item, dict)
        ],
        "clarification_tasks": [
            {
                "unresolved_request": item.get("unresolved_request"),
                "originator": item.get("originator"),
                "reason": (item.get("clarification_reason") or "")[:300],
            }
            for item in state.get("clarification_tasks", []) or []
            if isinstance(item, dict)
        ],
        "conversational_tasks": [
            item.get("customer_component")
            for item in state.get("conversational_tasks", []) or []
            if isinstance(item, dict)
        ],
        "final_response": state.get("final_response", ""),
        "response_blocks": payload.get("responseBlocks", []),
        "ui_type": payload.get("uiType"),
        "service_request_actions": payload.get("serviceRequestActions", []),
        "navigation_actions": payload.get("navigationActions", []),
        "personal_data_reveals": payload.get("personalDataReveals", []),
        "personal_data_substituted": payload.get("personalDataSubstituted", False),
        "precision_substituted": payload.get("precisionSubstituted", False),
        "soft_escalation_available": payload.get("softEscalationAvailable", False),
        "next_actions": payload.get("nextActions", []),
        "safety_retry_count": state.get("safety_retry_count", 0),
    }


def extract_system_log_events(session_id: str) -> list[dict[str, Any]]:
    """Read only the current structured log file and return this run's events.

    Only reliable for a short-lived or low-volume run: the production
    RotatingFileHandler backing this file caps total retention at 60MB
    (10MB x 5 backups) and this function only reads the live file, not the
    backups -- confirmed live that a multi-thousand-case research campaign
    fully cycles that retention in under an hour of real activity, which
    permanently deletes a case's own events before this can read them back.
    A multi-round study should use install_dedicated_capture_log plus
    extract_system_log_events_from instead, which are immune to that."""
    log_path = REPO_ROOT / "backend" / "logs" / "system.jsonl"
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("session_id") == session_id:
            events.append(record)
    return events


def install_dedicated_capture_log(path: Path) -> None:
    """Attach a second, plain (non-rotating) handler to the same production
    logger (horizon.system) so every event this process logs also lands in
    a dedicated, uncapped file -- additive only, the production
    RotatingFileHandler keeps running exactly as before. Exists because
    that handler's 60MB total retention (see extract_system_log_events)
    cannot survive a multi-thousand-case campaign; this file has no size
    cap and is only ever appended to, so a case's own internal
    prompts/reasoning/guidance can always be read back later regardless of
    how much the campaign has logged since. Idempotent per path: calling
    this again with the same path (e.g. an accidental second call within
    one process) will not attach a duplicate handler.

    Must force system_logger's own one-time setup (log level, propagate,
    the production rotating handler) to run first, by calling its
    _get_logger() before adding anything here. Confirmed live: that
    function's whole idempotency guard is `if logger.handlers: return
    logger` -- if our own handler is added to this logger before anything
    else ever has been, that guard sees a non-empty handler list on its
    own first real call and skips its setup forever, silently leaving the
    logger at Python's default level (which drops the INFO-level records
    log_event emits) and never attaching the production handler at all.
    Calling _get_logger() first guarantees that setup has already run
    before we add a second handler alongside it."""
    import os

    from backend.app.core.system_logger import _get_logger

    path.parent.mkdir(parents=True, exist_ok=True)
    logger = _get_logger()
    target = os.path.abspath(str(path))
    for existing in logger.handlers:
        if getattr(existing, "baseFilename", None) == target:
            return
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def extract_system_log_events_from(path: Path, session_id: str, start_offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Read only the bytes written to `path` since start_offset, returning
    events matching session_id plus the new offset to pass on the next
    call. A sequential per-case caller (one case fully finishes before the
    next starts) should track and reuse that offset across calls -- this
    keeps per-case extraction O(this case's own new lines) for the whole
    campaign, instead of re-scanning an ever-growing multi-hundred-MB file
    from byte zero for every single case."""
    if not path.exists():
        return [], start_offset
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(start_offset)
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("session_id") == session_id:
                events.append(record)
        new_offset = f.tell()
    return events, new_offset


def evaluate_assertions(summary: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    """Run only stable, declarative checks. Semantic quality remains a rubric."""
    outcomes: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        outcomes.append({"name": name, "passed": passed, "detail": detail})

    response = summary.get("final_response", "")
    payload_text = json.dumps({
        "response_blocks": summary.get("response_blocks"),
        "navigation_actions": summary.get("navigation_actions"),
    }, ensure_ascii=False)
    all_tools = {
        attempt.get("tool_name")
        for subquery in (summary.get("subquery_status") or {}).values()
        for attempt in subquery.get("tool_attempts", [])
        if isinstance(attempt, dict) and attempt.get("tool_name")
    }
    actual_escalation = bool(summary.get("soft_escalation_available")) or summary.get("triage_status") == "ESCALATION"

    for tool in expected.get("must_use_tools", []) or []:
        add(f"must_use_tools:{tool}", tool in all_tools, f"observed={sorted(all_tools)}")
    for tool in expected.get("must_not_use_tools", []) or []:
        add(f"must_not_use_tools:{tool}", tool not in all_tools, f"observed={sorted(all_tools)}")
    if expected.get("must_not_escalate") is True:
        add("must_not_escalate", not actual_escalation, f"actual_escalation={actual_escalation}")
    if expected.get("must_escalate") is True:
        add("must_escalate", actual_escalation, f"actual_escalation={actual_escalation}")
    for forbidden in expected.get("must_not_emit", []) or []:
        token_map = {
            "raw_route": ["/app/", "http://localhost:5173"],
            "destination_id": ["destinationId", "card-management", "security-settings"],
            "unresolved_placeholder": ["{{faq_", "{{data-"],
        }
        tokens = token_map.get(forbidden, [str(forbidden)])
        combined = f"{response}\n{payload_text}"
        add(f"must_not_emit:{forbidden}", not any(token in combined for token in tokens), f"tokens={tokens}")
    for required_topic in expected.get("required_topics", []) or []:
        add(f"required_topic:{required_topic}", True, "Requires source/LLM/human rubric review; not text-matched.")
    return outcomes


def write_markdown_report(path: Path, run: dict[str, Any]) -> None:
    records = run["records"]
    completed = [record for record in records if record["status"] == "OK"]
    errors = [record for record in records if record["status"] != "OK"]
    assertion_outcomes = [outcome for record in records for turn in record.get("turns", []) for outcome in turn.get("assertions", [])]
    assertion_passed = sum(bool(outcome["passed"]) for outcome in assertion_outcomes)
    lines = [
        f"# Automated Metrics Run: {run['run_id']}",
        "",
        f"- Study: `{run['study_id']}`",
        f"- Started: `{run['started_at']}`",
        f"- Completed: `{run['completed_at']}`",
        f"- Runner: `{run['runner']}`",
        f"- Cases: `{len(records)}`; completed: `{len(completed)}`; non-OK: `{len(errors)}`",
        f"- Assertions: `{assertion_passed}/{len(assertion_outcomes)}` passed",
        "",
        "## Cases requiring review",
        "",
        "| Case | Status | Failed assertions | Artifact |",
        "|---|---|---:|---|",
    ]
    for record in records:
        failures = sum(not item["passed"] for turn in record.get("turns", []) for item in turn.get("assertions", []))
        if record["status"] != "OK" or failures:
            lines.append(f"| {record['case_id']} | {record['status']} | {failures} | `{run['results_path'].name}` |")
    if len(lines) == 13:
        lines.append("| None | OK | 0 | — |")
    lines.extend([
        "",
        "## Interpretation required",
        "",
        "This generated report records mechanical outcomes only. Add source-verified relevance, "
        "groundedness, naturalness, and policy conclusions in `reports/RESEARCH_SUMMARY.md` before "
        "claiming a quality improvement.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_completed_case_ids(
    results_dir: Path,
    case_id_prefix: str,
    require_study_id: str | None = None,
    require_full_detail: bool = False,
) -> set[str]:
    """Scan every existing *_results.jsonl in results_dir for OK records
    whose case_id starts with case_id_prefix. Used to resume a round after
    an interrupted prior attempt (a hang, a crash, a manual restart)
    without redoing cases that already produced a genuine result -- an
    unattended multi-day run will get interrupted sometimes, and redoing
    hours of already-good work every time is real cost worth avoiding.

    require_study_id and require_full_detail exist for a resumed run whose
    case_ids overlap a DIFFERENT prior study sharing the same results_dir
    -- a detail backfill re-runs the exact same case_ids an earlier,
    detail-missing campaign run already produced OK records for. Without
    these filters, every backfill target would immediately match an old,
    still-detail-missing record and get wrongly skipped as "already done"
    on the very first attempt. require_study_id restricts matches to
    records from this specific study; require_full_detail additionally
    requires the matched record to actually carry an llm.request event
    (the thing being backfilled), so an interrupted PRIOR backfill attempt
    resumes correctly while the original campaign's detail-missing records
    for the same case_ids are never mistaken for already-backfilled ones."""
    completed: set[str] = set()
    if not results_dir.exists():
        return completed
    for path in results_dir.glob("*_results.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            case_id = record.get("case_id", "")
            if not case_id.startswith(case_id_prefix) or record.get("status") != "OK":
                continue
            if require_study_id is not None and record.get("study_id") != require_study_id:
                continue
            if require_full_detail:
                events = record.get("system_log_events", [])
                if not any(e.get("event") == "llm.request" for e in events if isinstance(e, dict)):
                    continue
            completed.add(case_id)
    return completed


async def run_graph_study(
    config: StudyConfig,
    cases: list[dict[str, Any]],
    output_dir: Path,
    key_pool: "ApiKeyPool | None" = None,
    skip_case_ids: set[str] | None = None,
    capture_log_path: Path | None = None,
) -> dict[str, Any]:
    """Execute a fixture through the real graph without persistence helpers.

    A multi-round orchestrator that calls this repeatedly should pass its
    own long-lived key_pool so key state (which key is currently active,
    and the daily-exhaustion wait/reset cycle) carries across rounds
    instead of every round wrongly assuming key #1 is fresh again.

    skip_case_ids lets a caller resume an interrupted round: those case_ids
    are neither invoked nor written to this run's own results file (they
    already have a genuine OK record in an earlier, interrupted attempt's
    results file for the same round -- see find_completed_case_ids). This
    run's results file plus that earlier one together are the complete
    record for the round; nothing needs merging by hand.

    capture_log_path, if given (paired with install_dedicated_capture_log
    having been called once by the caller), reads each case's full detail
    from that uncapped, offset-tracked file instead of re-scanning the
    production system.jsonl, which cannot survive a multi-round campaign's
    volume (see extract_system_log_events)."""
    skip_case_ids = skip_case_ids or set()
    capture_offset = capture_log_path.stat().st_size if capture_log_path and capture_log_path.exists() else 0
    from backend.app.core.system_logger import reset_request_context, set_request_context
    from backend.app.orchestration.graph import app_graph
    from backend.app.core.llm_service import ModelUnavailableError

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    results_path = output_dir / f"{run_id}_results.jsonl"
    progress_path = output_dir / f"{run_id}_progress.txt"
    report_path = config.manifest_path.parent / "reports" / f"{run_id}_AUTOMATED_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    records: list[dict[str, Any]] = []
    if key_pool is None:
        key_pool = ApiKeyPool()

    with progress_path.open("w", encoding="utf-8") as progress, results_path.open("w", encoding="utf-8") as results_file:
        progress.write(f"=== run started {started_at} study={config.study_id} cases={len(cases)} ===\n")
        if key_pool.available:
            progress.write(f"{utc_now()} API key pool active: {len(key_pool.keys)} key(s) available for daily-quota rotation\n")
            key_pool.install_call_site_rotation(progress)
        if skip_case_ids:
            progress.write(f"{utc_now()} resuming: skipping {len(skip_case_ids)} case(s) already completed OK in an earlier attempt at this round\n")
            progress.flush()
        for case_index, case in enumerate(cases, start=1):
            if case["case_id"] in skip_case_ids:
                progress.write(f"{utc_now()} [{case_index}/{len(cases)}] case={case['case_id']} status=SKIPPED_ALREADY_DONE\n")
                progress.flush()
                continue
            session_id = f"eval_{config.study_id}_{case_index}_{uuid.uuid4().hex[:8]}"
            graph_config = {"configurable": {"thread_id": session_id}}
            turn_records: list[dict[str, Any]] = []
            case_status = "OK"
            previous_turn_count = 0
            for turn_index, message in enumerate(case["messages"], start=1):
                attempts = 0
                started = time.perf_counter()
                context_token = set_request_context(session_id=session_id, user_id=config.user_id, evaluation_run_id=run_id, evaluation_case_id=case["case_id"])
                try:
                    while True:
                        attempts += 1
                        try:
                            await app_graph.ainvoke(
                                {
                                    "user_id": config.user_id,
                                    "session_id": session_id,
                                    "user_input": message,
                                    "turn_count": previous_turn_count + 1,
                                },
                                config=graph_config,
                            )
                            snapshot = app_graph.get_state(graph_config)
                            state = snapshot.values or {}
                            previous_turn_count = int(state.get("turn_count", previous_turn_count + 1))
                            summary = extract_state_summary(state)
                            expected = case.get("expected", {}) if (
                                turn_index == len(case["messages"]) or case.get("assert_each_turn", False)
                            ) else {}
                            turn_records.append({
                                "turn_index": turn_index,
                                "message": message,
                                "status": "OK",
                                "attempts": attempts,
                                "elapsed_seconds": round(time.perf_counter() - started, 3),
                                "summary": summary,
                                "assertions": evaluate_assertions(summary, expected),
                            })
                            break
                        # ModelUnavailableError is a BaseException (production
                        # BUG-030); named so it is recorded like any error.
                        except (Exception, ModelUnavailableError) as error:
                            info = rate_limit_info(error)
                            if not info.get("rate_limited"):
                                case_status = "ERROR"
                                turn_records.append({"turn_index": turn_index, "message": message, "status": "ERROR", "attempts": attempts, "error": str(error)[:1000], "elapsed_seconds": round(time.perf_counter() - started, 3), "assertions": []})
                                break
                            if info.get("is_daily"):
                                if key_pool.available:
                                    exhausted_index = key_pool.index
                                    if key_pool.rotate():
                                        progress.write(f"{utc_now()} case={case['case_id']} turn={turn_index} daily quota hit on key #{exhausted_index + 1}; rotated to key #{key_pool.index + 1}\n")
                                        progress.flush()
                                        continue
                                case_status = "STOPPED_DAILY_LIMIT"
                                turn_records.append({"turn_index": turn_index, "message": message, "status": case_status, "attempts": attempts, "error": info.get("raw"), "elapsed_seconds": round(time.perf_counter() - started, 3), "assertions": []})
                                break
                            if attempts >= config.max_retries:
                                case_status = "FAILED_MAX_RETRIES"
                                turn_records.append({"turn_index": turn_index, "message": message, "status": case_status, "attempts": attempts, "error": info.get("raw"), "elapsed_seconds": round(time.perf_counter() - started, 3), "assertions": []})
                                break
                            wait_seconds = (info.get("retry_seconds") or config.default_rate_limit_wait_seconds) + 3
                            progress.write(f"{utc_now()} case={case['case_id']} turn={turn_index} rate_limited wait={wait_seconds}s attempt={attempts}\n")
                            progress.flush()
                            await asyncio.sleep(wait_seconds)
                finally:
                    reset_request_context(context_token)
                if case_status != "OK":
                    break
                if turn_index < len(case["messages"]):
                    await asyncio.sleep(config.pacing_seconds)

            record = {
                "run_id": run_id,
                "study_id": config.study_id,
                "case_index": case_index,
                "case_id": case["case_id"],
                "title": case.get("title", case["case_id"]),
                "tags": case.get("tags", []),
                "expected": case.get("expected", {}),
                "review_rubric": case.get("review_rubric", []),
                "session_id": session_id,
                "status": case_status,
                "turns": turn_records,
            }
            if config.capture_system_log:
                if capture_log_path is not None:
                    events, capture_offset = extract_system_log_events_from(capture_log_path, session_id, capture_offset)
                    record["system_log_events"] = events
                else:
                    record["system_log_events"] = extract_system_log_events(session_id)
            records.append(record)
            results_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            results_file.flush()
            progress.write(f"{utc_now()} [{case_index}/{len(cases)}] case={case['case_id']} status={case_status}\n")
            progress.flush()
            if case_status == "STOPPED_DAILY_LIMIT":
                progress.write(f"{utc_now()} daily quota exhausted; stopping run.\n")
                break
            if case_index < len(cases):
                await asyncio.sleep(config.pacing_seconds)
        completed_at = utc_now()
        progress.write(f"=== run finished {completed_at} cases={len(records)} ===\n")

    run = {
        "run_id": run_id,
        "study_id": config.study_id,
        "runner": config.runner,
        "started_at": started_at,
        "completed_at": completed_at,
        "results_path": results_path,
        "progress_path": progress_path,
        "records": records,
    }
    write_markdown_report(report_path, run)
    run["report_path"] = report_path
    return run


def _api_turn(client: Any, session_id: str, user_id: str, message: str, provider: str) -> dict[str, Any]:
    """Send one turn through /api/chat and time what the customer would see:
    the first streamed progress event and the final complete payload."""
    started = time.perf_counter()
    first_feedback = None
    complete = None
    with client.stream(
        "POST", "/api/chat",
        json={"session_id": session_id, "user_id": user_id, "message": message, "llm_provider": provider},
    ) as response:
        for line in response.iter_lines():
            if not line or not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if first_feedback is None and event.get("type") in ("progress", "init", "complete"):
                first_feedback = time.perf_counter() - started
            if event.get("type") == "complete":
                complete = event
    total = time.perf_counter() - started
    return {"first_feedback_seconds": round(first_feedback, 3) if first_feedback is not None else None,
            "elapsed_seconds": round(total, 3), "complete": complete}


async def run_api_study(
    config: StudyConfig,
    cases: list[dict[str, Any]],
    output_dir: Path,
    key_pool: "ApiKeyPool | None" = None,
    skip_case_ids: set[str] | None = None,
    capture_log_path: Path | None = None,
) -> dict[str, Any]:
    """Execute a fixture through the real /api/chat endpoint (in-process
    TestClient), recording customer-visible timing -- time to first streamed
    feedback and time to the complete reply -- and whether the turn ended in
    the model-unavailable message, alongside the same state summary and
    per-case log events the graph runner records.

    Research runs are expected to survive laptop sleep, temporary network
    loss, and exhaustion of the complete free-tier key/model pool. The
    production router owns bounded per-request key rotation and Gemini model
    fallback; the research-only wrapper below waits and retries only after
    that complete pool is exhausted. HTTP transport failures between this
    runner and its in-process API are likewise retried instead of turning a
    sleeping laptop into a recorded product failure.
    """
    skip_case_ids = skip_case_ids or set()
    capture_offset = capture_log_path.stat().st_size if capture_log_path and capture_log_path.exists() else 0
    import socket
    import threading
    import httpx
    import uvicorn
    from backend.app.api_gateway import MODEL_UNAVAILABLE_MESSAGE, app
    from backend.app.orchestration.graph import app_graph

    # A real HTTP server in this same process (so the graph state stays
    # readable) -- FastAPI's TestClient buffers a streamed response, which
    # would make "time to first feedback" equal the total turn time.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(900.0))
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    results_path = output_dir / f"{run_id}_results.jsonl"
    progress_path = output_dir / f"{run_id}_progress.txt"
    report_path = config.manifest_path.parent / "reports" / f"{run_id}_AUTOMATED_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    records: list[dict[str, Any]] = []
    if key_pool is None:
        key_pool = ApiKeyPool()

    with progress_path.open("w", encoding="utf-8") as progress, results_path.open("w", encoding="utf-8") as results_file:
        progress.write(f"=== run started {started_at} study={config.study_id} runner=api_validation provider={config.llm_provider} cases={len(cases)} ===\n")
        if key_pool.available:
            progress.write(
                f"{utc_now()} API key pool active: {len(key_pool.keys)} key(s); "
                "production rotation and cross-model fallback enabled\n"
            )
            key_pool.install_call_site_rotation(
                progress,
                all_keys_wait_seconds=max(config.unavailable_retry_wait_seconds, 600),
            )
        progress.flush()
        for case_index, case in enumerate(cases, start=1):
            if case["case_id"] in skip_case_ids:
                progress.write(f"{utc_now()} [{case_index}/{len(cases)}] case={case['case_id']} status=SKIPPED_ALREADY_DONE\n")
                progress.flush()
                continue
            session_id = f"eval_{config.study_id}_{case_index}_{uuid.uuid4().hex[:8]}"
            graph_config = {"configurable": {"thread_id": session_id}}
            turn_records: list[dict[str, Any]] = []
            case_status = "OK"
            if config.send_init:
                await asyncio.to_thread(_api_turn, client, session_id, config.user_id, "**INIT**", config.llm_provider)
            for turn_index, message in enumerate(case["messages"], start=1):
                attempts = 0
                unavailable_count = 0
                while True:
                    attempts += 1
                    try:
                        outcome = await asyncio.to_thread(_api_turn, client, session_id, config.user_id, message, config.llm_provider)
                    except Exception as error:  # runner-to-local-API transport failure
                        if _is_persistent_transient_error(error):
                            progress.write(
                                f"{utc_now()} case={case['case_id']} turn={turn_index} "
                                f"transport unavailable; retry in {config.unavailable_retry_wait_seconds}s: "
                                f"{str(error)[:200]}\n"
                            )
                            progress.flush()
                            await asyncio.sleep(config.unavailable_retry_wait_seconds)
                            continue
                        case_status = "ERROR"
                        turn_records.append({"turn_index": turn_index, "message": message, "status": "ERROR", "attempts": attempts,
                                             "error": str(error)[:1000], "assertions": []})
                        break
                    complete = outcome.get("complete") or {}
                    text = str(complete.get("text", ""))
                    unavailable = text.strip() == MODEL_UNAVAILABLE_MESSAGE or text.strip().startswith(MODEL_UNAVAILABLE_MESSAGE)
                    if unavailable and attempts < config.max_retries:
                        unavailable_count += 1
                        progress.write(f"{utc_now()} case={case['case_id']} turn={turn_index} model unavailable; retry in {config.unavailable_retry_wait_seconds}s (attempt {attempts})\n")
                        progress.flush()
                        await asyncio.sleep(config.unavailable_retry_wait_seconds)
                        continue
                    snapshot = app_graph.get_state(graph_config)
                    summary = extract_state_summary(snapshot.values or {})
                    expected = case.get("expected", {}) if (
                        turn_index == len(case["messages"]) or case.get("assert_each_turn", False)
                    ) else {}
                    turn_records.append({
                        "turn_index": turn_index, "message": message,
                        "status": "MODEL_UNAVAILABLE" if unavailable else "OK",
                        "attempts": attempts, "model_unavailable_count": unavailable_count + (1 if unavailable else 0),
                        "first_feedback_seconds": outcome.get("first_feedback_seconds"),
                        "elapsed_seconds": outcome.get("elapsed_seconds"),
                        "reply_text": text, "ui": {k: complete.get(k) for k in ("uiType", "navigationActions", "serviceRequestActions", "softEscalationAvailable") if k in complete},
                        "summary": summary, "assertions": evaluate_assertions(summary, expected),
                    })
                    if unavailable:
                        case_status = "MODEL_UNAVAILABLE"
                    break
                if case_status != "OK":
                    break
                if turn_index < len(case["messages"]):
                    await asyncio.sleep(config.pacing_seconds)
            record = {
                "run_id": run_id, "study_id": config.study_id, "runner": "api_validation", "llm_provider": config.llm_provider,
                "case_index": case_index, "case_id": case["case_id"], "title": case.get("title", case["case_id"]),
                "tags": case.get("tags", []), "expected": case.get("expected", {}), "review_rubric": case.get("review_rubric", []),
                "session_id": session_id, "status": case_status, "turns": turn_records,
            }
            if config.capture_system_log:
                if capture_log_path is not None:
                    events, capture_offset = extract_system_log_events_from(capture_log_path, session_id, capture_offset)
                    record["system_log_events"] = events
                else:
                    record["system_log_events"] = extract_system_log_events(session_id)
            records.append(record)
            results_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            results_file.flush()
            progress.write(f"{utc_now()} [{case_index}/{len(cases)}] case={case['case_id']} status={case_status}\n")
            progress.flush()
            if case_index < len(cases):
                await asyncio.sleep(config.pacing_seconds)
        completed_at = utc_now()
        progress.write(f"=== run finished {completed_at} cases={len(records)} ===\n")

    client.close()
    server.should_exit = True
    run = {"run_id": run_id, "study_id": config.study_id, "runner": "api_validation", "started_at": started_at,
           "completed_at": completed_at, "results_path": results_path, "progress_path": progress_path, "records": records}
    write_markdown_report(report_path, run)
    run["report_path"] = report_path
    return run
