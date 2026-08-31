import sys
import yaml
import os
import json
import base64
import threading
import re
from typing import Callable
from datetime import datetime
import httpx2
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
from tools.wrt_tools.wrt_ai_agent_tools import WRT_ANTHROPIC_TOOLS, WRT_TOOL_FUNCTIONS
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
from anthropic import Anthropic

from ai_skills_loader import (
    discover_skills,
    build_skill_catalog,
    make_load_skill,
    LOAD_SKILL_TOOL,
)
from ai_task_planner import (
    build_tool_catalog,
    select_tools_for_task as _select_tools_for_task,
    generate_test_script as _generate_test_script,
    format_plan_for_reply,
)
from ai_agent_backend import make_script_context_gatherer, DEFAULT_SCRIPT_CONTEXT_TOOLS
from ai_task_runner import run_test_script
from ai_flow_model import build_flow_model, flow_event, flow_clear_event

# When frozen by PyInstaller, bundled data files live in sys._MEIPASS.
# The exe itself is in os.path.dirname(sys.executable).
if getattr(sys, "frozen", False):
    SCRIPT_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load API key from YAML config
config_path = os.path.join(SCRIPT_DIR, "open_ai_key.yaml")
with open(config_path, "r") as f:
    config = yaml.safe_load(f)

base_url = "https://gnai.intel.com/api/providers/anthropic"
auth_token = config["gnai_token"]

# Initialize the Anthropic client (skip SSL verify for Intel internal proxy)
http_client = httpx2.Client(verify=False)
client = Anthropic(base_url=base_url, auth_token=auth_token, http_client=http_client)

