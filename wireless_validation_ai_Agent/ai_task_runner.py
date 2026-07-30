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


def _is_failure(result) -> bool:
    """A step failed if the tool reported an error or a failure status."""
    if isinstance(result, dict):
        if result.get("error"):
            return True
        status = str(result.get("status", "")).lower()
        if status in ("failure", "error", "fail", "failed"):
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
    """
    setup = script.get("setup", []) if isinstance(script, dict) else []
    steps = script.get("steps", []) if isinstance(script, dict) else []
    teardown = script.get("teardown", []) if isinstance(script, dict) else []
    task = (script.get("task") if isinstance(script, dict) else "") or "Test run"
    rounds = max(1, int(rounds or 1))
    start_time = datetime.now()
    stamp = start_time.strftime("%Y%m%d_%H%M%S")
    base_dir = script_dir or os.getcwd()
    
    # Discover report folder from script arguments FIRST (script was generated with absolute paths)
    # If found, use that folder; otherwise create a new one with current timestamp
    discovered_folder = None
    for phase in [setup, steps, teardown]:
        discovered_folder = _discover_report_folder(phase)
        if discovered_folder:
            break
    
    planned_report_folder = discovered_folder or os.path.join(base_dir, "report", f"report_{stamp}")


    _emit(
        step_callback,
        print_logs,
        f"[Runner] Executing setup ({len(setup)}) + {len(steps)} step(s) × "
        f"{rounds} round(s) + teardown ({len(teardown)}) — no LLM, no token cost.",
    )

    report_folder = None

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

    # One-time setup.
    setup_results = []
    if setup:
        _emit(step_callback, print_logs, "[Runner] ----- Setup -----")
        setup_results, folder = _execute_steps(
            setup, len(setup), tool_functions, step_callback, print_logs,
            "[Runner] Setup ", on_done=_tick,
            report_folder_hint=planned_report_folder,
        )
        report_folder = report_folder or folder

    # Repeated per-round body.
    round_results = []
    for r in range(1, rounds + 1):
        if rounds > 1:
            _emit(
                step_callback,
                print_logs,
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
        )
        report_folder = report_folder or folder
        overall = "PASS" if all(x["ok"] for x in results) else "FAIL"
        round_results.append({"round": r, "results": results, "overall": overall})
        _emit(step_callback, print_logs, f"[Runner] Round {r} — {overall}.")

    # One-time teardown.
    teardown_results = []
    if teardown:
        _emit(step_callback, print_logs, "[Runner] ----- Teardown -----")
        teardown_results, folder = _execute_steps(
            teardown, len(teardown), tool_functions, step_callback, print_logs,
            "[Runner] Teardown ", on_done=_tick,
            report_folder_hint=report_folder or planned_report_folder,
        )
        report_folder = report_folder or folder

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
        }

        content = raw_report
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
):
    """Run every step once with the given log prefix. Returns (results, report_folder)."""
    results = []
    report_folder = None
    round_path_map = round_path_map or {}

    for i, step in enumerate(steps, 1):
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
            )
            results.extend(branch_results)
            report_folder = report_folder or branch_folder

            results.append({
                "index": i,
                "function": f"[IF] {if_fn}",
                "arguments": effective_if_args,
                "result": if_result,
                "ok": not _is_failure(if_result),
                "branch": branch_label,
                "condition": condition_str,
            })
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
