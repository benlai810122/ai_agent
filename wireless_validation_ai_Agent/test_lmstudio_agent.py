"""
Standalone minimal tool-using AGENT demo for a self-hosted LM Studio LLM.

This script is completely independent from the rest of the project. It shows
the core idea of an AI agent: the LLM is the "brain" that decides which tool
to call, sees the result, and decides the next step until it can answer.

It uses the OpenAI-compatible "tools" (function calling) API that LM Studio
exposes. If the model does not natively support tool calls, see the notes at
the bottom about the JSON/ReAct fallback approach.

Server info:
    Model : google/gemma-4-12b-qat
    Host  : 10.225.74.147
    Port  : 1234 (LM Studio default)

Usage:
    python test_lmstudio_agent.py

Requirements:
    pip install requests
"""

import sys
import os
import re
import json
from datetime import datetime

import requests
import yaml

from ai_task_planner import (
    select_tools_for_task as _plan_select_tools,
    generate_test_script as _plan_generate_script,
)

# Import all tool schemas + implementations from the existing tools/ folder.
# Each module exposes X_ANTHROPIC_TOOLS (list of Anthropic-format schemas) and
# X_TOOL_FUNCTIONS (dict of tool_name -> python callable). This mirrors the way
# ai_agent.py wires the tools together.
from tools.regular_tools.regular_ai_agent_tools import (
    ANTHROPIC_TOOLS,
    TOOL_FUNCTIONS,
)
from tools.bluetooth_tools.bluetooth_ai_agent_tools import (
    BLUETOOTH_ANTHROPIC_TOOLS,
    BLUETOOTH_TOOL_FUNCTIONS,
)
from tools.audio_headset_tools.headset_ai_agent_tools import (
    HEADSET_ANTHROPIC_TOOLS,
    HEADSET_TOOL_FUNCTIONS,
)
from tools.bluetooth_tools.bluetooth_ws_ibterverify_tools import (
    IBTERVERIFY_ANTHROPIC_TOOLS,
    IBTERVERIFY_TOOL_FUNCTIONS,
)
from tools.bluetooth_tools.bluetooth_ws_hci_tools import (
    HCITOOL_ANTHROPIC_TOOLS,
    HCITOOL_TOOL_FUNCTIONS,
)
from tools.arduino_tools.arduino_ai_agent_tools import (
    ARDUINO_ANTHROPIC_TOOLS,
    ARDUINO_TOOL_FUNCTIONS,
)
from tools.mouse_keyboard_tools.mouse_ai_Agent_tools import (
    MOUSE_KEYBOARD_ANTHROPIC_TOOLS,
    MOUSE_KEYBOARD_TOOL_FUNCTIONS,
)
from tools.regular_tools.power_state_ai_agent_tools import (
    POWER_STATE_ANTHROPIC_TOOLS,
    POWER_STATE_TOOL_FUNCTIONS,
)
from tools.driver_install_tools.isst_driver_install_ai_agent_tools import (
    ISST_DRIVER_INSTALL_ANTHROPIC_TOOLS,
    ISST_DRIVER_INSTALL_TOOL_FUNCTIONS,
)
from tools.wrt_tools.wrt_ai_agent_tools import (
    WRT_ANTHROPIC_TOOLS,
    WRT_TOOL_FUNCTIONS,
)
from tools.dexarm_tools.dexarm_ai_agent_tools import (
    DEXARM_ANTHROPIC_TOOLS,
    DEXARM_TOOL_FUNCTIONS,
)
from tools.teams_tools.teams_ai_agent_tools import (
    TEAMS_ANTHROPIC_TOOLS,
    TEAMS_TOOL_FUNCTIONS,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOST = "10.225.74.147"
PORT = 1234
MODEL = "google/gemma-4-12b-qat"

BASE_URL = f"http://{HOST}:{PORT}/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"

# (connect timeout, read timeout) in seconds. Connect is generous for VPN latency.
REQUEST_TIMEOUT = (30, 180)

MAX_STEPS = 100  # safety cap so the agent loop can never run forever


# ---------------------------------------------------------------------------
# Skills (progressive disclosure), mirrored from ai_agent.py
# Only each skill's name + description is always in the system prompt. The full
# body is loaded ON DEMAND via the load_skill tool.
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(SCRIPT_DIR, "skills")


def _parse_skill_file(file_path: str):
    """Parse a SKILL.md file into {name, description, body}."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    if not content.lstrip().startswith("---"):
        return None

    stripped = content.lstrip()
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_raw, body = parts[1], parts[2]
    try:
        meta = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError:
        return None

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    if not name:
        return None

    return {"name": name, "description": description, "body": body.strip()}


def _discover_skills() -> dict:
    """Recursively scan SKILLS_DIR for all SKILL.md files -> {name: skill}."""
    skills: dict = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for root, dirnames, filenames in os.walk(SKILLS_DIR):
        dirnames.sort()
        for filename in sorted(filenames):
            if filename != "SKILL.md":
                continue
            parsed = _parse_skill_file(os.path.join(root, filename))
            if parsed:
                skills[parsed["name"]] = parsed
    return skills


# Load all available skills once at startup.
SKILLS = _discover_skills()


def _build_skill_catalog() -> str:
    """Always-on catalog text listing each skill name + description."""
    if not SKILLS:
        return "No skills are currently available."
    lines = []
    for skill in SKILLS.values():
        desc = " ".join(skill["description"].split())
        lines.append(f"- {skill['name']}: {desc}")
    return "\n".join(lines)


def load_skill(name: str) -> dict:
    """Tool: return the full instructions (body) for a named skill."""
    skill = SKILLS.get(name)
    if not skill:
        available = ", ".join(SKILLS.keys()) or "(none)"
        return {
            "status": "error",
            "error": f"Unknown skill '{name}'. Available skills: {available}",
        }
    return {
        "status": "success",
        "name": skill["name"],
        "instructions": skill["body"],
    }


# System prompt mirrored from ai_agent.py's BASE_SYSTEM_INSTRUCTION, now including
# the Skills section so the model knows to load a skill before matching tasks.
SYSTEM_PROMPT = (
    "You are a helpful AI assistant running on the user's laptop. "
    "You have access to tools that let you interact with the local system. "
    "Use the available tools whenever the user asks about system info, time, files, "
    "opening websites, opening local files, closing local media/apps, etc. "
    "Always attempt to execute every test step automatically using the available tools. "
    "Do NOT ask the user to perform steps manually unless there is truly no tool or equivalent method available. "
    "When calling tools, always provide ALL required parameters. "
    "Answer clearly and concisely.\n\n"
    "## Skills\n"
    "You have specialized skills available. Each skill contains detailed step-by-step "
    "instructions for a domain. The full instructions are NOT loaded yet — only the "
    "summaries below are. When the user's request matches a skill, FIRST call the "
    "load_skill tool with that skill's name to load its full instructions, THEN follow "
    "them. You may load multiple skills if a task spans several domains. Do not guess "
    "the detailed steps — always load the relevant skill first.\n\n"
    "Available skills:\n"
    
)


# ---------------------------------------------------------------------------
# 1. Merge every tool from the tools/ folder
#    ALL_ANTHROPIC_TOOLS : list of Anthropic-format schemas
#    TOOL_IMPLEMENTATIONS : dict of tool_name -> python callable
# ---------------------------------------------------------------------------
ALL_ANTHROPIC_TOOLS = (
    ANTHROPIC_TOOLS
    + BLUETOOTH_ANTHROPIC_TOOLS
    + HEADSET_ANTHROPIC_TOOLS
    + IBTERVERIFY_ANTHROPIC_TOOLS
    + HCITOOL_ANTHROPIC_TOOLS
    + ARDUINO_ANTHROPIC_TOOLS
    + MOUSE_KEYBOARD_ANTHROPIC_TOOLS
    + POWER_STATE_ANTHROPIC_TOOLS
    + ISST_DRIVER_INSTALL_ANTHROPIC_TOOLS
    + WRT_ANTHROPIC_TOOLS
    + DEXARM_ANTHROPIC_TOOLS
    + TEAMS_ANTHROPIC_TOOLS
)

TOOL_IMPLEMENTATIONS = {
    **TOOL_FUNCTIONS,
    **BLUETOOTH_TOOL_FUNCTIONS,
    **HEADSET_TOOL_FUNCTIONS,
    **IBTERVERIFY_TOOL_FUNCTIONS,
    **HCITOOL_TOOL_FUNCTIONS,
    **ARDUINO_TOOL_FUNCTIONS,
    **MOUSE_KEYBOARD_TOOL_FUNCTIONS,
    **POWER_STATE_TOOL_FUNCTIONS,
    **ISST_DRIVER_INSTALL_TOOL_FUNCTIONS,
    **WRT_TOOL_FUNCTIONS,
    **DEXARM_TOOL_FUNCTIONS,
    **TEAMS_TOOL_FUNCTIONS,
}


# ---------------------------------------------------------------------------
# 2. Schema converter: Anthropic format -> OpenAI / LM Studio format
#
#    Anthropic:                         OpenAI / LM Studio:
#    {                                  {
#      "name": ...,                       "type": "function",
#      "description": ...,                "function": {
#      "input_schema": {...}                "name": ...,
#    }                                      "description": ...,
#                                           "parameters": {...}
#                                         }
#                                       }
#    The inner JSON Schema is identical; only the wrapper and the
#    "input_schema" -> "parameters" key name change.
# ---------------------------------------------------------------------------
def anthropic_to_openai_tool(anthropic_tool: dict) -> dict:
    """Convert a single Anthropic tool schema into OpenAI/LM Studio format."""
    return {
        "type": "function",
        "function": {
            "name": anthropic_tool["name"],
            "description": anthropic_tool.get("description", ""),
            "parameters": anthropic_tool.get(
                "input_schema", {"type": "object", "properties": {}}
            ),
        },
    }


# The full tool list in OpenAI/LM Studio format, plus a name -> schema lookup.
TOOLS = [anthropic_to_openai_tool(t) for t in ALL_ANTHROPIC_TOOLS]
TOOLS_BY_NAME = {t["function"]["name"]: t for t in TOOLS}

# Register the load_skill tool alongside the regular tools so the agent can pull
# in a skill's full instructions on demand.
LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": (
            "Load the full detailed instructions for one of the available skills. "
            "Call this BEFORE performing a task that matches a skill, using the exact "
            "skill name from the 'Available skills' list. Returns the skill's full "
            "step-by-step instructions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The exact skill name to load (e.g. 'bluetooth-validation').",
                }
            },
            "required": ["name"],
        },
    },
}
TOOLS.append(LOAD_SKILL_TOOL)
TOOLS_BY_NAME["load_skill"] = LOAD_SKILL_TOOL
TOOL_IMPLEMENTATIONS["load_skill"] = load_skill

# A compact catalog (name + description only) used ONLY for the selection phase.
# This is cheap to send because it omits the full parameter schemas.
TOOL_CATALOG = "\n".join(
    f"- {t['function']['name']}: {t['function']['description']}" for t in TOOLS
)


# ---------------------------------------------------------------------------
# 3. Low-level call to the LLM
# ---------------------------------------------------------------------------
def call_llm(messages, tools=None):
    """Send the conversation + a tool subset to the LLM and return the raw message.

    `tools` is the list of tool schemas to expose for THIS call. If None, no
    tools are sent (used for the plain selection/answer phases).
    """
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(CHAT_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]


# ---------------------------------------------------------------------------
# 3b. Planning phase
#     Ask the LLM (using the compact tool + skill catalogs) which skills and
#     tools this task needs, and to outline a test flow. NOTHING is executed
#     here — it only plans, and the flow is shown to the user. The generic
#     planning/scripting logic lives in task_planner.py; here we just bind it to
#     the LM Studio backend (LLM call + tool-schema accessors).
# ---------------------------------------------------------------------------
def _get_tool_name(t):
    return t["function"]["name"]


def _get_tool_description(t):
    return t["function"].get("description", "")


def _get_tool_params(t):
    return t["function"].get("parameters", {"type": "object", "properties": {}})


def _plan_llm_call(prompt: str, max_tokens: int) -> str:
    """LLM call used for planning/scripting (single user prompt, no tools)."""
    message = call_llm([{"role": "user", "content": prompt}], tools=None)
    text = (message.get("content") or "").strip()
    if not text:
        text = (message.get("reasoning_content") or "").strip()
    return text


def select_tools_for_task(user_goal: str):
    """Plan skills + tools + flow for a task (LM Studio-bound wrapper)."""
    return _plan_select_tools(
        user_goal,
        llm_call=_plan_llm_call,
        tool_catalog=TOOL_CATALOG,
        skill_catalog=_build_skill_catalog(),
        tools_by_name=TOOLS_BY_NAME,
        valid_skill_names=SKILLS,
    )


def _print_plan(selected_names: list, selected_skills: list, flow: list) -> None:
    """Show the planned skills, tools, and test flow. No tools are executed."""
    print("\n--- Planned test flow (no tools executed yet) ---")
    print(f"Skills to load : {selected_skills or '(none)'}")
    print(f"Tools to use   : {selected_names}")
    if flow:
        print("Test flow:")
        for i, item in enumerate(flow, 1):
            step = item.get("step", "")
            step_tools = item.get("tools") or []
            tools_str = ", ".join(step_tools) if step_tools else "(no tools)"
            print(f"  {i}. {step}  [tools: {tools_str}]")
    else:
        print("Test flow    : (none provided)")
    print("-------------------------------------------------")


# ---------------------------------------------------------------------------
# 3c. Test-script generation
#     Use the planned flow as a prompt and ask the LLM to turn it into a
#     concrete, runnable JSON test script: an ordered list of tool calls with
#     reasonable parameters. The script is saved as a .json file.
# ---------------------------------------------------------------------------
def generate_test_script(user_goal: str, selected_tools, flow):
    """Turn a planned flow into a runnable JSON test script (LM Studio-bound)."""
    return _plan_generate_script(
        user_goal,
        selected_tools,
        flow,
        llm_call=_plan_llm_call,
        tools_by_name=TOOLS_BY_NAME,
        get_tool_name=_get_tool_name,
        get_tool_params=_get_tool_params,
        script_dir=SCRIPT_DIR,
    )


def _print_script(script_obj) -> None:
    """Show the generated test script as a numbered list of tool calls."""
    print("\n--- Generated test script ---")
    steps = script_obj.get("steps", [])
    if not steps:
        print("  (no steps generated)")
    for i, step in enumerate(steps, 1):
        args = step.get("arguments", {})
        arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        print(f"  {i}. {step.get('function')}({arg_str})")
    print("-----------------------------")


# ---------------------------------------------------------------------------
# 4. The agent loop: decide -> call tool -> observe -> repeat -> answer
# ---------------------------------------------------------------------------
def _run_loop(user_goal: str, selected_tools, verbose: bool = True, preloaded_skills=None):
    """Run the decide->call->observe loop with a FIXED tool subset.

    `preloaded_skills` is a list of skill names chosen during planning; their
    full instructions are injected up front so the model doesn't need to call
    load_skill for them.

    Returns the final answer text (or None if the step limit was hit).
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_goal},
    ]

    # Track which skills were already loaded in THIS run. Small local models
    # tend to re-issue the same load_skill call over and over even though the
    # instructions are already in context; we short-circuit those repeats.
    loaded_skills: set = set()

    # Inject the instructions for skills chosen during planning so they are
    # already in context (no load_skill round-trip needed for them).
    for skill_name in (preloaded_skills or []):
        skill = SKILLS.get(skill_name)
        if skill and skill["name"] not in loaded_skills:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Instructions for skill '{skill['name']}':\n\n{skill['body']}"
                    ),
                }
            )
            loaded_skills.add(skill["name"])
            if verbose:
                print(f"[skill] Pre-loaded '{skill['name']}' from the plan.")

    for step in range(1, MAX_STEPS + 1):
        if verbose:
            print(f"\n--- Step {step}: asking the LLM ---")
        message = call_llm(messages, tools=selected_tools)
        tool_calls = message.get("tool_calls") or []

        # No tool calls -> the model is giving its final answer.
        if not tool_calls:
            final = (message.get("content") or "").strip()
            if not final:
                final = (message.get("reasoning_content") or "").strip()
            if verbose:
                print("\n=== Final answer ===")
                print(final or "[!] Empty response.")
            return final

        # The assistant message that contains the tool calls must be added
        # to history before we append the tool results.
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )

        # Execute each requested tool and feed the result back.
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}

            if verbose:
                print(f"[tool call] {name}({args})")

            impl = TOOL_IMPLEMENTATIONS.get(name)
            if impl is None:
                result = f"Error: unknown tool '{name}'."
            elif name == "load_skill" and args.get("name") in loaded_skills:
                # Skill already loaded this run — don't resend the full body.
                # A short reminder nudges the model to move on instead of looping.
                result = (
                    f"Skill '{args.get('name')}' is already loaded; its full "
                    "instructions are already in the conversation above. Do NOT "
                    "load it again — proceed with the task using those instructions."
                )
                if verbose:
                    print(f"[skill] '{args.get('name')}' already loaded; skipping reload.")
            else:
                try:
                    result = impl(**args)
                except Exception as exc:  # noqa: BLE001
                    result = f"Error running tool: {exc}"
                # Remember successfully loaded skills so repeats are short-circuited.
                if (
                    name == "load_skill"
                    and isinstance(result, dict)
                    and result.get("status") == "success"
                ):
                    loaded_skills.add(result.get("name"))

            if verbose:
                print(f"[tool result] {result}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": str(result),
                }
            )

    if verbose:
        print("\n[!] Reached the step limit without a final answer.")
    return None


