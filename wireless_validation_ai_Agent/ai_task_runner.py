"""Deterministic test-script runner.

Once a test script has been PLANNED (by the LLM) and the user confirms it, the
steps can be executed WITHOUT the model: just call each tool function in order
with its pre-planned arguments. The AI is only used to build the script; running
it is plain Python, so this path costs no tokens.

Script shape (produced by ai_task_planner.generate_test_script):
    {"task": <str>, "steps": [{"function": <tool>, "arguments": {..}}, ...]}
"""

import os
import json
import re
from datetime import datetime

# Optional Echo MCP analysis. If the module or the Echo server is unavailable
# (e.g. off-VPN), the runner degrades gracefully and skips the analysis step.
try:
    from ai_echo_mcp_api import analyze_test_result
except Exception:  # pragma: no cover - Echo integration is optional
    analyze_test_result = None

from ai_task_state_manager import save_runner_state, clear_runner_state
from ai_flow_model import build_flow_model, flow_event, node_event, round_event

# Tool functions that reboot the machine. When one of these runs as a top-level
# step, the runner checkpoints its position first, issues the reboot, and stops;
# the run resumes deterministically after logon.
_REBOOT_FUNCS = frozenset({"reboot_laptop"})


class _RebootIssued(Exception):
    """Raised to unwind the runner after a reboot step fires, so no further
    steps run in the (about-to-die) process."""


def _short(value, limit: int = 400) -> str:
    """Compact single-line rendering of a tool result for logs."""
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _emit(step_callback, print_logs: bool, text: str) -> None:
    """Send a progress line to the console and/or the UI callback."""
    if print_logs:
        print(text, flush=True)
    if step_callback:
        try:
            step_callback(text)
        except Exception:
            pass


# Statuses that tools return to signal a successful or otherwise benign outcome.
# ANY other status (e.g. "not_found", "device_not_found", "connection_uncertain",
# "connect_button_not_found", "timeout", "module_missing", …) is treated as a
# failure so that soft failures are not silently reported as PASS.
_SUCCESS_STATUSES = frozenset({
    "success", "ok", "okay", "pass", "passed", "complete", "completed", "done",
    "skipped", "connected", "enabled", "acknowledged",
    "already_in_desired_state", "no_tasks", "warning",
})


# Functional-verdict values (reported in fields like ``overall``/``result``)
# that mean the test outcome itself failed, even when the tool ran cleanly
# (``status == "success"``). Used so a passing status can't mask a failing
# verdict (e.g. sc_status_check -> {"status": "success", "overall": "FAIL"}).
_FAILURE_VERDICTS = frozenset({"fail", "failed", "failure", "error"})


def _has_hard_error(result) -> bool:
    """True only if the tool raised/returned a hard error (has an ``error`` key)."""
    return isinstance(result, dict) and bool(result.get("error"))


def _is_failure(result) -> bool:
    """A step failed if the tool reported an error, a non-success status, or a
    functional verdict of FAIL.

    Only statuses in ``_SUCCESS_STATUSES`` are considered passing; every other
    status counts as a failure. This ensures soft-failure statuses such as
    ``not_found`` or ``device_not_found`` (e.g. a Bluetooth/audio device that
    does not exist) correctly fail the test instead of being reported as PASS.

    A tool can also execute cleanly (``status == "success"``) yet report a
    failing functional outcome via a verdict field such as ``overall``/
    ``result``/``verdict`` (e.g. ``sc_status_check`` returns
    ``{"status": "success", "overall": "FAIL"}``). Those verdicts are honored
    here so the deterministic runner (and the web UI reply it drives) agree with
    the final report instead of reporting a false PASS.
    """
    if isinstance(result, dict):
        if result.get("error"):
            return True
        status = str(result.get("status", "")).strip().lower()
        if status and status not in _SUCCESS_STATUSES:
            return True
        for key in ("overall", "verdict", "result", "outcome"):
            verdict = result.get(key)
            if isinstance(verdict, str) and verdict.strip().lower() in _FAILURE_VERDICTS:
                return True
    return False


