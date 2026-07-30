"""Shared task planning + test-script generation.

Backend-agnostic: the LLM call and the tool-schema accessors are injected by the
caller, so both the Anthropic agent (ai_agent.py) and the LM Studio agent
(test_lmstudio_agent.py) can reuse the same planning/scripting logic even though
they use different LLM backends and tool-schema shapes.

During the precheck/planning phase this module can:
  1. PLAN which skills/tools a test needs and outline a step-by-step flow
     (select_tools_for_task), and
  2. turn that flow into a concrete, runnable JSON test script — a tool call with
     proper parameters for every step (generate_test_script).

Injected dependencies:
  - llm_call(prompt: str, max_tokens: int) -> str
        Send a single user prompt to the model and return its text reply.
  - get_tool_name(tool) -> str / get_tool_description(tool) -> str /
    get_tool_params(tool) -> dict
        Accessors that read a field out of ONE tool schema (shape differs per
        backend: Anthropic uses top-level keys, OpenAI/LM Studio nests under
        "function").
"""

import os
import re
import json
from datetime import datetime


def build_tool_catalog(tools, get_tool_name, get_tool_description) -> str:
    """Compact catalog listing each tool name + description (no param schema)."""
    return "\n".join(
        f"- {get_tool_name(t)}: {get_tool_description(t)}" for t in tools
    )


def build_tool_param_catalog(selected_tools, get_tool_name, get_tool_params) -> str:
    """List each selected tool with its full JSON parameter schema."""
    lines = []
    for t in selected_tools:
        name = get_tool_name(t)
        params = get_tool_params(t)
        lines.append(f"- {name}: {json.dumps(params)}")
    return "\n".join(lines)


def flow_to_text(flow: list) -> str:
    """Render the planned flow as a readable numbered list for a prompt."""
    if not flow:
        return "(no flow provided)"
    lines = []
    for i, item in enumerate(flow, 1):
        step = item.get("step", "")
        step_tools = item.get("tools") or []
        tools_str = ", ".join(step_tools) if step_tools else "(no tools)"
        lines.append(f"{i}. {step} [tools: {tools_str}]")
    return "\n".join(lines)


