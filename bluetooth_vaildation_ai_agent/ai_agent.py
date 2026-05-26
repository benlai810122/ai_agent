import yaml
import os
import json
import base64
import threading
import re
from typing import Callable
from datetime import datetime
import httpx
from regular_ai_agent_tools import ANTHROPIC_TOOLS, TOOL_FUNCTIONS, set_scheduler_notifier, list_scheduled_tasks
from bluetooth_ai_agent_tools import BLUETOOTH_ANTHROPIC_TOOLS, BLUETOOTH_TOOL_FUNCTIONS
from headset_ai_agent_tools import HEADSET_ANTHROPIC_TOOLS, HEADSET_TOOL_FUNCTIONS
from anthropic import Anthropic

# Load API key from YAML config
config_path = os.path.join(os.path.dirname(__file__), "open_ai_key.yaml")
with open(config_path, "r") as f:
    config = yaml.safe_load(f)

base_url = 'https://gnai.intel.com/api/providers/anthropic'
auth_token = config['gnai_token']

# Initialize the Anthropic client (skip SSL verify for Intel internal proxy)
http_client = httpx.Client(verify=False)
client = Anthropic(base_url=base_url, auth_token=auth_token, http_client=http_client)

# Merge all tools
ALL_TOOLS = ANTHROPIC_TOOLS + BLUETOOTH_ANTHROPIC_TOOLS + HEADSET_ANTHROPIC_TOOLS
ALL_TOOL_FUNCTIONS = {**TOOL_FUNCTIONS, **BLUETOOTH_TOOL_FUNCTIONS, **HEADSET_TOOL_FUNCTIONS}

MODEL = "claude-4-5-sonnet"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULED_MESSAGES = []
SCHEDULED_MESSAGES_LOCK = threading.Lock()
LAST_AUTO_SUMMARY_EXECUTED_COUNT = 0
PENDING_TEST_CONFIRMATIONS = {}

SYSTEM_INSTRUCTION = (
    "You are a helpful AI assistant running on the user's laptop. "
    "You have access to tools that let you interact with the local system. "
    "Use the available tools whenever the user asks about system info, time, files, opening websites, opening local files, closing local media/apps, etc. "
    "For any headset or audio validation test, make sure system audio is not muted before and during playback checks. "
    "When the user asks to play music or audio, use open_local_file to open the file from the 'music' subfolder inside the project root. "
    "If no specific track is named, first call list_directory on the 'music' folder to discover available files, then open the first suitable one. "
    "When calling tools, always provide ALL required parameters. "
    "When creating files with content, use create_file with the content parameter, or use write_file with both file_path and content. "
    "When asked to create or save any test report, always save it under the 'report' folder. "
    "The report filename must follow this exact pattern: report_YYYYMMDD_HH_mm_ss. "
    "The report content must always include exactly three parts in this order: Test Item, Test Result, Summary. "
    "The report must also include Test Start Time and Test End Time. "
    "If any error happens, the report must include Test Error Happened Time. "
    "When running multi-step scheduled test flows, do not require a separate report-generation schedule. "
    "After the last scheduled test task finishes, automatically generate one final summary report that consolidates all scheduled test results."
    "Answer clearly and concisely. "
    f"Your program is located at: {SCRIPT_DIR}"
)

TEST_PRECHECK_SYSTEM_INSTRUCTION = (
    "You are preparing a test execution precheck. "
    "Do NOT call any tools in this step. "
    "Analyze what parts of the user's requested test can be done with currently available tools, and what parts cannot be done. "
    "Return a concise plan using this structure: "
    "1) Planned Test Steps (numbered), "
    "2) Unsupported Parts, "
    "3) Capability Match (percentage), "
    "4) Ask for confirmation to start now (yes/no). "
    "If a user asks for actions like reboot/shutdown and no direct tool exists, clearly mark those actions as unsupported. "
    "The capability percentage should roughly reflect supported requested actions divided by total requested actions."
)


