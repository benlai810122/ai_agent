"""
Standalone tool-using AGENT for a self-hosted LM Studio LLM.

This script mirrors the structure and flow of ai_agent.py, but is bound to a
self-hosted LM Studio model (OpenAI-compatible "tools" / function-calling API)
instead of the Anthropic backend. It reuses the SAME shared modules as the main
agent so both stay in lockstep:

    ai_skills_loader  – skill discovery + progressive-disclosure load_skill tool
    ai_task_planner   – plan skills/tools/flow and generate a runnable JSON script
    ai_agent_backend  – gather real runtime context before the script is written
    ai_task_runner    – deterministically execute a confirmed script (no LLM cost)

Flow (identical to ai_agent.py):
    1. A test/validation request is detected.
    2. The planner picks skills + tools, outlines a one-round flow, and turns it
       into a concrete JSON test script (using real gathered runtime context).
    3. The plan + script are shown and the user is asked to confirm.
    4. On "yes" the script is executed deterministically by ai_task_runner; the
       final report is written following the report-format skill.
    Non-test messages fall through to a normal tool-using chat loop.

Server info:
    Model : google/gemma-4-12b-qat
    Host  : 10.225.74.147
    Port  : 1234 (LM Studio default)

Usage:
    python test_lmstudio_agent.py

Requirements:
    pip install requests pyyaml
"""

import os
import re
import json

import requests

