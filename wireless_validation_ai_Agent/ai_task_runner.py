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
        results, folder = _execute_steps(
            steps, len(steps), tool_functions, step_callback, print_logs, prefix,
            on_done=_tick,
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
        stamp = start_time.strftime("%Y%m%d_%H%M%S")
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
            content, report_folder=report_folder, stamp=stamp, script_dir=script_dir
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
    steps, total, tool_functions, step_callback, print_logs, prefix, on_done=None
):
    """Run every step once with the given log prefix. Returns (results, report_folder)."""
    results = []
    report_folder = None

    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        fname = str(step.get("function", "")).strip()
        fargs = step.get("arguments", {})
        if not isinstance(fargs, dict):
            fargs = {}

        _emit(
            step_callback,
            print_logs,
            f"{prefix}Step {i}/{total}: {fname}({json.dumps(fargs, default=str)})",
        )

        fn = tool_functions.get(fname)
        if fn is None:
            result = {"error": f"Unknown function: {fname}"}
        else:
            try:
                result = fn(**fargs)
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
                "arguments": fargs,
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
        report_file = os.path.join(folder, f"report_{stamp}.md")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(content)
        return report_file
    except OSError:
        return None