def run_agent(user_goal: str):
    """Plan the task, generate a concrete JSON test script, then STOP.

    The test is NOT executed — planning + script generation only. Uncomment the
    _run_loop call below to actually run the flow.
    """
    print("\n--- Planning tools & flow for this task ---")
    selected_tools, selected_names, selected_skills, flow, _assessment = select_tools_for_task(user_goal)
    _print_plan(selected_names, selected_skills, flow)

    # Use the planned flow as a prompt to build a concrete JSON test script.
    script_obj, script_path = generate_test_script(user_goal, selected_tools, flow)
    if script_obj:
        _print_script(script_obj)
        if script_path:
            print(f"[script] Saved test script to: {script_path}")

    print("[plan] Flow + script created — stopping without executing the test.")
    return script_obj
    # return _run_loop(user_goal, selected_tools, preloaded_skills=selected_skills)


# Keywords that mark a request as a TEST/validation task (triggers flow planning).
TEST_KEYWORDS = (
    "test",
    "validate",
    "validation",
    "verify",
    "verification",
    "check",
)


def _is_test_request(text: str) -> bool:
    """Return True if the text contains a test/validation keyword (whole word)."""
    pattern = r"\b(" + "|".join(TEST_KEYWORDS) + r")\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def run_chat(user_goal: str):
    """Normal chatbot: answer / assist using the available tools, no test flow."""
    print("\n--- Chatting (no test flow) ---")
    return _run_loop(user_goal, TOOLS)


