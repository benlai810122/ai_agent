"""Anthropic backend glue for context gathering during test-script generation.

`task_planner.generate_test_script` accepts an optional `gather_context(instruction)`
callable so it can obtain REAL runtime values (report folder path, current time,
device names) before writing the script — instead of guessing parameters like
save paths. That callable is backend-specific, so its Anthropic implementation
lives here (as a factory) to keep ai_agent.py focused on the main agent loop.
"""

import json


# Read-only / setup tools the script planner may call to gather real values.
# Kept deliberately small and side-effect-light so planning never runs the actual
# test actions (playback, recording, reboots, driver installs, etc.).
DEFAULT_SCRIPT_CONTEXT_TOOLS = {
    "get_current_time",
    "create_report_folder",
    "get_audio_endpoints",
    "check_headset_endpoint",
    "get_audio_volume",
    "get_system_info",
    "get_laptop_info",
    "list_files",
}


def make_script_context_gatherer(
    *,
    client,
    model,
    system_blocks,
    tools,
    tool_functions,
    tool_names=DEFAULT_SCRIPT_CONTEXT_TOOLS,
    track_usage=None,
    get_step_callback=None,
    max_rounds=6,
):
    """Build a gather_context(instruction) -> str callable bound to the Anthropic API.

    Runs a short tool-enabled loop restricted to `tool_names`, executing them via
    `tool_functions`, and returns a plain-text summary of the facts gathered.

    Injected dependencies:
      - client / model / system_blocks: the Anthropic client and request config.
      - tools: full tool-schema list (Anthropic format); filtered to tool_names.
      - tool_functions: {name: callable} used to actually run the info tools.
      - track_usage(response, label, step_callback): optional token-usage logger.
      - get_step_callback(): optional; returns the active UI/console callback.
    """
    info_tools = [t for t in tools if t["name"] in tool_names]

    def gather_context(instruction: str) -> str:
        if not info_tools:
            return ""

        step_callback = get_step_callback() if get_step_callback else None
        convo = [{"role": "user", "content": instruction}]
        gathered: list[str] = []

        for _ in range(max_rounds):
            response = client.messages.create(
                model=model,
                max_tokens=1500,
                system=system_blocks,
                messages=convo,
                tools=info_tools,
            )
            if track_usage:
                track_usage(response, "script-context", step_callback)

            if not any(block.type == "tool_use" for block in response.content):
                final = "".join(
                    b.text for b in response.content if b.type == "text"
                ).strip()
                if final:
                    gathered.append(final)
                break

            convo.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                fn = tool_functions.get(block.name)
                args = block.input if block.input else {}
                try:
                    result = fn(**args) if fn else {"error": "unknown tool"}
                except Exception as e:  # noqa: BLE001
                    result = {"error": str(e)}
                gathered.append(
                    f"{block.name}({json.dumps(args, default=str)}) -> "
                    f"{json.dumps(result, default=str)}"
                )
                if step_callback:
                    try:
                        step_callback(f"[Context] {block.name} -> gathered")
                    except Exception:
                        pass
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            convo.append({"role": "user", "content": tool_results})

        return "\n".join(gathered).strip()

    return gather_context