def select_tools_for_task(
    user_goal: str,
    *,
    llm_call,
    tool_catalog: str,
    skill_catalog: str,
    tools_by_name: dict,
    valid_skill_names,
) -> tuple:
    """Plan + pre-check a test task in ONE call.

    Chooses skills + tools, outlines a step-by-step flow, AND assesses how much
    of the task is achievable. No tool is executed. Returns
    (selected_schemas, selected_names, selected_skills, flow_steps, assessment)
    where assessment is {"capability_percent", "unsupported", "summary"}.
    Falls back to ALL tools / no skills if the reply can't be parsed.
    """
    planning_prompt = (
        "You are PLANNING and PRE-CHECKING how to complete a test task. Do NOT "
        "execute anything — only plan.\n\n"
        "Available tools (name: description):\n\n"
        f"{tool_catalog}\n\n"
        "Available skills (name: description). A skill holds detailed step-by-step "
        "instructions for a domain:\n\n"
        f"{skill_catalog}\n\n"
        f'Task: "{user_goal}"\n\n'
        "When assessing capability, if a tool can achieve the same outcome through "
        "an equivalent method, count it as SUPPORTED. Only mark a step UNSUPPORTED "
        "if there is genuinely no tool or equivalent approach available (e.g. "
        "reboot, shutdown, physical button press).\n\n"
        "Plan only ONE round/iteration of the test. If the task asks to repeat the "
        "test N times, do NOT expand or duplicate steps per round — the flow is "
        "executed multiple times automatically. Do NOT include per-round reporting "
        "steps (e.g. report_cycle_result).\n\n"
        "Respond with ONLY a JSON object with exactly these keys:\n"
        '  "skills": array of the exact skill names to load for this task,\n'
        '  "tools": array of the exact tool names you will need,\n'
        '  "flow": array of steps for ONE round; each step is an object with "step" '
        '(a short description) and "tools" (array of the exact tool names used in '
        "THAT step),\n"
        '  "unsupported": array of short descriptions of steps that truly cannot be '
        "done (empty array if none),\n"
        '  "capability_percent": integer 0-100 = achievable steps / total steps * 100,\n'
        '  "summary": a short plain-text summary of the plan and what is or is not '
        "supported.\n"
        "Do NOT call or run any tools. Output ONLY the JSON object, no extra text."
    )

    text = (llm_call(planning_prompt, 2000) or "").strip()

    tool_names, skill_names, flow = [], [], []
    assessment = {"capability_percent": None, "unsupported": [], "summary": ""}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                tool_names = [n for n in parsed.get("tools", []) if isinstance(n, str)]
                skill_names = [
                    n for n in parsed.get("skills", []) if isinstance(n, str)
                ]
                # Each flow step carries its own list of tools. Accept plain
                # strings too (older format) and normalize to {step, tools}.
                for item in parsed.get("flow", []):
                    if isinstance(item, dict):
                        step = str(item.get("step", "")).strip()
                        step_tools = [
                            t for t in item.get("tools", []) if isinstance(t, str)
                        ]
                        if step:
                            flow.append({"step": step, "tools": step_tools})
                    elif isinstance(item, str) and item.strip():
                        flow.append({"step": item.strip(), "tools": []})

                # Capability pre-check fields (merged in from the old precheck step).
                assessment["unsupported"] = [
                    str(u).strip()
                    for u in parsed.get("unsupported", [])
                    if str(u).strip()
                ]
                cap = parsed.get("capability_percent")
                if isinstance(cap, (int, float)):
                    assessment["capability_percent"] = int(cap)
                assessment["summary"] = str(parsed.get("summary", "")).strip()
        except json.JSONDecodeError:
            pass

    # Keep only names that actually exist.
    valid_tools = [n for n in tool_names if n in tools_by_name]
    valid_skills = [n for n in skill_names if n in valid_skill_names]

    # Fallback: if nothing valid was selected, expose all tools.
    if not valid_tools:
        valid_tools = list(tools_by_name.keys())
    # Always keep load_skill available so the agent can pull in more skills.
    if "load_skill" not in valid_tools:
        valid_tools.append("load_skill")

    selected = [tools_by_name[n] for n in valid_tools]
    return selected, valid_tools, valid_skills, flow, assessment