# ---------------------------------------------------------------------------
# 4b. Validation runner: select tools ONCE, then repeat the round N times.
# ---------------------------------------------------------------------------
def _write_validation_report(
    task: str,
    rounds: int,
    pass_count: int,
    fail_count: int,
    unknown: int,
    results: list,
    selected_names: list,
    start_time: datetime,
    end_time: datetime,
) -> str | None:
    """Write the final validation report following the report-format skill.

    Creates a run folder `report/report_YYYYMMDD_HHmmss` and a report file with
    the same name. Required contents (in order): Test Item, Test Result, Summary,
    plus Test Start Time and Test End Time.
    """
    try:
        stamp = start_time.strftime("%Y%m%d_%H%M%S")
        run_folder = os.path.join(SCRIPT_DIR, "report", f"report_{stamp}")
        os.makedirs(run_folder, exist_ok=True)
        report_file = os.path.join(run_folder, f"report_{stamp}.md")

        pass_rate = (pass_count / rounds * 100) if rounds else 0.0
        overall = "PASS" if fail_count == 0 and unknown == 0 else "FAIL"
        failed_rounds = [r for r, v, _ in results if v == "FAIL"]
        unknown_rounds = [r for r, v, _ in results if v == "UNKNOWN"]

        # Per-round verdict lines.
        round_lines = "\n".join(
            f"- Round {r}: {v}" for r, v, _ in results
        ) or "- (no rounds executed)"

        # Per-round details including the LLM's full final answer/summary.
        detail_blocks = []
        for r, v, final_text in results:
            answer = (final_text or "").strip() or "(no answer returned)"
            detail_blocks.append(
                f"### Round {r} — {v}\n{answer}"
            )
        round_details = "\n\n".join(detail_blocks) or "(no rounds executed)"

        error_line = ""
        if failed_rounds or unknown_rounds:
            error_line = (
                f"\n## Test Error Happened Time\n{end_time:%Y-%m-%d %H:%M:%S}\n"
            )

        content = (
            f"# Validation Report report_{stamp}\n\n"
            "## Test Item\n"
            f"{task}\n\n"
            f"Rounds requested: {rounds}\n"
            f"Tools used: {', '.join(selected_names)}\n\n"
            "## Test Result\n"
            f"Overall: {overall}\n\n"
            f"{round_lines}\n\n"
            "## Summary\n"
            f"- Total rounds: {rounds}\n"
            f"- PASS: {pass_count}\n"
            f"- FAIL: {fail_count}\n"
            f"- UNKNOWN: {unknown}\n"
            f"- Pass rate: {pass_rate:.1f}%\n"
            f"- Failed rounds: {failed_rounds or 'none'}\n"
            f"- Unknown rounds: {unknown_rounds or 'none'}\n\n"
            "## Round Details (LLM summaries)\n"
            f"{round_details}\n\n"
            f"## Test Start Time\n{start_time:%Y-%m-%d %H:%M:%S}\n\n"
            f"## Test End Time\n{end_time:%Y-%m-%d %H:%M:%S}\n"
            f"{error_line}"
        )

        with open(report_file, "w", encoding="utf-8") as f:
            f.write(content)
        return report_file
    except OSError as exc:
        print(f"[report] Failed to write report: {exc}")
        return None