def _get_nested(obj, key_path: str):
    """Traverse a dot-notation key path (e.g. 'devices[0].connected') into a dict/list."""
    import re as _re
    current = obj
    for part in key_path.strip().split("."):
        if current is None:
            return None
        m = _re.match(r'^(\w+)\[(\d+)\]$', part)
        if m:
            k, idx = m.group(1), int(m.group(2))
            if isinstance(current, dict):
                current = current.get(k)
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _parse_condition_value(val_str: str):
    """Parse a condition RHS string to a Python value."""
    v = val_str.strip()
    if v.lower() == "true":  return True
    if v.lower() == "false": return False
    if v.lower() in ("null", "none"): return None
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _evaluate_condition(condition: str, result: dict) -> bool:
    """Evaluate a simple condition string against a tool result dict.

    Supported syntax (case-insensitive for keywords):
      - Logical: ``A AND B``, ``A OR B``, ``NOT A``
      - Comparison: ``key == value``, ``key != value``
        key supports dot-notation and list indexing: ``devices[0].connected``
      - Shorthands: ``error`` (result has error), ``ok`` / ``success`` (no error)
      - Bare key: ``has_output``  — truthy check on that result key

    Values: ``true`` / ``false`` (bool), ``null``, numbers, quoted strings,
    unquoted strings.
    """
    cond = condition.strip()
    if not cond:
        return True

    # AND / OR split (left-to-right, AND binds tighter)
    if " AND " in cond:
        return all(_evaluate_condition(c.strip(), result) for c in cond.split(" AND "))
    if " OR " in cond:
        return any(_evaluate_condition(c.strip(), result) for c in cond.split(" OR "))
    if cond.upper().startswith("NOT "):
        return not _evaluate_condition(cond[4:].strip(), result)

    lower = cond.lower()
    if lower == "error":
        return bool(result.get("error")) if isinstance(result, dict) else False
    if lower in ("ok", "success"):
        return not _is_failure(result)

    for op in ("!=", "=="):
        if op in cond:
            lhs, _, rhs = cond.partition(op)
            actual = _get_nested(result, lhs.strip()) if isinstance(result, dict) else None
            expected = _parse_condition_value(rhs.strip())
            return (actual == expected) if op == "==" else (actual != expected)

    # Bare key — truthy check
    return bool(_get_nested(result, cond)) if isinstance(result, dict) else False