# Merge all tools
ALL_TOOLS = (
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
ALL_TOOL_FUNCTIONS = {
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

MODEL = "claude-4-5-sonnet"

PENDING_TEST_CONFIRMATIONS = {}
PENDING_TEST_SCRIPTS = {}  # Planned test scripts awaiting user confirmation

# The step callback for the turn currently running, so deep helpers (planning,
# script generation) can route their token-usage logs to the same UI/console
# sink without threading the callback through every function signature.
_ACTIVE_STEP_CALLBACK = threading.local()


def _current_step_callback():
    return getattr(_ACTIVE_STEP_CALLBACK, "cb", None)

# ── Agent Skills (progressive disclosure) ──────────────────────
# The skill discovery/loading helpers live in skills_loader.py to keep this file
# focused on the agent loop. Only each skill's name + description is always in the
# system prompt; the full body is loaded on demand via the load_skill tool.
SKILLS_DIR = os.path.join(SCRIPT_DIR, "skills")

# Load all available skills once at startup.
SKILLS = discover_skills(SKILLS_DIR)


# Small always-on base prompt (the general Role guidance only). The detailed
# per-domain instructions are pulled in on demand via load_skill.
BASE_SYSTEM_INSTRUCTION = (
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

SYSTEM_INSTRUCTION = BASE_SYSTEM_INSTRUCTION


# Bind the discovered skills to the load_skill tool implementation.
load_skill = make_load_skill(SKILLS)

# Register the skill loader alongside the existing tools.
ALL_TOOLS = ALL_TOOLS + [LOAD_SKILL_TOOL]
ALL_TOOL_FUNCTIONS["load_skill"] = load_skill

# ── Task planning + test-script generation ─────────────────────
# The generic planning/scripting logic lives in task_planner.py so both this
# agent and test_lmstudio_agent.py can share it. Here we just bind it to the
# Anthropic backend: the LLM call and the tool-schema accessors.
ALL_TOOLS_BY_NAME = {t["name"]: t for t in ALL_TOOLS}


def _get_tool_name(tool) -> str:
    return tool["name"]


def _get_tool_description(tool) -> str:
    return tool.get("description", "")


def _get_tool_params(tool) -> dict:
    return tool.get("input_schema", {"type": "object", "properties": {}})


def get_tool_param_schema(name: str) -> dict:
    """Return a tool's JSON parameter schema by name (for the flow-panel editor)."""
    tool = ALL_TOOLS_BY_NAME.get(name)
    if not tool:
        return {"type": "object", "properties": {}}
    return tool.get("input_schema", {"type": "object", "properties": {}})


_TOOL_CATALOG = build_tool_catalog(ALL_TOOLS, _get_tool_name, _get_tool_description)


def _plan_llm_call(prompt: str, max_tokens: int) -> str:
    """LLM call used for planning/scripting (no tools, single user prompt)."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_BLOCKS,
        messages=[{"role": "user", "content": prompt}],
    )
    _track_usage(response, "planning", _current_step_callback())
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


def select_tools_for_task(user_goal: str) -> tuple:
    """Plan + precheck skills, tools, flow and capability (Anthropic-bound wrapper)."""
    return _select_tools_for_task(
        user_goal,
        llm_call=_plan_llm_call,
        tool_catalog=_TOOL_CATALOG,
        skill_catalog=build_skill_catalog(SKILLS),
        tools_by_name=ALL_TOOLS_BY_NAME,
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


def _echo_analysis_block(run_data: dict) -> str:
    """Format the Echo MCP analysis (if any) as an input section for the report."""
    analysis = run_data.get("echo_analysis")
    if not analysis or not str(analysis).strip():
        return ""
    return (
        "An independent analysis of this test result was produced by the Echo "
        "assistant. Treat it as expert input — incorporate its findings into the "
        "Summary where they add value, but the raw run data above remains the "
        "source of truth for pass/fail:\n"
        "=== ECHO ANALYSIS ===\n"
        f"{analysis}\n"
        "=== END ECHO ANALYSIS ===\n\n"
    )


def _generate_ai_report(run_data: dict) -> str:
    """Have the AI write the final report from the run data + report-format skill.

    The test itself already ran deterministically; this is a single LLM call that
    turns the collected per-round results (and any error messages) into a polished
    report following the report-format skill. Returns the report markdown.
    """
    skill = SKILLS.get("report-format")
    skill_body = skill["body"] if skill else ""
    report_folder = run_data.get("report_folder") or "(unknown)"
    prompt = (
        "You are writing the FINAL test report for a test run that has already "
        "finished. Do NOT call any tools — just write the report text.\n\n"
        "A report output folder is already prepared by the runner, and the final "
        "markdown will be saved there automatically. Do NOT create a new folder "
        "or suggest alternate save locations.\n"
        f"Existing report folder: {report_folder}\n\n"
        "Follow these report-format rules exactly:\n"
        f"{skill_body}\n\n"
        "Here is the raw run data (per-round step results and any error messages). "
        "Use it as the source of truth — do not invent results:\n"
        f"{run_data.get('raw_report', '')}\n\n"
        f"{_echo_analysis_block(run_data)}"
        f"Overall result: {run_data.get('overall')} | Rounds: {run_data.get('rounds')}\n"
        f"Test start time: {run_data.get('start_time'):%Y-%m-%d %H:%M:%S}\n"
        f"Test end time: {run_data.get('end_time'):%Y-%m-%d %H:%M:%S}\n\n"
        "Write the complete final report in Markdown. Include the required sections "
        "in order (Test Item, Test Result, Summary), plus Test Start Time and Test "
        "End Time, and a Test Error Happened Time section if any step faihihled. "
        "Summarize the rounds and clearly call out any failures with their error "
        "messages. Output ONLY the report markdown, no extra commentary."
    )
    return _plan_llm_call(prompt, 4096)


def generate_test_script(
    user_goal: str, selected_tools: list, flow: list, selected_skills=None
) -> tuple:
    """Turn a planned flow into a runnable JSON test script (Anthropic-bound).

    The final report-writing step (write_file) is excluded on purpose — the
    report is generated after the test finishes using the real tool results,
    not pre-scripted with placeholder content. The selected skills' full
    instructions (with their calibrated coordinates/values) are passed in so the
    script uses the exact parameters instead of guessed ones.
    """
    return _generate_test_script(
        user_goal,
        selected_tools,
        flow,
        llm_call=_plan_llm_call,
        tools_by_name=ALL_TOOLS_BY_NAME,
        get_tool_name=_get_tool_name,
        get_tool_params=_get_tool_params,
        script_dir=SCRIPT_DIR,
        exclude_step_tools={"write_file"},
        gather_context=_gather_script_context,
        skills_text=_skills_text(selected_skills),
    )


# ── Prompt caching ──────────────────────────────────────────────
# The system prompt and tool schemas are large and identical on every request.
# Marking them with cache_control lets the API reuse them at ~10% token cost on
# cache hits, which is the single biggest saving for long iteration tests.
#
# The base system instruction is its own cached block so it is reused across BOTH
# the precheck call and the main tool-use calls. The precheck appends a second,
# uncached block for its extra guidance.
SYSTEM_BASE_BLOCK = {
    "type": "text",
    "text": SYSTEM_INSTRUCTION,
    "cache_control": {"type": "ephemeral"},
}

SYSTEM_BLOCKS = [SYSTEM_BASE_BLOCK]


def _is_test_request(user_text: str) -> bool:
    text = user_text.lower()
    test_keywords = [
        "test",
        "validate",
        "verification",
        "verify",
        "schedule",
    ]
    return any(k in text for k in test_keywords)


def _parse_rounds(user_text: str) -> int:
    """Extract how many times to run the test. Defaults to 1 when unspecified."""
    text = (user_text or "").lower()
    # "<n> times / rounds / iterations / cycles / loops / reps / passes"
    m = re.search(
        r"(\d+)\s*(?:times|rounds?|iterations?|cycles?|loops?|reps?|repetitions?|passes)\b",
        text,
    )
    if m:
        return max(1, int(m.group(1)))
    # "repeat / iterate / loop / run / cycle <n>"
    m = re.search(
        r"(?:repeat|iterate|loop|run|cycle)\s+(?:it\s+|the\s+test\s+)?(\d+)",
        text,
    )
    if m:
        return max(1, int(m.group(1)))
    return 1


def _parse_confirmation(user_text: str) -> tuple[str, str]:
    """Parse a user reply into (decision, extra_instruction).

    decision: 'yes' | 'no' | 'pending'
    extra_instruction: any additional context the user added after the intent word,
                       e.g. "yes, but please also check the mic" -> extra="check the mic"
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


def _print_step_start(
    step_index: int,
    fn_name: str,
    fn_args: dict,
    cycle_index: int = 1,
    step_callback: Callable[[str], None] | None = None,
) -> None:
    """Print a clear step-start marker before each test/tool action."""
    if fn_name == "report_cycle_result":
        cycle = fn_args.get("cycle_number", cycle_index)
        result = fn_args.get("result", "").upper()
        summary = fn_args.get("summary", "")
        step_text = (
            f"{'='*60}\n  CYCLE {cycle} COMPLETE — {result}\n  {summary}\n{'='*60}"
        )
    else:
        step_text = f"Cycle {cycle_index} | Step {step_index}: {fn_name} | args={json.dumps(fn_args, default=str)}"
    print(step_text, flush=True)
    if step_callback:
        try:
            step_callback(step_text)
        except Exception:
            pass


# ── Token usage / cost tracking ─────────────────────────────────
# Approximate per-token USD prices for the model (Claude Sonnet tier). Adjust if
# your billing rates differ — these are only used to estimate cost.
TOKEN_PRICING = {
    "input": 3.00 / 1_000_000,       # normal input tokens
    "output": 15.00 / 1_000_000,     # output tokens
    "cache_write": 3.75 / 1_000_000,  # cache creation (write) input tokens
    "cache_read": 0.30 / 1_000_000,   # cache read (hit) input tokens
}

# Running total across the whole process so the user sees cumulative cost.
SESSION_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_write_tokens": 0,
    "cache_read_tokens": 0,
    "cost_usd": 0.0,
}


def _track_usage(
    response,
    label: str = "",
    step_callback: Callable[[str], None] | None = None,
) -> None:
    """Read token usage from an API response, accumulate it, and report it."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    cost = (
        inp * TOKEN_PRICING["input"]
        + out * TOKEN_PRICING["output"]
        + cache_write * TOKEN_PRICING["cache_write"]
        + cache_read * TOKEN_PRICING["cache_read"]
    )

    SESSION_USAGE["input_tokens"] += inp
    SESSION_USAGE["output_tokens"] += out
    SESSION_USAGE["cache_write_tokens"] += cache_write
    SESSION_USAGE["cache_read_tokens"] += cache_read
    SESSION_USAGE["cost_usd"] += cost

    tag = f" | {label}" if label else ""
    text = (
        f"[Tokens{tag}] in={inp} out={out} "
        f"cache_write={cache_write} cache_read={cache_read} "
        f"| step ≈ ${cost:.4f} | session ≈ ${SESSION_USAGE['cost_usd']:.4f}"
    )
    print(text, flush=True)
    if step_callback:
        try:
            step_callback(text)
        except Exception:
            pass


def _emit_session_summary(
    step_callback: Callable[[str], None] | None = None,
) -> str:
    """Print + report the cumulative token cost for the whole session so far."""
    summary = (
        f"[Session tokens] in={SESSION_USAGE['input_tokens']} "
        f"out={SESSION_USAGE['output_tokens']} "
        f"cache_write={SESSION_USAGE['cache_write_tokens']} "
        f"cache_read={SESSION_USAGE['cache_read_tokens']} "
        f"| total ≈ ${SESSION_USAGE['cost_usd']:.4f}"
    )
    print(summary, flush=True)
    if step_callback:
        try:
            step_callback(summary)
        except Exception:
            pass
    return summary


def _emit_thinking(
    content_blocks,
    step_callback: Callable[[str], None] | None = None,
) -> None:
    """Surface the model's intermediate reasoning/narration text blocks."""
    for block in content_blocks:
        if getattr(block, "type", None) == "text":
            thought = (block.text or "").strip()
            if not thought:
                continue
            text = f"[Thinking] {thought}"
            print(text, flush=True)
            if step_callback:
                try:
                    step_callback(text)
                except Exception:
                    pass


# Anthropic-bound helper that gathers real runtime context (report folder path,
# current time, device names) before the test script is written. The loop itself
# lives in agent_backend.py to keep this file focused on the agent loop.
_gather_script_context = make_script_context_gatherer(
    client=client,
    model=MODEL,
    system_blocks=SYSTEM_BLOCKS,
    tools=ALL_TOOLS,
    tool_functions=ALL_TOOL_FUNCTIONS,
    tool_names=DEFAULT_SCRIPT_CONTEXT_TOOLS,
    track_usage=_track_usage,
    get_step_callback=_current_step_callback,
)


# ── Agent loop ──────────────────────────────────────────────────

def _plan_and_confirm(
    messages: list,
    goal_text: str,
    session_key,
    *,
    step_callback: Callable[[str], None] | None = None,
    replan_note: str | None = None,
) -> str:
    """Plan tools/skills/flow, generate a runnable script, store it as pending,
    and return the confirmation reply.

    Shared by the initial test request and by the re-planning paths (user replied
    "yes"/"no" but added extra conditions), so the script is always rebuilt the
    same way and shown for confirmation again.
    """

    def _status(msg: str) -> None:
        print(msg, flush=True)
        if step_callback:
            try:
                step_callback(msg)
            except Exception:
                pass

    if replan_note:
        _status(replan_note)
    _status(
        "[Planning] Analyzing your request and building the test plan — "
        "picking the right tools and skills… this can take a moment."
    )
    selected_tools, selected_names, selected_skills, flow, assessment = (
        select_tools_for_task(goal_text)
    )
    _status(
        "[Planning] Gathering device info and generating the concrete test "
        "script with proper parameters…"
    )
    script_obj, script_path = generate_test_script(
        goal_text, selected_tools, flow, selected_skills
    )
    _status("[Planning] Finalizing the test plan…")
    rounds = _parse_rounds(goal_text)
    # Publish the flow-chart model so the Web UI panel can render the plan.
    if step_callback and script_obj:
        try:
            model = build_flow_model(script_obj, rounds, editable=True)
            if model:
                step_callback(flow_event(model))
        except Exception:
            pass
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
    _emit_session_summary(step_callback)
    reply = plan_block + confirmation_prompt
    messages.append({"role": "assistant", "content": reply})
    return reply


def _run_agent_turn(
    messages: list,
    user_text: str,
    print_tool_logs: bool = True,
    require_test_confirmation: bool = True,
    step_callback: Callable[[str], None] | None = None,
) -> str:
    """Run one agent turn with tool handling and return final text reply."""
    session_key = id(messages)
    # Expose this turn's callback so deep planning/scripting helpers can log their
    # token usage to the same sink (console + web UI progress feed).
    _ACTIVE_STEP_CALLBACK.cb = step_callback
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
                    tool_functions=ALL_TOOL_FUNCTIONS,
                    rounds=rounds,
                    step_callback=step_callback,
                    print_logs=print_tool_logs,
                    script_dir=SCRIPT_DIR,
                    report_generator=_generate_ai_report,
                )
                # Show the cumulative token cost for the whole session so far
                # (planning + script-context + report generation across turns).
                _emit_session_summary(step_callback)
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
                    replan_note="[Planning] Re-creating the test script based on "
                    "your new conditions…",
                )

            # Case 2: plain "no" — cancel.
            cancel_reply = "Test execution cancelled. Let me know if you'd like to try something else."
            # Clear the flow-chart panel — the planned test was cancelled.
            if step_callback:
                try:
                    step_callback(flow_clear_event())
                except Exception:
                    pass
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
        )

    messages.append({"role": "user", "content": user_text})

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_BLOCKS,
        messages=messages,
        tools=ALL_TOOLS,
    )
    _track_usage(response, "agent turn", step_callback)

    # Handle tool calls in a loop
    tool_step_index = 0
    cycle_index = 1

    while any(block.type == "tool_use" for block in response.content):
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Show the model's reasoning/narration for this step before running tools.
        _emit_thinking(assistant_content, step_callback)

        tool_results = []
        for block in assistant_content:
            if block.type != "tool_use":
                continue

            fn_name = block.name
            fn_args = block.input if block.input else {}
            tool_step_index += 1
            _print_step_start(
                tool_step_index,
                fn_name,
                fn_args,
                cycle_index=cycle_index,
                step_callback=step_callback,
            )
            if print_tool_logs:
                print(f"  [Tool: {fn_name}({fn_args})]")

            fn = ALL_TOOL_FUNCTIONS.get(fn_name)
            if fn:
                try:
                    result = fn(**fn_args)
                except TypeError as e:
                    result = {"error": f"Invalid arguments for {fn_name}: {e}"}
            else:
                result = {"error": f"Unknown function: {fn_name}"}

            # A completed cycle marker advances the cycle and restarts step numbering.
            if fn_name == "report_cycle_result":
                cycle_index += 1
                tool_step_index = 0

            # If screenshot was captured, add image content for vision analysis
            if (
                fn_name == "capture_screen"
                and isinstance(result, dict)
                and result.get("status") == "success"
            ):
                with open(result["file_path"], "rb") as img_file:
                    img_b64 = base64.b64encode(img_file.read()).decode("utf-8")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [
                            {"type": "text", "text": json.dumps(result, default=str)},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": img_b64,
                                },
                            },
                        ],
                    }
                )
            else:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_BLOCKS,
            messages=messages,
            tools=ALL_TOOLS,
        )
        _track_usage(response, "agent turn", step_callback)

    reply = "".join(block.text for block in response.content if block.type == "text")
    messages.append({"role": "assistant", "content": response.content})

    # Report the cumulative token cost for the whole session so far.
    _emit_session_summary(step_callback)

    return reply


def run_agent():
    """Run an interactive AI agent loop with tool use."""
    messages = []
    print("=== AI Agent (Anthropic) ===")
    print(
        "Skills: time, system info, laptop info, list files, read files, run commands, screen capture, bluetooth scan & connect, headset endpoint check"
    )
    print(f"Loaded {len(SKILLS)} skill(s): {', '.join(SKILLS.keys()) or '(none)'}")
    print("Type 'quit' or 'exit' to stop.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        reply = _run_agent_turn(messages, user_input, print_tool_logs=True)
        print(f"\nAgent: {reply}\n")


if __name__ == "__main__":
    run_agent()