def run_validation(task: str, rounds: int = 100):
    """Run the same validation task many times, reusing one tool selection.

    Each round asks the model to end its answer with 'RESULT: PASS' or
    'RESULT: FAIL' so we can track a pass/fail summary across all rounds.
    """
    # Plan ONCE — the round definition never changes.
    print("\n--- Planning tools & flow for the validation task (once) ---")
    selected_tools, selected_names, selected_skills, flow, _assessment = select_tools_for_task(task)
    _print_plan(selected_names, selected_skills, flow)

    # Record the overall start time for the report (report-format skill).
    start_time = datetime.now()

    # Ask the model to end each round with a clear machine-readable verdict.
    round_task = (
        f"{task}\n\n"
        "When you have finished, end your reply with a line exactly like "
        "'RESULT: PASS' if the check succeeded, or 'RESULT: FAIL' if it did not."
    )

    results = []  # list of (round_number, verdict, final_text)
    pass_count = 0
    fail_count = 0

    for r in range(1, rounds + 1):
        print(f"\n========== Round {r}/{rounds} ==========")
        final_text = _run_loop(
            round_task, selected_tools, verbose=True, preloaded_skills=selected_skills
        )

        verdict = "UNKNOWN"
        if final_text:
            m = re.search(r"RESULT:\s*(PASS|FAIL)", final_text, re.IGNORECASE)
            if m:
                verdict = m.group(1).upper()

        if verdict == "PASS":
            pass_count += 1
        elif verdict == "FAIL":
            fail_count += 1

        results.append((r, verdict, final_text or ""))
        print(f"[round {r}] verdict: {verdict}")

    # Summary
    unknown = rounds - pass_count - fail_count
    print("\n================ VALIDATION SUMMARY ================")
    print(f"Total rounds : {rounds}")
    print(f"PASS         : {pass_count}")
    print(f"FAIL         : {fail_count}")
    print(f"UNKNOWN      : {unknown}")
    if rounds:
        print(f"Pass rate    : {pass_count / rounds * 100:.1f}%")
    failed_rounds = [r for r, v, _ in results if v == "FAIL"]
    if failed_rounds:
        print(f"Failed rounds: {failed_rounds}")
    print("===================================================")

    # After all rounds finish, write the final report following the
    # report-format skill (run folder + report file, required contents).
    end_time = datetime.now()
    report_path = _write_validation_report(
        task=task,
        rounds=rounds,
        pass_count=pass_count,
        fail_count=fail_count,
        unknown=unknown,
        results=results,
        selected_names=selected_names,
        start_time=start_time,
        end_time=end_time,
    )
    if report_path:
        print(f"[report] Final report written to: {report_path}")

    return {
        "rounds": rounds,
        "pass": pass_count,
        "fail": fail_count,
        "unknown": unknown,
        "tools_used": selected_names,
        "results": results,
        "report_path": report_path,
    }