# Shared modules — the SAME ones ai_agent.py uses, so the LM Studio agent and the
# Anthropic agent share one planning/scripting/running/skill implementation.
from ai_skills_loader import (
    discover_skills,
    build_skill_catalog,
    make_load_skill,
    LOAD_SKILL_TOOL as LOAD_SKILL_TOOL_ANTHROPIC,
)
from ai_task_planner import (
    build_tool_catalog,
    select_tools_for_task as _select_tools_for_task,
    generate_test_script as _generate_test_script,
    format_plan_for_reply,
)
from ai_agent_backend import (
    make_openai_script_context_gatherer,
    DEFAULT_SCRIPT_CONTEXT_TOOLS,
)
from ai_task_runner import run_test_script

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
from tools.driver_install_tools.bluetooth_driver_install_ai_agnet_tools import (
    BLUETOOTH_DRIVER_INSTALL_ANTHROPIC_TOOLS,
    BLUETOOTH_DRIVER_INSTALL_TOOL_FUNCTIONS,
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
from tools.ics_icps_tools.ics_ai_agent_tools import (
    ICS_ANTHROPIC_TOOLS,
    ICS_TOOL_FUNCTIONS,
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(SCRIPT_DIR, "skills")

# ---------------------------------------------------------------------------
# Skills (progressive disclosure) — discovered by the shared ai_skills_loader.
# Only each skill's name + description is always in the system prompt; the full
# body is loaded ON DEMAND via the load_skill tool.
# ---------------------------------------------------------------------------
SKILLS = discover_skills(SKILLS_DIR)

# Bind the discovered skills to the load_skill tool implementation.
load_skill = make_load_skill(SKILLS)

# System prompt mirrored from ai_agent.py's BASE_SYSTEM_INSTRUCTION, including the
# always-on skill catalog so the model knows to load a skill before matching tasks.
SYSTEM_PROMPT = (
    "You are a helpful AI assistant running on the user's laptop. "
    "You have access to tools that let you interact with the local system. "
    "Use the available tools whenever the user asks about system info, time, files, "
    "opening websites, opening local files, closing local media/apps, etc. "
    "Always attempt to execute every test step automatically using the available tools. "
    "Do NOT ask the user to perform steps manually unless there is truly no tool or equivalent method available. "
    "When calling tools, always provide ALL required parameters. "
    "Answer clearly and concisely. "
    f"Your program is located at: {SCRIPT_DIR}\n\n"
    "## Skills\n"
    "You have specialized skills available. Each skill contains detailed step-by-step "
    "instructions for a domain. The full instructions are NOT loaded yet — only the "
    "summaries below are. When the user's request matches a skill, FIRST call the "
    "load_skill tool with that skill's name to load its full instructions, THEN follow "
    "them. You may load multiple skills if a task spans several domains. Do not guess "
    "the detailed steps — always load the relevant skill first.\n\n"
    "Available skills:\n"
    f"{build_skill_catalog(SKILLS)}\n"
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
    + BLUETOOTH_DRIVER_INSTALL_ANTHROPIC_TOOLS
    + WRT_ANTHROPIC_TOOLS
    + DEXARM_ANTHROPIC_TOOLS
    + TEAMS_ANTHROPIC_TOOLS
    + ICS_ANTHROPIC_TOOLS
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
    **BLUETOOTH_DRIVER_INSTALL_TOOL_FUNCTIONS,
    **WRT_TOOL_FUNCTIONS,
    **DEXARM_TOOL_FUNCTIONS,
    **TEAMS_TOOL_FUNCTIONS,
    **ICS_TOOL_FUNCTIONS,
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

# Register the load_skill tool (converted from the shared Anthropic schema) so the
# agent can pull in a skill's full instructions on demand.
LOAD_SKILL_TOOL = anthropic_to_openai_tool(LOAD_SKILL_TOOL_ANTHROPIC)
TOOLS.append(LOAD_SKILL_TOOL)
TOOL_IMPLEMENTATIONS["load_skill"] = load_skill

TOOLS_BY_NAME = {t["function"]["name"]: t for t in TOOLS}


# ---------------------------------------------------------------------------
# 3. Tool-schema accessors (OpenAI/LM Studio shape) + compact catalog.
# ---------------------------------------------------------------------------
def _get_tool_name(t: dict) -> str:
    return t["function"]["name"]


def _get_tool_description(t: dict) -> str:
    return t["function"].get("description", "")


def _get_tool_params(t: dict) -> dict:
    return t["function"].get("parameters", {"type": "object", "properties": {}})


# A compact catalog (name + description only) used ONLY for the selection phase.
TOOL_CATALOG = build_tool_catalog(TOOLS, _get_tool_name, _get_tool_description)


# ---------------------------------------------------------------------------
# 4. Low-level call to the LLM
# ---------------------------------------------------------------------------
def call_llm(messages, tools=None):
    """Send the conversation + a tool subset to the LLM and return the raw message.

    `tools` is the list of tool schemas to expose for THIS call. If None, no
    tools are sent (used for the plain planning/answer phases).
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


def _plan_llm_call(prompt: str, max_tokens: int) -> str:
    """LLM call used for planning/scripting/report (single user prompt, no tools)."""
    message = call_llm([{"role": "user", "content": prompt}], tools=None)
    text = (message.get("content") or "").strip()
    if not text:
        text = (message.get("reasoning_content") or "").strip()
    return text


# ---------------------------------------------------------------------------
# 5. Planning + test-script generation (bound to the shared ai_task_planner).
# ---------------------------------------------------------------------------
def select_tools_for_task(user_goal: str) -> tuple:
    """Plan + precheck skills, tools, flow and capability (LM Studio-bound wrapper)."""
    return _select_tools_for_task(
        user_goal,
        llm_call=_plan_llm_call,
        tool_catalog=TOOL_CATALOG,
        skill_catalog=build_skill_catalog(SKILLS),
        tools_by_name=TOOLS_BY_NAME,
        valid_skill_names=SKILLS,
    )


def _skills_text(skill_names) -> str:
    """Concatenate the full instruction bodies of the given skills for the planner."""
    parts = []
    for name in skill_names or []:
        skill = SKILLS.get(name)
        if skill and skill.get("body"):
            parts.append(f"### Skill: {skill['name']}\n{skill['body']}")
    return "\n\n".join(parts)


# LM Studio-bound helper that gathers real runtime context (report folder path,
# current time, device names) before the test script is written. The loop itself
# lives in ai_agent_backend.py to keep this file focused on the agent flow.
_gather_script_context = make_openai_script_context_gatherer(
    call_llm=call_llm,
    tools=TOOLS,
    tool_functions=TOOL_IMPLEMENTATIONS,
    system_prompt=SYSTEM_PROMPT,
    tool_names=DEFAULT_SCRIPT_CONTEXT_TOOLS,
)


def generate_test_script(
    user_goal: str, selected_tools: list, flow: list, selected_skills=None
) -> tuple:
    """Turn a planned flow into a runnable JSON test script (LM Studio-bound).

    The final report-writing step (write_file) is excluded on purpose — the report
    is generated after the test finishes using the real tool results, not
    pre-scripted with placeholder content. The selected skills' full instructions
    (with their calibrated coordinates/values) are passed in so the script uses the
    exact parameters instead of guessed ones.
    """
    return _generate_test_script(
        user_goal,
        selected_tools,
        flow,
        llm_call=_plan_llm_call,
        tools_by_name=TOOLS_BY_NAME,
        get_tool_name=_get_tool_name,
        get_tool_params=_get_tool_params,
        script_dir=SCRIPT_DIR,
        exclude_step_tools={"write_file"},
        gather_context=_gather_script_context,
        skills_text=_skills_text(selected_skills),
    )


def _generate_ai_report(run_data: dict) -> str:
    """Have the AI write the final report from the run data + report-format skill.

    The test itself already ran deterministically; this is a single LLM call that
    turns the collected per-round results (and any error messages) into a polished
    report following the report-format skill. Returns the report markdown.
    """
    skill = SKILLS.get("report-format")
    skill_body = skill["body"] if skill else ""
    prompt = (
        "You are writing the FINAL test report for a test run that has already "
        "finished. Do NOT call any tools — just write the report text.\n\n"
        "Follow these report-format rules exactly:\n"
        f"{skill_body}\n\n"
        "Here is the raw run data (per-round step results and any error messages). "
        "Use it as the source of truth — do not invent results:\n"
        f"{run_data.get('raw_report', '')}\n\n"
        f"Overall result: {run_data.get('overall')} | Rounds: {run_data.get('rounds')}\n"
        f"Test start time: {run_data.get('start_time'):%Y-%m-%d %H:%M:%S}\n"
        f"Test end time: {run_data.get('end_time'):%Y-%m-%d %H:%M:%S}\n\n"
        "Write the complete final report in Markdown. Include the required sections "
        "in order (Test Item, Test Result, Summary), plus Test Start Time and Test "
        "End Time, and a Test Error Happened Time section if any step failed. "
        "Summarize the rounds and clearly call out any failures with their error "
        "messages. Output ONLY the report markdown, no extra commentary."
    )
    return _plan_llm_call(prompt, 4096)


# ---------------------------------------------------------------------------
# 6. Request classification + confirmation parsing (mirrored from ai_agent.py).
# ---------------------------------------------------------------------------
TEST_KEYWORDS = (
    "test",
    "validate",
    "validation",
    "verify",
    "verification",
    "check",
    "schedule",
)


def _is_test_request(text: str) -> bool:
    """Return True if the text contains a test/validation keyword (whole word)."""
    pattern = r"\b(" + "|".join(TEST_KEYWORDS) + r")\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def _parse_rounds(user_text: str) -> int:
    """Extract how many times to run the test. Defaults to 1 when unspecified."""
    text = (user_text or "").lower()
    m = re.search(
        r"(\d+)\s*(?:times|rounds?|iterations?|cycles?|loops?|reps?|repetitions?|passes)\b",
        text,
    )
    if m:
        return max(1, int(m.group(1)))
    m = re.search(
        r"(?:repeat|iterate|loop|run|cycle)\s+(?:it\s+|the\s+test\s+)?(\d+)",
        text,
    )
    if m:
        return max(1, int(m.group(1)))
    return 1


def _parse_confirmation(user_text: str) -> tuple:
    """Parse a user reply into (decision, extra_instruction).

    decision: 'yes' | 'no' | 'pending'
    extra_instruction: any additional context the user added after the intent word.
    """
    text = user_text.strip()
    yes_pattern = re.compile(
        r"^\s*(yes|yeah|yep|yup|sure|ok|okay|go ahead|go|start|proceed|continue|do it|let'?s go|please start)"
        r"(?:[,!.]?\s+(.*))?$",
        re.IGNORECASE | re.DOTALL,
    )
    no_pattern = re.compile(
        r"^\s*(no|nope|nah|cancel|stop|don'?t|do not|abort|skip it)"
        r"(?:[,!.]?\s+(.*))?$",
        re.IGNORECASE | re.DOTALL,
    )
    m = yes_pattern.match(text)
    if m:
        extra = (m.group(2) or "").strip()
        extra = re.sub(
            r"^(but|please|just|also|and|however|though)\s+",
            "",
            extra,
            flags=re.IGNORECASE,
        ).strip()
        return "yes", extra

    m = no_pattern.match(text)
    if m:
        extra = (m.group(2) or "").strip()
        extra = re.sub(
            r"^(but|please|just|also|and|however|though)\s+",
            "",
            extra,
            flags=re.IGNORECASE,
        ).strip()
        return "no", extra
    return "pending", ""


def _emit(step_callback, print_logs: bool, text: str) -> None:
    """Send a progress line to the console and/or the UI callback."""
    if print_logs:
        print(text, flush=True)
    if step_callback:
        try:
            step_callback(text)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 7. The agent tool-use loop: decide -> call tool -> observe -> repeat -> answer.
#    Operates on a shared `messages` list (system + history already present).
# ---------------------------------------------------------------------------
def _run_tool_loop(
    messages: list,
    selected_tools: list,
    preloaded_skills=None,
    print_tool_logs: bool = True,
    step_callback=None,
) -> str:
    """Run the decide->call->observe loop with a FIXED tool subset.

    `preloaded_skills` is a list of skill names chosen during planning; their full
    instructions are injected up front so the model doesn't need to call load_skill
    for them. Returns the final answer text.
    """
    # Track which skills were already loaded in THIS run. Small local models tend
    # to re-issue the same load_skill call repeatedly even though the instructions
    # are already in context; we short-circuit those repeats.
    loaded_skills: set = set()

    for skill_name in preloaded_skills or []:
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
            _emit(step_callback, print_tool_logs, f"[skill] Pre-loaded '{skill['name']}' from the plan.")

    for step in range(1, MAX_STEPS + 1):
        message = call_llm(messages, tools=selected_tools)
        tool_calls = message.get("tool_calls") or []

        # No tool calls -> the model is giving its final answer.
        if not tool_calls:
            final = (message.get("content") or "").strip()
            if not final:
                final = (message.get("reasoning_content") or "").strip()
            messages.append({"role": "assistant", "content": final})
            return final

        # The assistant message that contains the tool calls must be added to
        # history before we append the tool results.
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

            _emit(step_callback, print_tool_logs, f"  [Tool: {name}({args})]")

            impl = TOOL_IMPLEMENTATIONS.get(name)
            if impl is None:
                result = f"Error: unknown tool '{name}'."
            elif name == "load_skill" and args.get("name") in loaded_skills:
                # Skill already loaded this run — don't resend the full body.
                result = (
                    f"Skill '{args.get('name')}' is already loaded; its full "
                    "instructions are already in the conversation above. Do NOT "
                    "load it again — proceed with the task using those instructions."
                )
            else:
                try:
                    result = impl(**args)
                except Exception as exc:  # noqa: BLE001
                    result = f"Error running tool: {exc}"
                if (
                    name == "load_skill"
                    and isinstance(result, dict)
                    and result.get("status") == "success"
                ):
                    loaded_skills.add(result.get("name"))

            _emit(step_callback, print_tool_logs, f"  [Result: {result}]")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": str(result),
                }
            )

    reply = "[!] Reached the step limit without a final answer."
    messages.append({"role": "assistant", "content": reply})
    return reply


# ---------------------------------------------------------------------------
# 8. One agent turn: confirmation handling + plan/script + deterministic run.
#    Mirrors ai_agent._run_agent_turn, bound to the LM Studio backend.
# ---------------------------------------------------------------------------
PENDING_TEST_CONFIRMATIONS = {}
PENDING_TEST_SCRIPTS = {}  # Planned test scripts awaiting user confirmation


def _plan_and_confirm(
    messages: list,
    goal_text: str,
    session_key,
    *,
    step_callback=None,
    print_tool_logs: bool = True,
    replan_note: str | None = None,
) -> str:
    """Plan tools/skills/flow, generate a runnable script, store it as pending,
    and return the confirmation reply.

    Shared by the initial test request and by the re-planning paths (user replied
    "yes"/"no" but added extra conditions), so the script is always rebuilt the
    same way and shown for confirmation again.
    """
    if replan_note:
        _emit(step_callback, print_tool_logs, replan_note)
    _emit(
        step_callback,
        print_tool_logs,
        "[Planning] Analyzing your request and building the test plan — "
        "picking the right tools and skills… this can take a moment.",
    )
    selected_tools, selected_names, selected_skills, flow, assessment = (
        select_tools_for_task(goal_text)
    )
    _emit(
        step_callback,
        print_tool_logs,
        "[Planning] Gathering device info and generating the concrete test "
        "script with proper parameters…",
    )
    script_obj, script_path = generate_test_script(
        goal_text, selected_tools, flow, selected_skills
    )
    _emit(step_callback, print_tool_logs, "[Planning] Finalizing the test plan…")
    rounds = _parse_rounds(goal_text)
    plan_block = format_plan_for_reply(
        script_obj, script_path, selected_skills, flow, assessment, rounds
    )

    PENDING_TEST_CONFIRMATIONS[session_key] = goal_text
    PENDING_TEST_SCRIPTS[session_key] = {
        "script": script_obj,
        "path": script_path,
        "skills": selected_skills,
    }
    confirmation_prompt = (
        "\n\n---\n"
        "**Ready to start?** Please reply **yes** to begin or **no** to cancel."
    )
    reply = plan_block + confirmation_prompt
    messages.append({"role": "assistant", "content": reply})
    return reply


def _run_agent_turn(
    messages: list,
    user_text: str,
    print_tool_logs: bool = True,
    require_test_confirmation: bool = True,
    step_callback=None,
) -> str:
    """Run one agent turn with tool handling and return the final text reply."""
    session_key = id(messages)
    pending_request = PENDING_TEST_CONFIRMATIONS.get(session_key)
    confirmed_execution = False

    if pending_request:
        decision, extra_instruction = _parse_confirmation(user_text)

        if decision == "yes":
            plan = PENDING_TEST_SCRIPTS.pop(session_key, None)
            PENDING_TEST_CONFIRMATIONS.pop(session_key, None)
            script = (plan or {}).get("script") or {}
            has_runnable = bool(
                script.get("setup") or script.get("steps") or script.get("teardown")
            )

            # Case 3: "yes" WITH additional conditions — don't run the current
            # script. Re-create it so the new conditions are incorporated, then
            # ask for confirmation again.
            if extra_instruction:
                combined_goal = (
                    f"{pending_request}\n\n"
                    f"Additional conditions to incorporate: {extra_instruction}"
                )
                messages.append({"role": "user", "content": user_text})
                return _plan_and_confirm(
                    messages,
                    combined_goal,
                    session_key,
                    step_callback=step_callback,
                    print_tool_logs=print_tool_logs,
                    replan_note="[Planning] Updating the test script with your "
                    "additional conditions…",
                )

            # Case 1: plain "yes" — run the pre-planned script deterministically
            # by calling each tool function in order. No LLM, so no token cost.
            if has_runnable:
                rounds = _parse_rounds(pending_request)
                messages.append({"role": "user", "content": user_text})
                reply = run_test_script(
                    script,
                    tool_functions=TOOL_IMPLEMENTATIONS,
                    rounds=rounds,
                    step_callback=step_callback,
                    print_logs=print_tool_logs,
                    script_dir=SCRIPT_DIR,
                    report_generator=_generate_ai_report,
                )
                messages.append({"role": "assistant", "content": reply})
                return reply

            # No runnable script was produced — let the LLM agent execute.
            confirmed_execution = True
            user_text = (
                f"User confirmed to proceed. Execute this test plan now: "
                f"{pending_request}. "
                "Use tools as needed and report each major step result."
            )

        elif decision == "no":
            PENDING_TEST_CONFIRMATIONS.pop(session_key, None)
            PENDING_TEST_SCRIPTS.pop(session_key, None)

            # Case 4: "no" WITH additional conditions — the previous plan is
            # rejected. Re-create the test script from those conditions and ask
            # for confirmation again.
            if extra_instruction:
                revised_goal = (
                    f"{pending_request}\n\n"
                    "The previously planned test was rejected. Re-create it with "
                    f"these changes: {extra_instruction}"
                )
                messages.append({"role": "user", "content": user_text})
                return _plan_and_confirm(
                    messages,
                    revised_goal,
                    session_key,
                    step_callback=step_callback,
                    print_tool_logs=print_tool_logs,
                    replan_note="[Planning] Re-creating the test script based on "
                    "your new conditions…",
                )

            # Case 2: plain "no" — cancel.
            cancel_reply = "Test execution cancelled. Let me know if you'd like to try something else."
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": cancel_reply})
            return cancel_reply

        else:
            # Case 5: a reply that doesn't start with yes/no — remind the user to
            # decide whether to run the test.
            reminder_reply = (
                "A test plan is waiting for your confirmation. "
                'Please reply **yes** to start the test (you can also add extra conditions, e.g. "yes, but also check the mic", to rebuild the script) '
                'or **no** to cancel (you can also add new conditions, e.g. "no, run it 5 times instead", to rebuild the script).'
            )
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": reminder_reply})
            return reminder_reply

    if (
        require_test_confirmation
        and _is_test_request(user_text)
        and not confirmed_execution
    ):
        messages.append({"role": "user", "content": user_text})
        return _plan_and_confirm(
            messages,
            user_text,
            session_key,
            step_callback=step_callback,
            print_tool_logs=print_tool_logs,
        )

    # Normal chat / confirmed-fallback execution -> tool-using agent loop.
    messages.append({"role": "user", "content": user_text})
    preloaded = None
    if confirmed_execution:
        plan = PENDING_TEST_SCRIPTS.pop(session_key, None)
        preloaded = (plan or {}).get("skills")
    return _run_tool_loop(
        messages,
        TOOLS,
        preloaded_skills=preloaded,
        print_tool_logs=print_tool_logs,
        step_callback=step_callback,
    )


# ---------------------------------------------------------------------------
# 9. Interactive front-end (mirrors ai_agent.run_agent).
# ---------------------------------------------------------------------------
def run_agent():
    """Run an interactive AI agent loop with tool use."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("=== AI Agent (LM Studio) ===")
    print(f"Model: {MODEL} @ {BASE_URL}")
    print(f"Loaded {len(TOOLS)} tools from the tools/ folder.")
    print(f"Loaded {len(SKILLS)} skill(s): {', '.join(SKILLS.keys()) or '(none)'}")
    print(
        "Send a message containing 'test'/'validate'/'verify'/'check' to plan a test; "
        "anything else is normal chat."
    )
    print("Type 'quit' or 'exit' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        try:
            reply = _run_agent_turn(messages, user_input, print_tool_logs=True)
        except requests.exceptions.RequestException as exc:
            reply = f"[!] Request failed: {exc}"

        print(f"\nAgent: {reply}\n")


if __name__ == "__main__":
    run_agent()


# ---------------------------------------------------------------------------
# NOTE: JSON / ReAct fallback
# ---------------------------------------------------------------------------
# If the model does NOT reliably return tool_calls, switch to a prompt-based
# approach: instruct the model in the system prompt to reply with a strict JSON
# object like:
#     {"tool": "calculator", "args": {"expression": "2+2"}}
# or  {"final": "the answer"}
# then parse that JSON yourself, run the tool, and feed the result back as a
# normal user/assistant message. That avoids relying on native tool-calling.
