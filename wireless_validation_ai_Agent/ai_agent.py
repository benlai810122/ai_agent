import sys
import yaml
import os
import json
import base64
import threading
import re
from typing import Callable
from datetime import datetime
import httpx
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
    _load_task_state,
    _save_task_state,
)
from tools.driver_install_tools.isst_driver_install_ai_agent_tools import (
    ISST_DRIVER_INSTALL_ANTHROPIC_TOOLS,
    ISST_DRIVER_INSTALL_TOOL_FUNCTIONS,
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
from anthropic import Anthropic

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
http_client = httpx.Client(verify=False)
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
    + WRT_ANTHROPIC_TOOLS
    + DEXARM_ANTHROPIC_TOOLS
    + TEAMS_ANTHROPIC_TOOLS
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
    **WRT_TOOL_FUNCTIONS,
    **DEXARM_TOOL_FUNCTIONS,
    **TEAMS_TOOL_FUNCTIONS,
}

MODEL = "claude-4-5-sonnet"

PENDING_TEST_CONFIRMATIONS = {}
PENDING_TASKS = {}  # Track ongoing tasks for reboot resumption
PENDING_TASKS_LOCK = threading.Lock()

# ── Agent Skills (progressive disclosure) ──────────────────────
# Instead of sending one giant system prompt every request, the detailed
# instructions live in skills/<name>/SKILL.md files. Only each skill's short
# name + description is always in the system prompt. The model loads the full
# body of a skill ON DEMAND by calling the load_skill tool. This keeps each
# request small and avoids hitting the model context limit.
SKILLS_DIR = os.path.join(SCRIPT_DIR, "skills")