# ---------------------------------------------------------------------------
# 5. Simple interactive front-end
# ---------------------------------------------------------------------------
def main():
    print("=== LM Studio Tool-Using Agent Demo ===")
    print(f"Loaded {len(TOOLS)} tools from the tools/ folder.")
    print("Ask the agent to perform a task and it will call the matching tools.")
    print("Commands:")
    print("  <message with 'test'/'validate'/'verify'/'check'>  build a test flow")
    print("  validate <N> <task>   run <task> as a validation loop for N rounds")
    print("  <anything else>       normal AI chat")
    print("  exit / quit           leave")
    print()

    while True:
        try:
            goal = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[*] Bye!")
            break

        if not goal:
            continue
        if goal.lower() in ("exit", "quit"):
            print("[*] Bye!")
            break

        try:
            # "validate <N> <task>" -> run the validation loop N times.
            if goal.lower().startswith("validate ") and len(goal.split(maxsplit=2)) == 3 and goal.split(maxsplit=2)[1].isdigit():
                parts = goal.split(maxsplit=2)
                rounds = int(parts[1])
                task = parts[2]
                run_validation(task, rounds=rounds)
            # A test/validation keyword -> build a test flow (planning only).
            elif _is_test_request(goal):
                run_agent(goal)
            # Otherwise -> behave like a normal AI chat bot.
            else:
                run_chat(goal)
        except requests.exceptions.RequestException as exc:
            print(f"[!] Request failed: {exc}")
        print()


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# NOTE: JSON / ReAct fallback
# ---------------------------------------------------------------------------
# If gemma-4-12b-qat does NOT reliably return tool_calls, switch to a
# prompt-based approach: instruct the model in the system prompt to reply with
# a strict JSON object like:
#     {"tool": "calculator", "args": {"expression": "2+2"}}
# or  {"final": "the answer"}
# then parse that JSON yourself, run the tool, and feed the result back as a
# normal user/assistant message. That avoids relying on native tool-calling.
