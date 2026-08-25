"""Shared flow-chart model for the Web UI visual flow panel.

Turns a runnable test script (see ai_task_planner.generate_test_script) into a
compact node/group model the browser can render, and defines the canonical node
id scheme so the runner's live ``[Node]`` events line up with the rendered nodes.

Script shape: {"task", "setup":[..], "steps":[..], "teardown":[..]} where each
item is {"function", "arguments"} or a conditional {"if", "then", "else"}.
"""

import json


def _summarize_args(args, limit: int = 70) -> str:
    """Render arguments as a short one-line ``k=v, ...`` summary."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for k, v in args.items():
        vs = v if isinstance(v, str) else json.dumps(v, default=str)
        parts.append(f"{k}={vs}")
    s = ", ".join(parts)
    return s if len(s) <= limit else s[: limit - 1] + "\u2026"


def _build_nodes(steps, base: str) -> list:
    """Build the node list for one phase/branch, ids prefixed with ``base``."""
    nodes = []
    if not isinstance(steps, list):
        return nodes
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        nid = f"{base}-{i}"
        if "if" in step and isinstance(step.get("if"), dict):
            if_spec = step["if"]
            fn = str(if_spec.get("function", "")).strip()
            cond = str(if_spec.get("condition", "ok")).strip()
            nodes.append({
                "id": nid,
                "type": "condition",
                "function": fn,
                "summary": f"{fn}({_summarize_args(if_spec.get('arguments'))})",
                "condition": cond,
                "then": _build_nodes(step.get("then") or [], f"{nid}-then"),
                "else": _build_nodes(step.get("else") or [], f"{nid}-else"),
            })
        else:
            fn = str(step.get("function", "")).strip()
            nodes.append({
                "id": nid,
                "type": "action",
                "function": fn,
                "summary": f"{fn}({_summarize_args(step.get('arguments'))})",
            })
    return nodes


def build_flow_model(script, rounds: int = 1) -> dict | None:
    """Return a {task, rounds, groups:[{key,title,nodes}]} model, or None."""
    if not isinstance(script, dict):
        return None
    setup = script.get("setup") or []
    steps = script.get("steps") or []
    teardown = script.get("teardown") or []
    task = script.get("task") or "Test run"

    groups = []
    if setup:
        groups.append({"key": "setup", "title": "Setup", "nodes": _build_nodes(setup, "setup")})
    groups.append({
        "key": "steps",
        "title": "Test Steps" + (f" (\u00d7{rounds} rounds)" if rounds and rounds > 1 else ""),
        "nodes": _build_nodes(steps, "steps"),
    })
    if teardown:
        groups.append({"key": "teardown", "title": "Teardown", "nodes": _build_nodes(teardown, "teardown")})

    return {"task": task, "rounds": int(rounds or 1), "groups": groups}


def flow_event(model) -> str:
    """Serialize a model as a ``[Flow] <json>`` progress line."""
    return "[Flow] " + json.dumps(model, default=str)


def node_event(node_base: str, index: int, status: str, *, ok=None, round_number=None) -> str:
    """Serialize a ``[Node] <json>`` live-highlight progress line."""
    payload = {"id": f"{node_base}-{index}", "status": status}
    if ok is not None:
        payload["ok"] = bool(ok)
    if round_number is not None:
        payload["round"] = int(round_number)
    return "[Node] " + json.dumps(payload, default=str)


def round_event(round_number: int, total: int) -> str:
    """Serialize a ``[Round] <json>`` progress line."""
    return "[Round] " + json.dumps({"round": int(round_number), "total": int(total)})