_LEGACY_REPORT_MARKERS = [
    os.path.normcase(os.path.join("tools", "regular_tools", "report")),
    os.path.normcase(os.path.join("regular_tools", "report")),
]
_ROUND_MEDIA_EXTS = {".wav", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
_RUN_REPORT_RE = re.compile(r"(.+[\\/]report[\\/]report_\d{8}_\d{6})(?:[\\/].*)?$")


def _normalize_for_lookup(path: str) -> str:
    return os.path.normcase(path.replace("/", os.sep).replace("\\", os.sep))


def _rewrite_legacy_report_path(path: str, report_folder: str) -> str:
    """Map legacy tools/regular_tools/report paths into the active run report folder."""
    normalized = path.replace("/", os.sep).replace("\\", os.sep)
    lookup = os.path.normcase(normalized)
    for marker in _LEGACY_REPORT_MARKERS:
        idx = lookup.find(marker)
        if idx >= 0:
            suffix = normalized[idx + len(marker):].lstrip("\\/")
            return os.path.join(report_folder, suffix) if suffix else report_folder
    return path



def _add_round_suffix_if_media(path: str, round_number: int | None) -> str:
    """Add _<round> suffix to media filenames so round artifacts stay unique."""
    if not round_number:
        return path
    base, ext = os.path.splitext(path)
    if ext.lower() not in _ROUND_MEDIA_EXTS:
        return path
    if re.search(r"_\d+$", os.path.basename(base)):
        return path
    return f"{base}_{round_number}{ext}"


def _extract_run_report_folder(path: str) -> str | None:
    """Return <...>/report/report_YYYYMMDD_HHMMSS when present in a path."""
    if not isinstance(path, str) or not path.strip():
        return None
    normalized = path.replace("/", os.sep).replace("\\", os.sep)
    m = _RUN_REPORT_RE.match(normalized)
    if not m:
        return None
    return os.path.abspath(m.group(1))


def _discover_report_folder(value) -> str | None:
    """Find a run report folder inside nested argument values."""
    if isinstance(value, str):
        return _extract_run_report_folder(value)
    if isinstance(value, dict):
        for v in value.values():
            found = _discover_report_folder(v)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _discover_report_folder(item)
            if found:
                return found
        return None
    return None


def _rewrite_argument_value(
    value,
    *,
    key: str,
    report_folder_hint: str | None,
    round_number: int | None,
    round_path_map: dict,
):
    """Recursively rewrite step arguments for report-folder and per-round artifacts."""
    if isinstance(value, dict):
        return {
            k: _rewrite_argument_value(
                v,
                key=str(k),
                report_folder_hint=report_folder_hint,
                round_number=round_number,
                round_path_map=round_path_map,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _rewrite_argument_value(
                item,
                key=key,
                report_folder_hint=report_folder_hint,
                round_number=round_number,
                round_path_map=round_path_map,
            )
            for item in value
        ]
    if not isinstance(value, str):
        return value

    original = value
    mapped = round_path_map.get(_normalize_for_lookup(original))
    if mapped:
        return mapped

    candidate = original
    if report_folder_hint:
        candidate = _rewrite_legacy_report_path(candidate, report_folder_hint)

    if key in {"save_path", "file_path"}:
        candidate = _add_round_suffix_if_media(candidate, round_number)

    if candidate != original:
        round_path_map[_normalize_for_lookup(original)] = candidate
    return candidate


def run_test_script(
    script,
    *,
    tool_functions,
    rounds: int = 1,
    step_callback=None,
    print_logs: bool = True,
    write_report: bool = True,
    script_dir: str | None = None,
    report_generator=None,
    echo_analyze: bool = True,
    resume_state: dict | None = None,
    script_path: str | None = None,
) -> str:
    """Execute a planned test script for one or more rounds by calling tool funcs.

    No LLM is involved in EXECUTION. Returns a markdown summary of the run.

    Args:
        script: {"task", "setup", "steps", "teardown"}. 'setup' and 'teardown'
            run once; 'steps' (one round) runs 'rounds' times. Legacy scripts with
            only 'steps' still work (setup/teardown default to empty).
        tool_functions: {name: callable} used to run each step.
        rounds: how many times to run the per-round 'steps' (defaults to 1).
        step_callback: optional sink for live progress lines (console + web UI).
        print_logs: also print progress lines to stdout.
        write_report: write a markdown report at the end.
        script_dir: base dir used for the report folder fallback.
        report_generator: optional callable report_generator(run_data: dict) -> str.
            If provided, the AI (or any writer) produces the final report content
            from the collected run data (per-round results + errors). On failure
            or empty output the deterministic report is used instead.
        echo_analyze: when True, send the raw result to the Echo MCP server for
            an independent analysis first, and pass that analysis to the report
            generator (under run_data["echo_analysis"]). Skipped gracefully if
            Echo is unavailable.
        resume_state: a checkpoint (from ai_task_state_manager.load_runner_state) used to
            continue a run that was interrupted by a reboot step. When set, the
            runner restores the report folder, completed results, and resumes from
            the saved phase/round/step.
        script_path: optional path of the saved script JSON, stored in the
            checkpoint for reference.
    """
    setup = script.get("setup", []) if isinstance(script, dict) else []
    steps = script.get("steps", []) if isinstance(script, dict) else []
    teardown = script.get("teardown", []) if isinstance(script, dict) else []
    task = (script.get("task") if isinstance(script, dict) else "") or "Test run"
    rounds = max(1, int(rounds or 1))
    base_dir = script_dir or os.getcwd()

    # Publish the flow-chart model so the Web UI panel can render it before the
    # first step runs (subsequent [Node] events drive the live highlight).
    try:
        _flow_model = build_flow_model(script, rounds)
        if _flow_model:
            _emit(step_callback, False, flow_event(_flow_model))
    except Exception:  # noqa: BLE001 - visualization must never break a run
        pass

    # Discover report folder from script arguments FIRST (script was generated with
    # absolute paths). If found, use that folder; otherwise create a new one.
    discovered_folder = None
    for phase in [setup, steps, teardown]:
        discovered_folder = _discover_report_folder(phase)
        if discovered_folder:
            break

    # ── Resume vs fresh run ──
    if resume_state:
        start_time = (
            datetime.fromisoformat(resume_state["start_time"])
            if resume_state.get("start_time")
            else datetime.now()
        )
        stamp = resume_state.get("stamp") or start_time.strftime("%Y%m%d_%H%M%S")
        rounds = int(resume_state.get("rounds", rounds) or rounds)
        report_folder = resume_state.get("report_folder")
        planned_report_folder = report_folder or discovered_folder or os.path.join(
            base_dir, "report", f"report_{stamp}"
        )
        setup_results = resume_state.get("setup_results", []) or []
        round_results = resume_state.get("round_results", []) or []
        resume_phase = resume_state.get("phase")
        resume_round = int(resume_state.get("current_round", 1) or 1)
        resume_index = int(resume_state.get("next_step_index", 0) or 0)
        resume_partial = resume_state.get("current_partial", []) or []
        _emit(
            step_callback, print_logs,
            f"[Runner] Resuming after reboot — phase='{resume_phase}', "
            f"round {resume_round}/{rounds}, next step index {resume_index}.",
        )
    else:
        clear_runner_state()  # drop any stale checkpoint from a prior run
        start_time = datetime.now()
        stamp = start_time.strftime("%Y%m%d_%H%M%S")
        report_folder = None
        planned_report_folder = discovered_folder or os.path.join(
            base_dir, "report", f"report_{stamp}"
        )
        setup_results = []
        round_results = []
        resume_phase = None
        resume_round = 1
        resume_index = 0
        resume_partial = []

    teardown_results = []

    _emit(
        step_callback,
        print_logs,
        f"[Runner] Executing setup ({len(setup)}) + {len(steps)} step(s) × "
        f"{rounds} round(s) + teardown ({len(teardown)}) — no LLM, no token cost.",
    )

    # Total step units across setup + all rounds + teardown, used to drive a
    # determinate progress bar in the UI (emitted as "[Progress] done/total").
    total_units = len(setup) + rounds * len(steps) + len(teardown)
    progress = {"done": 0, "total": max(1, total_units)}

    def _tick():
        progress["done"] += 1
        _emit(
            step_callback,
            print_logs,
            f"[Progress] {progress['done']}/{progress['total']}",
        )

    # Tracks where we are so the reboot checkpoint knows which phase/round to save.
    _cur = {"phase": resume_phase or "setup", "round": resume_round}
    _logon_scheduled = {"done": False}

    def _on_reboot(next_step_index: int, current_partial: list) -> None:
        """Persist the runner checkpoint and register auto-start before rebooting."""
        if not _logon_scheduled["done"]:
            try:
                from tools.regular_tools.power_state_ai_agent_tools import (
                    schedule_ai_agent_on_startup,
                )
                schedule_ai_agent_on_startup()
            except Exception:  # noqa: BLE001 - best effort; reboot proceeds regardless
                pass
            _logon_scheduled["done"] = True

        save_runner_state({
            "script": script,
            "script_path": script_path,
            "task": task,
            "rounds": rounds,
            "stamp": stamp,
            "report_folder": report_folder or planned_report_folder,
            "start_time": start_time.isoformat(),
            "phase": _cur["phase"],
            "current_round": _cur["round"],
            "next_step_index": next_step_index,
            "setup_results": setup_results,
            "round_results": round_results,
            "current_partial": current_partial,
        })
        _emit(
            step_callback, print_logs,
            "[Runner] Checkpoint saved. The system will reboot and the run will "
            "resume automatically after logon.",
        )

    try:
        # ── One-time setup (unless already completed before the reboot) ──
        if resume_phase in (None, "setup"):
            s_start = resume_index if resume_phase == "setup" else 0
            s_prior = resume_partial if resume_phase == "setup" else None
            if setup or s_prior:
                _cur["phase"], _cur["round"] = "setup", 1
                _emit(step_callback, print_logs, "[Runner] ----- Setup -----")
                setup_results, folder = _execute_steps(
                    setup, len(setup), tool_functions, step_callback, print_logs,
                    "[Runner] Setup ", on_done=_tick,
                    report_folder_hint=planned_report_folder,
                    on_reboot=_on_reboot, start_index=s_start, prior_results=s_prior,
                    node_base="setup",
                    project_root=base_dir,
                )
                report_folder = report_folder or folder

        # ── Repeated per-round body ──
        if resume_phase in (None, "setup", "steps"):
            _cur["phase"] = "steps"
            round_start = resume_round if resume_phase == "steps" else 1
            for r in range(round_start, rounds + 1):
                _cur["round"] = r
                _emit(step_callback, False, round_event(r, rounds))
                if resume_phase == "steps" and r == resume_round:
                    r_start, r_prior = resume_index, resume_partial
                else:
                    r_start, r_prior = 0, None
                if rounds > 1:
                    _emit(
                        step_callback, print_logs,
                        f"[Runner] ===== Round {r}/{rounds} =====",
                    )
                prefix = f"[Runner] R{r} " if rounds > 1 else "[Runner] "
                round_map = {}
                results, folder = _execute_steps(
                    steps, len(steps), tool_functions, step_callback, print_logs, prefix,
                    on_done=_tick,
                    report_folder_hint=report_folder or planned_report_folder,
                    round_number=r,
                    round_path_map=round_map,
                    on_reboot=_on_reboot, start_index=r_start, prior_results=r_prior,
                    node_base="steps",
                    project_root=base_dir,
                )
                report_folder = report_folder or folder
                round_overall = "PASS" if all(x["ok"] for x in results) else "FAIL"
                round_results.append(
                    {"round": r, "results": results, "overall": round_overall}
                )
                _emit(step_callback, print_logs, f"[Runner] Round {r} — {round_overall}.")

        # ── One-time teardown ──
        _cur["phase"] = "teardown"
        t_start = resume_index if resume_phase == "teardown" else 0
        t_prior = resume_partial if resume_phase == "teardown" else None
        if teardown or t_prior:
            _emit(step_callback, print_logs, "[Runner] ----- Teardown -----")
            teardown_results, folder = _execute_steps(
                teardown, len(teardown), tool_functions, step_callback, print_logs,
                "[Runner] Teardown ", on_done=_tick,
                report_folder_hint=report_folder or planned_report_folder,
                on_reboot=_on_reboot, start_index=t_start, prior_results=t_prior,
                node_base="teardown",
                project_root=base_dir,
            )
            report_folder = report_folder or folder
    except _RebootIssued:
        reply = (
            "## Reboot in progress\n\n"
            f"The runner reached a reboot step for **{task}** and saved a "
            "checkpoint. The system will restart and the test will resume "
            "automatically after logon — no action needed."
        )
        _emit(step_callback, print_logs, "[Runner] Reboot issued — awaiting restart.")
        return reply

    end_time = datetime.now()
    passed_rounds = sum(1 for rr in round_results if rr["overall"] == "PASS")
    setup_ok = all(x["ok"] for x in setup_results)
    teardown_ok = all(x["ok"] for x in teardown_results)
    overall = (
        "PASS"
        if (setup_ok and teardown_ok and passed_rounds == rounds)
        else "FAIL"
    )

    report_path = None
    if write_report:
        # Deterministic raw report — always built; also fed to the AI generator
        # as the source data (it already contains per-round results + errors).
        raw_report = _build_report_content(
            task,
            setup_results,
            round_results,
            teardown_results,
            overall,
            start_time,
            end_time,
            rounds,
            stamp,
        )

        # Ask Echo MCP to analyze the raw result BEFORE the AI writes the report,
        # so the report generator can use both the results and Echo's analysis.
        echo_analysis = None
        if echo_analyze and analyze_test_result is not None:
            _emit(
                step_callback,
                print_logs,
                "[Runner] Sending result to Echo MCP for analysis\u2026",
            )
            try:
                echo_analysis = analyze_test_result(raw_report)
                _emit(step_callback, print_logs, "[Runner] Echo analysis received.")
            except Exception as e:  # noqa: BLE001
                _emit(
                    step_callback,
                    print_logs,
                    f"[Runner] Echo analysis skipped ({e}).",
                )

        run_data = {
            "task": task,
            "rounds": rounds,
            "overall": overall,
            "start_time": start_time,
            "end_time": end_time,
            "report_folder": report_folder,
            "stamp": stamp,
            "setup_results": setup_results,
            "round_results": round_results,
            "teardown_results": teardown_results,
            "raw_report": raw_report,
            "echo_analysis": echo_analysis,
        }

        content = raw_report
        if echo_analysis:
            content = f"{raw_report}\n\n## Echo MCP Analysis\n\n{echo_analysis}\n"
        if report_generator:
            _emit(
                step_callback,
                print_logs,
                "[Runner] Generating final report with the AI agent…",
            )
            try:
                ai_content = report_generator(run_data)
                if ai_content and ai_content.strip():
                    content = ai_content.strip()
            except Exception as e:  # noqa: BLE001
                _emit(
                    step_callback,
                    print_logs,
                    f"[Runner] AI report generation failed ({e}); "
                    "using the deterministic report.",
                )

        report_path = _save_report(
            content,
            report_folder=report_folder or planned_report_folder,
            stamp=stamp,
            script_dir=script_dir,
        )

    # Run completed end-to-end (no pending reboot) — drop the resume checkpoint.
    clear_runner_state()

    _emit(
        step_callback,
        print_logs,
        f"[Runner] Done — {overall} ({passed_rounds}/{rounds} round(s) passed).",
    )
    return _build_reply(
        task,
        setup_results,
        round_results,
        teardown_results,
        overall,
        start_time,
        end_time,
        rounds,
        report_path,
    )


def _execute_steps(
    steps,
    total,
    tool_functions,
    step_callback,
    print_logs,
    prefix,
    on_done=None,
    report_folder_hint=None,
    round_number=None,
    round_path_map=None,
    on_reboot=None,
    start_index=0,
    prior_results=None,
    node_base=None,
    project_root=None,
):
    """Run every step once with the given log prefix. Returns (results, report_folder).

    Resume support: ``start_index`` skips the first N steps (already done before a
    reboot) and ``prior_results`` seeds the results list with those completed
    steps. ``on_reboot(next_step_index, current_partial)`` is called right before a
    top-level reboot step fires, so the runner can checkpoint and stop.

    ``node_base`` (e.g. "setup"/"steps"/"teardown") enables live ``[Node]`` events
    for the Web UI flow panel; node ids follow ``{node_base}-{i}`` and branch
    children ``{node_base}-{i}-then/-else-{j}`` (see ai_flow_model).

    A step flagged ``wrt_debug`` runs the ``wrt_log_debug`` tool (dump/copy/clear
    WRT logs into ``<report_folder>/wrt_debug``) when that step fails, then continues.
    """
    results = list(prior_results) if prior_results else []
    report_folder = None
    round_path_map = round_path_map or {}

    def _node(index, status, ok=None):
        if node_base:
            _emit(step_callback, False, node_event(
                node_base, index, status, ok=ok, round_number=round_number,
            ))

    def _maybe_wrt_debug(step, index, failed):
        """Run wrt_log_debug into the report folder when a flagged step failed."""
        if not failed or not (isinstance(step, dict) and step.get("wrt_debug")):
            return None
        wrt_fn = tool_functions.get("wrt_log_debug")
        if wrt_fn is None:
            _emit(step_callback, print_logs,
                  f"{prefix}Step {index}: WRT Debug requested but wrt_log_debug is unavailable.")
            return {"status": "error", "error": "wrt_log_debug tool unavailable"}
        base = report_folder or report_folder_hint or os.getcwd()
        wrt_dir = os.path.join(base, "wrt_debug")
        log_path = os.path.relpath(wrt_dir, project_root) if project_root else wrt_dir
        _emit(step_callback, print_logs,
              f"{prefix}Step {index} failed \u2014 running WRT Debug (logs -> {wrt_dir})\u2026")
        try:
            res = wrt_fn(log_path=log_path)
        except Exception as e:  # noqa: BLE001 - WRT collection must not abort the run
            res = {"status": "error", "error": str(e)}
        _emit(step_callback, print_logs, f"{prefix}Step {index} WRT Debug: {_short(res)}")
        return res

    for i, step in enumerate(steps, 1):
        if i <= start_index:
            continue
        if not isinstance(step, dict):
            continue

        # ── Conditional step: {"if": {"function", "arguments", "condition"}, "then": [...], "else": [...]} ──
        if "if" in step:
            if_spec = step.get("if") or {}
            if not isinstance(if_spec, dict):
                continue
            if_fn   = str(if_spec.get("function", "")).strip()
            if_args = if_spec.get("arguments") or {}
            if not isinstance(if_args, dict):
                if_args = {}
            condition_str = str(if_spec.get("condition", "ok")).strip()
            then_steps = step.get("then") or []
            else_steps = step.get("else") or []

            effective_if_args = {
                k: _rewrite_argument_value(
                    v,
                    key=str(k),
                    report_folder_hint=report_folder_hint,
                    round_number=round_number,
                    round_path_map=round_path_map,
                )
                for k, v in if_args.items()
            }

            _emit(
                step_callback, print_logs,
                f"{prefix}Step {i}/{total}: [IF] {if_fn}({json.dumps(effective_if_args, default=str)}) "
                f"condition='{condition_str}'",
            )

            _node(i, "running")
            if_fn_callable = tool_functions.get(if_fn)
            if if_fn_callable is None:
                if_result = {"error": f"Unknown function: {if_fn}"}
            else:
                try:
                    if_result = if_fn_callable(**effective_if_args)
                except TypeError as e:
                    if_result = {"error": f"Invalid arguments for {if_fn}: {e}"}
                except Exception as e:  # noqa: BLE001
                    if_result = {"error": str(e)}

            branch_taken = _evaluate_condition(condition_str, if_result)
            chosen_steps = then_steps if branch_taken else else_steps
            branch_label = "THEN" if branch_taken else "ELSE"

            _emit(
                step_callback, print_logs,
                f"{prefix}Step {i} [IF→{branch_label}]: condition='{condition_str}' "
                f"evaluated to {branch_taken}. "
                f"Running {len(chosen_steps)} branch step(s).",
            )

            branch_results, branch_folder = _execute_steps(
                chosen_steps,
                len(chosen_steps),
                tool_functions,
                step_callback,
                print_logs,
                f"{prefix}  [{branch_label}] ",
                on_done=None,       # branch sub-steps don't count in outer progress
                report_folder_hint=report_folder_hint,
                round_number=round_number,
                round_path_map=round_path_map,
                on_reboot=None,     # reboot must be a top-level step (see below)
                node_base=f"{node_base}-{i}-{branch_label.lower()}" if node_base else None,
                project_root=project_root,
            )
            results.extend(branch_results)
            report_folder = report_folder or branch_folder

            results.append({
                "index": i,
                "function": f"[IF] {if_fn}",
                "arguments": effective_if_args,
                "result": if_result,
                # A conditional CHECK only fails on a hard error; a soft status
                # like "not_found" is an expected outcome that the THEN/ELSE
                # branch is responsible for handling.
                "ok": not _has_hard_error(if_result),
                "branch": branch_label,
                "condition": condition_str,
            })
            _node(i, "done", ok=not _has_hard_error(if_result))
            wrt = _maybe_wrt_debug(step, i, _has_hard_error(if_result))
            if wrt is not None:
                results[-1]["wrt_debug"] = wrt
            if on_done:
                on_done()
            continue

        # ── Normal step ──
        fname = str(step.get("function", "")).strip()
        fargs = step.get("arguments", {})
        if not isinstance(fargs, dict):
            fargs = {}

        effective_args = {
            k: _rewrite_argument_value(
                v,
                key=str(k),
                report_folder_hint=report_folder_hint,
                round_number=round_number,
                round_path_map=round_path_map,
            )
            for k, v in fargs.items()
        }

        if not report_folder:
            discovered = _discover_report_folder(effective_args)
            if discovered:
                report_folder = discovered

        # ── Reboot step: checkpoint, issue reboot, and stop this process ──
        if fname in _REBOOT_FUNCS:
            if on_reboot is None:
                # Nested (conditional) reboot can't be resumed reliably because
                # its index is branch-relative, not top-level. Fail loudly
                # WITHOUT issuing the reboot so we don't strand a dead process.
                result = {
                    "error": "reboot must be a top-level step (not inside an if/"
                    "then/else branch) so the runner can resume after restart."
                }
                _emit(step_callback, print_logs, f"{prefix}Step {i} ERROR: {_short(result)}")
                results.append({"index": i, "function": fname,
                                "arguments": effective_args, "result": result, "ok": False})
                _node(i, "done", ok=False)
                if on_done:
                    on_done()
                continue

            reboot_marker = {
                "index": i, "function": fname, "arguments": effective_args,
                "result": {"status": "reboot_issued"}, "ok": True, "reboot": True,
            }
            _emit(
                step_callback, print_logs,
                f"{prefix}Step {i}/{total}: {fname}({json.dumps(effective_args, default=str)}) "
                "— saving checkpoint and rebooting…",
            )
            _node(i, "running")
            # Persist BEFORE issuing the reboot (in case delay_seconds is 0).
            on_reboot(i, results + [reboot_marker])
            fn = tool_functions.get(fname)
            if fn is not None:
                try:
                    fn(**effective_args)
                except Exception:  # noqa: BLE001 - process is about to restart
                    pass
            if on_done:
                on_done()
            raise _RebootIssued()

        save_path = effective_args.get("save_path")
        if isinstance(save_path, str):
            folder = os.path.dirname(save_path)
            if folder:
                try:
                    os.makedirs(folder, exist_ok=True)
                except OSError:
                    pass

        _emit(
            step_callback,
            print_logs,
            f"{prefix}Step {i}/{total}: {fname}({json.dumps(effective_args, default=str)})",
        )

        _node(i, "running")
        fn = tool_functions.get(fname)
        if fn is None:
            result = {"error": f"Unknown function: {fname}"}
        else:
            try:
                result = fn(**effective_args)
            except TypeError as e:
                result = {"error": f"Invalid arguments for {fname}: {e}"}
            except Exception as e:  # noqa: BLE001
                result = {"error": str(e)}

        errored = _is_failure(result)
        _emit(
            step_callback,
            print_logs,
            f"{prefix}Step {i} {'ERROR' if errored else 'OK'}: {_short(result)}",
        )
        _node(i, "done", ok=not errored)

        # Remember the real report folder so the report is written into it.
        if (
            fname == "create_report_folder"
            and isinstance(result, dict)
            and result.get("folder_path")
        ):
            report_folder = result["folder_path"]

        results.append(
            {
                "index": i,
                "function": fname,
                "arguments": effective_args,
                "result": result,
                "ok": not errored,
            }
        )
        wrt = _maybe_wrt_debug(step, i, errored)
        if wrt is not None:
            results[-1]["wrt_debug"] = wrt
        if on_done:
            on_done()

    return results, report_folder


def _fail_detail(r) -> str:
    """Short failure reason for a step result."""
    if r["ok"]:
        return ""
    res = r["result"] if isinstance(r["result"], dict) else {}
    err = res.get("error") or res.get("message")
    return f" — {err}" if err else ""


def _phase_summary(lines, title, results) -> None:
    """Append a one-line phase summary plus any failures."""
    if not results:
        return
    total = len(results)
    ok = sum(1 for x in results if x["ok"])
    lines.append(f"**{title}:** {ok}/{total} ok\n")
    for x in results:
        if not x["ok"]:
            lines.append(
                f"    - Step {x['index']} `{x['function']}`{_fail_detail(x)}\n"
            )
    lines.append("\n")


def _build_reply(
    task,
    setup_results,
    round_results,
    teardown_results,
    overall,
    start_time,
    end_time,
    rounds,
    report_path,
) -> str:
    """Build the markdown reply summarizing the (possibly multi-round) run."""
    passed = sum(1 for rr in round_results if rr["overall"] == "PASS")

    lines = [
        "## Test Execution (script runner)\n",
        f"**Task:** {task}\n",
        f"**Rounds:** {rounds}\n",
        f"**Overall:** {overall} ({passed}/{rounds} round(s) passed)\n\n",
    ]

    _phase_summary(lines, "Setup (once)", setup_results)

    if rounds == 1 and round_results:
        results = round_results[0]["results"]
        total = len(results)
        ok = sum(1 for r in results if r["ok"])
        lines.append(f"**Test steps:** {ok}/{total} succeeded\n")
        for r in results:
            mark = "✅" if r["ok"] else "❌"
            lines.append(f"{r['index']}. {mark} `{r['function']}`{_fail_detail(r)}\n")
        lines.append("\n")
    else:
        lines.append("**Round results:**\n")
        for rr in round_results:
            results = rr["results"]
            total = len(results)
            ok = sum(1 for x in results if x["ok"])
            mark = "✅" if rr["overall"] == "PASS" else "❌"
            lines.append(
                f"- {mark} Round {rr['round']}: {rr['overall']} ({ok}/{total} steps)\n"
            )
            if rr["overall"] != "PASS":
                for x in results:
                    if not x["ok"]:
                        lines.append(
                            f"    - Step {x['index']} `{x['function']}`{_fail_detail(x)}\n"
                        )
        lines.append("\n")

    _phase_summary(lines, "Teardown (once)", teardown_results)

    lines.append(f"**Start:** {start_time:%Y-%m-%d %H:%M:%S}\n")
    lines.append(f"**End:** {end_time:%Y-%m-%d %H:%M:%S}\n")
    if report_path:
        lines.append(f"\n_Report saved: {report_path}_\n")
    return "".join(lines)


def _build_report_content(
    task,
    setup_results,
    round_results,
    teardown_results,
    overall,
    start_time,
    end_time,
    rounds,
    stamp,
) -> str:
    """Build the deterministic markdown report content (also used as AI input)."""
    passed = sum(1 for rr in round_results if rr["overall"] == "PASS")
    failed = rounds - passed

    def _step_lines(results):
        out = []
        for x in results:
            status = "OK" if x["ok"] else "ERROR"
            out.append(
                f"- Step {x['index']} {x['function']} — {status}: "
                f"`{_short(x['result'], limit=600)}`"
            )
        return "\n".join(out)

    blocks = []
    if setup_results:
        blocks.append("### Setup (once)\n" + _step_lines(setup_results))
    for rr in round_results:
        results = rr["results"]
        total = len(results)
        ok = sum(1 for x in results if x["ok"])
        blocks.append(
            f"### Round {rr['round']} — {rr['overall']} ({ok}/{total} steps)\n"
            + _step_lines(results)
        )
    if teardown_results:
        blocks.append("### Teardown (once)\n" + _step_lines(teardown_results))
    details_block = "\n\n".join(b for b in blocks if b) or "(no steps executed)"

    return (
        f"# Test Report report_{stamp}\n\n"
        "## Test Item\n"
        f"{task}\n\n"
        f"Rounds: {rounds}\n\n"
        "## Test Result\n"
        f"Overall: {overall}\n\n"
        "## Summary\n"
        f"- Total rounds: {rounds}\n"
        f"- Passed rounds: {passed}\n"
        f"- Failed rounds: {failed}\n\n"
        "## Details\n"
        f"{details_block}\n\n"
        f"## Test Start Time\n{start_time:%Y-%m-%d %H:%M:%S}\n\n"
        f"## Test End Time\n{end_time:%Y-%m-%d %H:%M:%S}\n"
    )


def _save_report(content, *, report_folder=None, stamp=None, script_dir=None) -> str | None:
    """Write report content to <report_folder>/report_<stamp>.md. Returns path or None."""
    folder = report_folder
    if not folder:
        base = script_dir or os.getcwd()
        folder = os.path.join(base, "report", f"report_{stamp}")
    try:
        os.makedirs(folder, exist_ok=True)
        file_stamp = stamp
        folder_name = os.path.basename(os.path.normpath(folder))
        if folder_name.startswith("report_") and len(folder_name) > len("report_"):
            file_stamp = folder_name[len("report_"):]
        report_file = os.path.join(folder, f"report_{file_stamp}.md")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(content)
        return report_file
    except OSError:
        return None