def _is_test_request(user_text: str) -> bool:
    text = user_text.lower()
    test_keywords = [
        "test", "validate", "verification", "verify", "reconnect", "disconnect", "connect",
        "bluetooth", "headset", "audio", "mic", "microphone", "speaker", "schedule",
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
        extra = re.sub(r"^(but|please|just|also|and|however|though)\s+", "", extra, flags=re.IGNORECASE).strip()
        return "yes", extra

    m = no_pattern.match(text)
    if m:
        extra = (m.group(2) or "").strip()
        extra = re.sub(r"^(but|please|just|also|and|however|though)\s+", "", extra, flags=re.IGNORECASE).strip()
        return "no", extra

    return "pending", ""


def _extract_capability_percent(reply_text: str) -> int | None:
    """Extract capability percentage from precheck text, if present."""
    if not reply_text:
        return None
    match = re.search(r"capability\s*match[^\d]*(\d{1,3})\s*%", reply_text, flags=re.IGNORECASE)
    if not match:
        # Fallback: find any percentage value in the reply.
        match = re.search(r"(\d{1,3})\s*%", reply_text)
    if not match:
        return None
    value = int(match.group(1))
    return max(0, min(100, value))


def _print_step_start(step_index: int, fn_name: str, fn_args: dict, step_callback: Callable[[str], None] | None = None) -> None:
    """Print a clear step-start marker before each test/tool action."""
    step_text = f"[TEST STEP START] Step {step_index}: {fn_name} | args={json.dumps(fn_args, default=str)}"
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
            extra_clause = f" Additionally: {extra_instruction}." if extra_instruction else ""
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
                "Please reply **yes** to start the test (you can also add extra instructions, e.g. \"yes, but also check the mic\") "
                "or **no** to cancel (you can also add a new request, e.g. \"no, please just check Bluetooth status\")."
            )
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": reminder_reply})
            return reminder_reply

    if require_test_confirmation and _is_test_request(user_text) and not confirmed_execution:
        messages.append({"role": "user", "content": user_text})
        precheck_response = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=f"{SYSTEM_INSTRUCTION} {TEST_PRECHECK_SYSTEM_INSTRUCTION}",
            messages=messages,
        )
        precheck_reply = "".join(block.text for block in precheck_response.content if block.type == "text")
        messages.append({"role": "assistant", "content": precheck_response.content})
        capability_percent = _extract_capability_percent(precheck_reply)

        # If coverage is 100%, start immediately after showing the schedule.
        if capability_percent == 100:
            execute_text = (
                f"Precheck complete with 100% capability match. Start now: {user_text}. "
                "Execute the planned test steps immediately and report each major step result."
            )
            messages.append({"role": "user", "content": execute_text})

            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_INSTRUCTION,
                messages=messages,
                tools=ALL_TOOLS,
            )

            tool_step_index = 0
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
                    _print_step_start(tool_step_index, fn_name, fn_args, step_callback=step_callback)
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

                    if fn_name == "capture_screen" and isinstance(result, dict) and result.get("status") == "success":
                        with open(result["file_path"], "rb") as img_file:
                            img_b64 = base64.b64encode(img_file.read()).decode("utf-8")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [
                                {"type": "text", "text": json.dumps(result, default=str)},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                            ],
                        })
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })

                messages.append({"role": "user", "content": tool_results})

                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    system=SYSTEM_INSTRUCTION,
                    messages=messages,
                    tools=ALL_TOOLS,
                )

            exec_reply = "".join(block.text for block in response.content if block.type == "text")
            messages.append({"role": "assistant", "content": response.content})
            return f"{precheck_reply}\n\n{exec_reply}"

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
        system=SYSTEM_INSTRUCTION,
        messages=messages,
        tools=ALL_TOOLS,
    )

    # Handle tool calls in a loop
    tool_step_index = 0
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
            _print_step_start(tool_step_index, fn_name, fn_args, step_callback=step_callback)
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

            # If screenshot was captured, add image content for vision analysis
            if fn_name == "capture_screen" and isinstance(result, dict) and result.get("status") == "success":
                with open(result["file_path"], "rb") as img_file:
                    img_b64 = base64.b64encode(img_file.read()).decode("utf-8")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [
                        {"type": "text", "text": json.dumps(result, default=str)},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    ],
                })
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })

        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_INSTRUCTION,
            messages=messages,
            tools=ALL_TOOLS,
        )

    reply = "".join(block.text for block in response.content if block.type == "text")
    messages.append({"role": "assistant", "content": response.content})
    return reply


def _on_scheduled_task(task: dict) -> None:
    """Execute scheduled tasks using the same tool-driven logic as normal chat."""
    global LAST_AUTO_SUMMARY_EXECUTED_COUNT

    description = task.get("description", "Scheduled task")
    task_id = task.get("task_id", "unknown")
    executed_at = task.get("executed_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"\n[SCHEDULED REMINDER] {description} (task_id={task_id}, time={executed_at})",
        flush=True,
    )
    try:
        scheduled_prompt = (
            "This is a scheduled task in a recurring test flow. "
            "Keep continuity with prior scheduled task executions in this same run, and reuse previous scheduled results when relevant. "
            "If this task asks for a final summary/report, summarize all prior scheduled executions in this thread before writing the final report. "
            f"Task ID: {task_id}. User request: {description}"
        )

        # Scheduled tasks run in background timers, so protect shared context updates.
        with SCHEDULED_MESSAGES_LOCK:
            reply = _run_agent_turn(SCHEDULED_MESSAGES, scheduled_prompt, print_tool_logs=True, require_test_confirmation=False)
        print(f"\nAgent: {reply}\n", flush=True)

        # Auto-summary: when all scheduled tasks are done, generate a final consolidated report
        # without requiring an extra manually scheduled "generate report" task.
        task_overview = list_scheduled_tasks()
        if task_overview.get("status") == "success":
            pending_count = task_overview.get("pending_count", 0)
            executed_count = task_overview.get("executed_count", 0)
            desc_lower = description.lower()
            is_explicit_report_task = "report" in desc_lower and "generate" in desc_lower

            should_auto_summarize = (
                pending_count == 0
                and executed_count >= 2
                and executed_count != LAST_AUTO_SUMMARY_EXECUTED_COUNT
                and not is_explicit_report_task
            )

            if should_auto_summarize:
                auto_summary_prompt = (
                    "All scheduled tasks in this batch are complete. "
                    "Now generate one final consolidated report based on all scheduled task executions in this conversation context. "
                    "Include successes/failures and overall pass rate. "
                    "Save the report under the report folder using the required report naming/content format."
                )
                with SCHEDULED_MESSAGES_LOCK:
                    summary_reply = _run_agent_turn(SCHEDULED_MESSAGES, auto_summary_prompt, print_tool_logs=True, require_test_confirmation=False)
                LAST_AUTO_SUMMARY_EXECUTED_COUNT = executed_count
                print(f"\nAgent: {summary_reply}\n", flush=True)
    except Exception as e:
        print(
            f"\nAgent: Scheduled task execution failed for task_id={task_id}. Error: {str(e)}\n",
            flush=True,
        )

def run_agent():
    """Run an interactive AI agent loop with tool use."""
    set_scheduler_notifier(_on_scheduled_task)

    messages = []
    print("=== AI Agent (Anthropic) ===")
    print("Skills: time, system info, laptop info, list files, read files, run commands, screen capture, bluetooth scan & connect, headset endpoint check, task scheduling")
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
