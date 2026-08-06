"""Reusable API wrapper around the Echo MCP server's `echo_chat` tool.

This module is intentionally standalone (NOT wired into ai_agent.py). Import it
wherever you need Echo to analyze something:

    from echo_mcp_api import analyze_test_result

    verdict = analyze_test_result(final_test_log_text)
    print(verdict)

Design notes:
  * Exposes a plain SYNCHRONOUS function so it drops into synchronous code
    (like the agent's test runner) without the caller touching asyncio.
  * Each call opens a short-lived MCP session, runs one `echo_chat`, and closes
    it. Simple and robust; no shared/persistent connection to manage.
  * TLS verification is disabled by default to match the Intel-internal cert
    setup used elsewhere in this project (see ai_agent.py's gnai client).
"""

from __future__ import annotations

import asyncio
import threading

import httpx2

from mcp import ClientSession
from mcp.client.sse import sse_client


DEFAULT_URL = "https://echo-backend.intel.com/mcp/sse"

# Default framing wrapped around raw text handed to analyze_test_result().
DEFAULT_ANALYSIS_INSTRUCTION = (
    "You are analyzing the final result of an automated wireless-validation "
    "test run. Review the data below and provide a concise analysis: overall "
    "pass/fail verdict, key observations, likely root cause of any failures, "
    "and recommended next steps.\n\n"
    "=== TEST RESULT ===\n"
    "{result}\n"
    "=== END TEST RESULT ==="
)


class EchoMcpError(RuntimeError):
    """Raised when the Echo MCP call fails or returns an error result."""


def _insecure_http_client_factory(headers=None, timeout=None, auth=None):
    """httpx_client_factory that skips TLS verification (Intel internal certs)."""
    kwargs = {"follow_redirects": True, "verify": False}
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    return httpx2.AsyncClient(**kwargs)


def _render_content(content) -> str:
    """Flatten an MCP tool result's content blocks into plain text."""
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else repr(block))
    return "\n".join(parts).strip()


async def _acall_echo_chat(
    question: str,
    *,
    url: str,
    timeout: float,
    verify_tls: bool,
    use_file_search: bool,
    user_context: str | None,
    conversation_history,
) -> str:
    factory = None if verify_tls else _insecure_http_client_factory
    async with sse_client(
        url=url,
        timeout=timeout,
        sse_read_timeout=max(timeout, 300.0),
        httpx_client_factory=factory or None,
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            call_args: dict = {
                "question": question,
                "use_file_search": use_file_search,
            }
            if user_context:
                call_args["user_context"] = user_context
            if conversation_history:
                call_args["conversation_history"] = conversation_history
            result = await session.call_tool("echo_chat", call_args)
            if getattr(result, "isError", False):
                raise EchoMcpError(_render_content(result.content))
            return _render_content(result.content)


def _run_async(coro):
    """Run an async coroutine from sync code, even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside an event loop (e.g. called from async context): use a
    # dedicated thread with its own loop so we don't clash with the caller's.
    box: dict = {}

    def worker():
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def ask_echo(
    question: str,
    *,
    user_context: str | None = None,
    use_file_search: bool = True,
    conversation_history=None,
    url: str = DEFAULT_URL,
    timeout: float = 120.0,
    verify_tls: bool = False,
) -> str:
    """Send one free-form question to Echo and return its plain-text answer.

    Low-level entry point. For test-result analysis, prefer
    analyze_test_result(), which frames the prompt for you.
    """
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")
    return _run_async(
        _acall_echo_chat(
            question,
            url=url,
            timeout=timeout,
            verify_tls=verify_tls,
            use_file_search=use_file_search,
            user_context=user_context,
            conversation_history=conversation_history,
        )
    )


def analyze_test_result(
    result_text: str,
    *,
    user_context: str | None = None,
    instruction_template: str = DEFAULT_ANALYSIS_INSTRUCTION,
    use_file_search: bool = True,
    url: str = DEFAULT_URL,
    timeout: float = 120.0,
    verify_tls: bool = False,
) -> str:
    """Analyze a final test result with Echo and return the analysis text.

    Args:
        result_text: The raw test result / log to be analyzed.
        user_context: Optional role/domain hint, e.g. "WiFi driver developer".
        instruction_template: Prompt framing; must contain a `{result}` slot.
        use_file_search: Let Echo consult org docs (True) or answer directly.
        url / timeout / verify_tls: Connection settings.

    Returns:
        Echo's analysis as a string.

    Raises:
        ValueError: if result_text is empty.
        EchoMcpError: if Echo returns an error result.
    """
    if not result_text or not result_text.strip():
        raise ValueError("result_text must be a non-empty string")
    question = instruction_template.format(result=result_text)
    return ask_echo(
        question,
        user_context=user_context,
        use_file_search=use_file_search,
        url=url,
        timeout=timeout,
        verify_tls=verify_tls,
    )


if __name__ == "__main__":
    # Minimal self-demo: feed a fake test result and print Echo's analysis.
    sample = (
        "Test: Bluetooth A2DP reconnect after S3 resume\n"
        "Iterations: 50\n"
        "Passed: 47\n"
        "Failed: 3 (iterations 12, 29, 41 - audio did not resume within 5s)\n"
        "Observed: hci0 reset logged before each failure; driver = intel-bt 23.10\n"
    )
    print("Sending sample test result to Echo for analysis...\n")
    analysis = analyze_test_result(
        sample, user_context="The user is a Bluetooth validation engineer"
    )
    print(analysis)