def generate_test_script(
    user_goal: str,
    selected_tools: list,
    flow: list,
    *,
    llm_call,
    tools_by_name: dict,
    get_tool_name,
    get_tool_params,
    script_dir: str,
    exclude_step_tools=None,
    gather_context=None,
    skills_text: str = "",
) -> tuple:
    """Turn the planned flow into a concrete, runnable JSON test script and save it.

    Returns (script_dict, file_path). Either may be None on failure. Script shape:
        {"task": <str>, "steps": [{"function": <tool>, "arguments": {..}}, ..]}

    exclude_step_tools: names of tools that must NOT appear in the script (e.g.
    the final report-writing tool). The final report is produced separately AFTER
    the test runs, using the real tool results, so it must not be pre-scripted
    with fabricated placeholder content.

    gather_context: optional callable gather_context(instruction: str) -> str. If
    provided, it is called FIRST so the model can run read-only/setup tools (e.g.
    get the current time, create the report folder, list device names) and return
    the REAL values. Those facts are injected into the script prompt so parameters
    like save paths use the actual report-folder path instead of guessed ones.
    """
    exclude = set(exclude_step_tools or ())
    param_catalog = build_tool_param_catalog(
        selected_tools, get_tool_name, get_tool_params
    )

    # Step 0: gather concrete runtime context (report folder path, timestamp,
    # device names, ...) by letting the caller run the necessary info tools.
    context_text = ""
    if gather_context:
        context_instruction = (
            "You are gathering the concrete runtime values needed to fill in a "
            "test script's parameters CORRECTLY. Call the necessary read-only / "
            "setup tools to obtain real values such as: the current time, the exact "
            "report folder path created for this run, and any device names or file "
            "paths the task needs. Do NOT perform the actual test actions (no "
            "playback, recording, reboots, etc.) — only gather facts.\n\n"
            f'Task: "{user_goal}"\n\n'
            "When done, list the concrete facts/values you gathered (especially the "
            "absolute report folder path and current timestamp)."
        )
        try:
            context_text = (gather_context(context_instruction) or "").strip()
        except Exception:
            context_text = ""

    context_block = ""
    if context_text:
        context_block = (
            "Known runtime context — USE these concrete values for parameters like "
            "save paths, report folder, device names, and timestamps. Build save "
            "paths from the real report folder path below; do NOT invent different "
            "paths:\n"
            f"{context_text}\n\n"
        )

    # Skill instructions hold the EXACT calibrated parameters (coordinates, press
    # depths, key layouts, target locations, device names). Feed them in so the
    # model uses real values instead of guessing.
    skills_block = ""
    if skills_text:
        skills_block = (
            "Relevant skill instructions — these contain the EXACT calibrated "
            "parameters required for this task (coordinates, press depths, key "
            "layouts, target locations, device names, file paths). You MUST copy "
            "the exact values from these instructions into the tool arguments. Do "
            "NOT invent, guess, or approximate any coordinate or value:\n"
            f"{skills_text}\n\n"
        )

    excluded_rule = ""
    if exclude:
        excluded_rule = (
            "- Do NOT include any of these tools — the final report is written "
            "separately AFTER the test finishes, using the real results: "
            f"{', '.join(sorted(exclude))}.\n"
        )
    script_prompt = (
        "You are writing a concrete, runnable test script from a plan.\n\n"
        f"Your program is located at: {script_dir}\n\n"
        f'Task: "{user_goal}"\n\n'
        f"{context_block}"
        f"{skills_block}"
        "Planned flow (each step lists the tools it uses):\n"
        f"{flow_to_text(flow)}\n\n"
        "Tools you may call, with their JSON parameter schemas:\n"
        f"{param_catalog}\n\n"
        "Produce ONLY a JSON object with this exact shape:\n"
        '{"task": "<short task description>", '
        '"setup": [{"function": "<tool>", "arguments": {"<param>": <value>}}], '
        '"steps": [{"function": "<tool>", "arguments": {"<param>": <value>}}], '
        '"teardown": [{"function": "<tool>", "arguments": {"<param>": <value>}}]}\n'
        "Rules:\n"
        "- Use ONLY the tool names listed above.\n"
        "- Plan only ONE round of the test. Do NOT repeat or duplicate steps for "
        "multiple rounds/cycles — the 'steps' list is executed multiple times "
        "automatically by the runner. Ignore any requested repeat count.\n"
        "- Put ONE-TIME preparation (get current time, create report folder, board "
        "check, connect, initial home) in 'setup'. Put the REPEATABLE test actions "
        "for ONE round (the actual clicks / moves / measurements) in 'steps'. Put "
        "ONE-TIME cleanup (disconnect, final home) in 'teardown'.\n"
        "- Do NOT include 'load_skill' — skills are only for planning.\n"
        "- Do NOT include 'report_cycle_result' or any per-round reporting step.\n"
        "- Use the EXACT parameter values from the skill instructions above "
        "(coordinates, press depths, key positions, target locations, device "
        "names). NEVER guess or approximate click/movement coordinates.\n"
        "- Do NOT add a step that writes, saves, or generates the test report; the "
        "report is created automatically once the test has finished.\n"
        f"{excluded_rule}"
        "- For save paths (screenshots, recordings, etc.), use the real report "
        "folder path from the runtime context above when available.\n"
        "- Never hardcode save paths under tools/regular_tools/report; that is a "
        "legacy location.\n"
        "- Give reasonable, concrete values for every required parameter.\n"
        "- Order the steps so they accomplish the task.\n"
        "- For conditional logic use this step format instead of a plain function step:\n"
        '  {"if": {"function": "<tool>", "arguments": {...}, "condition": "<expr>"}, '
        '"then": [<steps>], "else": [<steps>]}\n'
        "  The 'if' tool is called first; its result is evaluated against 'condition'.\n"
        "  Condition expression syntax:\n"
        "    status == success    has_output == true    connected == true\n"
        "    error                ok                    NOT error\n"
        "    A AND B              A OR B\n"
        "    devices[0].connected == true   (dot/index notation for nested fields)\n"
        "  Both 'then' and 'else' may be empty arrays [].\n"
        "  Example — reconnect only when not already connected:\n"
        '  {"if": {"function": "check_bluetooth_connection_status", '
        '"arguments": {"device_name": "Dell WL5024"}, '
        '"condition": "devices[0].connected == true"}, '
        '"then": [], '
        '"else": [{"function": "reconnect_bluetooth_via_ui", "arguments": {"device_name": "Dell WL5024"}}, '
        '{"function": "delay", "arguments": {"seconds": 5}}]}\n'
        "- Output ONLY the JSON object, with no extra text."
    )

    text = (llm_call(script_prompt, 4096) or "").strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None, None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None

    def _clean(raw_steps):
        """Keep only known tools; drop load_skill and excluded tools. Handles conditional steps."""
        cleaned = []
        for item in raw_steps if isinstance(raw_steps, list) else []:
            if not isinstance(item, dict):
                continue
            # Conditional step: {"if": {...}, "then": [...], "else": [...]}
            if "if" in item:
                if_spec = item.get("if") or {}
                if not isinstance(if_spec, dict):
                    continue
                if_fn = str(if_spec.get("function", "")).strip()
                if if_fn not in tools_by_name:
                    continue
                cleaned.append({
                    "if": {
                        "function": if_fn,
                        "arguments": if_spec.get("arguments") or {},
                        "condition": str(if_spec.get("condition", "ok")),
                    },
                    "then": _clean(item.get("then") or []),
                    "else": _clean(item.get("else") or []),
                })
                continue
            # Normal step
            fname = str(item.get("function", "")).strip()
            fargs = item.get("arguments", {})
            if not isinstance(fargs, dict):
                fargs = {}
            if fname == "load_skill":
                continue
            if fname in exclude:
                continue
            if fname in tools_by_name:
                cleaned.append({"function": fname, "arguments": fargs})
        return cleaned

    setup = _clean(parsed.get("setup", []) if isinstance(parsed, dict) else [])
    steps = _clean(parsed.get("steps", []) if isinstance(parsed, dict) else [])
    teardown = _clean(parsed.get("teardown", []) if isinstance(parsed, dict) else [])

    script_obj = {
        "task": (parsed.get("task") if isinstance(parsed, dict) else None) or user_goal,
        "setup": setup,
        "steps": steps,
        "teardown": teardown,
    }

    # Save to a timestamped JSON file under <script_dir>/test_scripts/.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    scripts_dir = os.path.join(script_dir, "test_scripts")
    file_path = os.path.join(scripts_dir, f"test_script_{stamp}.json")
    try:
        os.makedirs(scripts_dir, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(script_obj, f, indent=2)
    except OSError:
        return script_obj, None
    return script_obj, file_path


def _format_tool_call(function_name: str, arguments: dict) -> str:
    """Render a tool call as `name(arg=value, ...)` for markdown display."""
    args = arguments if isinstance(arguments, dict) else {}
    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    name = function_name or "<invalid-step>"
    return f"`{name}({arg_str})`"


def _render_steps(parts, steps, indent: str = "") -> None:
    """Append a numbered tool-step list, including conditional branches."""
    for i, step in enumerate(steps if isinstance(steps, list) else [], 1):
        if not isinstance(step, dict):
            continue

        # Conditional step format:
        # {"if": {"function", "arguments", "condition"}, "then": [...], "else": [...]}.
        if "if" in step:
            if_spec = step.get("if") or {}
            if not isinstance(if_spec, dict):
                continue
            if_fn = str(if_spec.get("function", "")).strip()
            if_args = if_spec.get("arguments") or {}
            condition = str(if_spec.get("condition", "ok")).strip() or "ok"
            then_steps = step.get("then") or []
            else_steps = step.get("else") or []

            parts.append(
                f"{indent}{i}. **IF** {_format_tool_call(if_fn, if_args)} "
                f"with condition `{condition}`\n"
            )
            parts.append(f"{indent}   - THEN ({len(then_steps)} step(s))\n")
            if then_steps:
                _render_steps(parts, then_steps, indent=f"{indent}     ")
            parts.append(f"{indent}   - ELSE ({len(else_steps)} step(s))\n")
            if else_steps:
                _render_steps(parts, else_steps, indent=f"{indent}     ")
            continue

        function_name = str(step.get("function", "")).strip()
        args = step.get("arguments", {})
        parts.append(f"{indent}{i}. {_format_tool_call(function_name, args)}\n")


def format_plan_for_reply(
    script_obj, script_path, selected_skills, flow, assessment=None, rounds=1
) -> str:
    """Build the user-facing markdown block for the precheck + flow + test script."""
    parts = []

    # Capability precheck (merged in from the old separate precheck step).
    if assessment:
        summary = (assessment.get("summary") or "").strip()
        cap = assessment.get("capability_percent")
        unsupported = assessment.get("unsupported") or []
        parts.append("## Test Precheck\n")
        if summary:
            parts.append(f"{summary}\n\n")
        if cap is not None:
            parts.append(f"**Capability match:** {cap}%\n\n")
        if unsupported:
            parts.append("**Unsupported parts:**\n")
            for u in unsupported:
                parts.append(f"- {u}\n")
            parts.append("\n")
        else:
            parts.append("**Unsupported parts:** none\n\n")

    parts.append("---\n## Planned Test Script\n")
    if selected_skills:
        parts.append(f"**Skills to load:** {', '.join(selected_skills)}\n\n")
    if flow:
        parts.append("**Test flow (one round):**\n")
        for i, item in enumerate(flow, 1):
            step = item.get("step", "")
            step_tools = item.get("tools") or []
            tools_str = ", ".join(step_tools) if step_tools else "(no tools)"
            parts.append(f"{i}. {step}  _(tools: {tools_str})_\n")
        parts.append("\n")

    setup = (script_obj or {}).get("setup") or []
    steps = (script_obj or {}).get("steps") or []
    teardown = (script_obj or {}).get("teardown") or []

    if setup or steps or teardown:
        rounds = max(1, int(rounds or 1))
        if setup:
            parts.append("**Setup (runs once):**\n")
            _render_steps(parts, setup)
            parts.append("\n")
        round_label = (
            f"**Test steps (one round — executed ×{rounds}):**\n"
            if rounds > 1
            else "**Test steps:**\n"
        )
        parts.append(round_label)
        if steps:
            _render_steps(parts, steps)
        else:
            parts.append("_(no per-round steps generated)_\n")
        parts.append("\n")
        if teardown:
            parts.append("**Teardown (runs once):**\n")
            _render_steps(parts, teardown)
            parts.append("\n")
        if rounds > 1:
            parts.append(
                f"_The **Test steps** above run {rounds} times; setup and teardown "
                "run once._\n"
            )
    else:
        parts.append("_(No concrete tool steps could be generated.)_\n")

    if script_path:
        parts.append(f"\n_Saved script: {script_path}_\n")
    return "".join(parts)