def _parse_skill_file(file_path: str) -> dict | None:
    """Parse a SKILL.md file into {name, description, body}.

    Expected format:
        ---
        name: <skill name>
        description: <text>
        ---
        <markdown body>
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    if not content.lstrip().startswith("---"):
        return None

    # Split off the YAML frontmatter between the first two '---' fences.
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
    """Recursively scan SKILLS_DIR for all SKILL.md files and return {name: skill}."""
    skills: dict = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills

    for root, dirnames, filenames in os.walk(SKILLS_DIR):
        # Keep discovery deterministic across platforms/runs.
        dirnames.sort()
        for filename in sorted(filenames):
            if filename != "SKILL.md":
                continue
            skill_md = os.path.join(root, filename)
            parsed = _parse_skill_file(skill_md)
            if parsed:
                skills[parsed["name"]] = parsed
    return skills


# Load all available skills once at startup.
SKILLS = _discover_skills()


def _build_skill_catalog() -> str:
    """Build the always-on catalog text listing each skill name + description."""
    if not SKILLS:
        return "No skills are currently available.\n"

    lines = []
    for skill in SKILLS.values():
        # Collapse whitespace so multi-line YAML descriptions read as one line.
        desc = " ".join(skill["description"].split())
        lines.append(f"- {skill['name']}: {desc}")
    return "\n".join(lines)


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
    f"{_build_skill_catalog()}\n"
)

SYSTEM_INSTRUCTION = BASE_SYSTEM_INSTRUCTION


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


LOAD_SKILL_TOOL = {
    "name": "load_skill",
    "description": (
        "Load the full detailed instructions for one of the available skills. "
        "Call this BEFORE performing a task that matches a skill, using the exact "
        "skill name from the 'Available skills' list. Returns the skill's full "
        "step-by-step instructions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The exact skill name to load (e.g. 'bluetooth-validation').",
            }
        },
        "required": ["name"],
    },
}

# Register the skill loader alongside the existing tools.
ALL_TOOLS = ALL_TOOLS + [LOAD_SKILL_TOOL]
ALL_TOOL_FUNCTIONS["load_skill"] = load_skill

TEST_PRECHECK_SYSTEM_INSTRUCTION = (
    "You are preparing a test execution precheck. "
    "Do NOT call any tools in this step. "
    "Analyze what parts of the user's requested test can be done with currently available tools, and what parts cannot be done. "
    "When evaluating capability, if a tool can achieve the same outcome through an equivalent method, count it as supported. "
    "For example: 'turn off Bluetooth' can be done with set_bluetooth_radio_via_ui, 'reconnect headset' can be done with reconnect_bluetooth_via_ui, "
    "'play music' can be done with open_local_file, 'check audio quality' can be done with record_audio_output + analyze_audio_file. "
    "Do NOT mark a step as unsupported just because the exact UI path described in the test case is different from the tool's method. "
    "Only mark a step as unsupported if there is genuinely no tool or equivalent approach available (e.g. reboot, shutdown, physical button press). "
    "Return a concise plan using this structure: "
    "1) Planned Test Steps (numbered) — map each original test step to the tool(s) that will be used, "
    "2) Unsupported Parts (only truly impossible steps), "
    "3) Capability Match (percentage), "
    "4) Ask for confirmation to start now (yes/no). "
    "The capability percentage should reflect the number of achievable steps (including equivalent methods) divided by total requested steps."
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

SYSTEM_BLOCKS_PRECHECK = [
    SYSTEM_BASE_BLOCK,
    {"type": "text", "text": TEST_PRECHECK_SYSTEM_INSTRUCTION},
]


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


# ── Agent loop ──────────────────────────────────────────────────

def _run_agent_turn(
    messages: list,
    user_text: str,
    print_tool_logs: bool = True,
    require_test_confirmation: bool = True,
    step_callback: Callable[[str], None] | None = None,
) -> str:
    """Run one agent turn with tool handling and return final text reply."""
    session_key = id(messages)
    pending_request = PENDING_TEST_CONFIRMATIONS.get(session_key)
    confirmed_execution = False

    if pending_request:
        decision, extra_instruction = _parse_confirmation(user_text)

        if decision == "yes":
            confirmed_execution = True
            extra_clause = (
                f" Additionally: {extra_instruction}." if extra_instruction else ""
            )
            user_text = (
                f"User confirmed to proceed. Execute this test plan now: {pending_request}.{extra_clause} "
                "Use tools as needed and report each major step result."
            )
            PENDING_TEST_CONFIRMATIONS.pop(session_key, None)

        elif decision == "no":
            PENDING_TEST_CONFIRMATIONS.pop(session_key, None)
            if extra_instruction:
                # User cancelled but left a follow-up request — handle it as a fresh turn.
                cancel_note = "Test execution cancelled."
                messages.append({"role": "user", "content": user_text})
                messages.append({"role": "assistant", "content": cancel_note})
                return _run_agent_turn(
                    messages,
                    extra_instruction,
                    print_tool_logs=print_tool_logs,
                    require_test_confirmation=require_test_confirmation,
                    step_callback=step_callback,
                )
            cancel_reply = "Test execution cancelled. Let me know if you'd like to try something else."
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": cancel_reply})
            return cancel_reply

        else:
            reminder_reply = (
                "A test plan is waiting for your confirmation. "
                'Please reply **yes** to start the test (you can also add extra instructions, e.g. "yes, but also check the mic") '
                'or **no** to cancel (you can also add a new request, e.g. "no, please just check Bluetooth status").'
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
        precheck_response = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=SYSTEM_BLOCKS_PRECHECK,
            messages=messages,
        )
        precheck_reply = "".join(
            block.text for block in precheck_response.content if block.type == "text"
        )
        messages.append({"role": "assistant", "content": precheck_response.content})

        # Always show the schedule and ask for user confirmation before executing.
        PENDING_TEST_CONFIRMATIONS[session_key] = user_text
        confirmation_prompt = (
            "\n\n---\n"
            "**Ready to start?** Please reply **yes** to begin or **no** to cancel."
        )
        return precheck_reply + confirmation_prompt

    messages.append({"role": "user", "content": user_text})

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_BLOCKS,
        messages=messages,
        tools=ALL_TOOLS,
    )

    # Handle tool calls in a loop
    tool_step_index = 0
    cycle_index = 1
    while any(block.type == "tool_use" for block in response.content):
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

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
                    # Special handling for reboot_laptop - pass pending tasks
                    if fn_name == "reboot_laptop":
                        with PENDING_TASKS_LOCK:
                            fn_args_with_tasks = fn_args.copy()
                            fn_args_with_tasks["pending_tasks"] = PENDING_TASKS
                            result = fn(**fn_args_with_tasks)
                    else:
                        result = fn(**fn_args)
                except TypeError as e:
                    result = {"error": f"Invalid arguments for {fn_name}: {e}"}
            else:
                result = {"error": f"Unknown function: {fn_name}"}
            
            # Track tasks in progress (for task recovery on reboot)
            if fn_name == "report_cycle_result":
                # Extract cycle info from args
                cycle_num = fn_args.get("cycle_number")
                cycle_result = fn_args.get("result")
                if cycle_num:
                    with PENDING_TASKS_LOCK:
                        PENDING_TASKS[f"cycle_{cycle_num}"] = {
                            "cycle_number": cycle_num,
                            "status": cycle_result,
                            "timestamp": datetime.now().isoformat()
                        }

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

    reply = "".join(block.text for block in response.content if block.type == "text")
    messages.append({"role": "assistant", "content": response.content})
    return reply


def run_agent():
    """Run an interactive AI agent loop with tool use."""
    global PENDING_TASKS
    
    # Load any previously saved tasks from before a reboot
    saved_state = _load_task_state()
    if saved_state.get("status") == "success" and saved_state.get("task_count", 0) > 0:
        PENDING_TASKS = saved_state.get("tasks", {})
        print(f"\n[TASK RECOVERY] Loaded {saved_state['task_count']} pending task(s) from previous session.")
        print(f"  Saved at: {saved_state.get('saved_at')}\n")

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
            # Save pending tasks before exiting
            with PENDING_TASKS_LOCK:
                if PENDING_TASKS:
                    _save_task_state(PENDING_TASKS)
            print("Goodbye!")
            break

        reply = _run_agent_turn(messages, user_input, print_tool_logs=True)
        print(f"\nAgent: {reply}\n")


if __name__ == "__main__":
    run_agent()
