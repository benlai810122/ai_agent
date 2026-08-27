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
                "arguments": if_spec.get("arguments") or {},
                "summary": f"{fn}({_summarize_args(if_spec.get('arguments'))})",
                "condition": cond,
                "wrt_debug": bool(step.get("wrt_debug")),
                "then": _build_nodes(step.get("then") or [], f"{nid}-then"),
                "else": _build_nodes(step.get("else") or [], f"{nid}-else"),
            })
        else:
            fn = str(step.get("function", "")).strip()
            nodes.append({
                "id": nid,
                "type": "action",
                "function": fn,
                "arguments": step.get("arguments") or {},
                "summary": f"{fn}({_summarize_args(step.get('arguments'))})",
                "wrt_debug": bool(step.get("wrt_debug")),
            })
    return nodes


def build_flow_model(script, rounds: int = 1, editable: bool = False) -> dict | None:
    """Return a {task, rounds, editable, groups:[{key,title,nodes}]} model, or None."""
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

    return {"task": task, "rounds": int(rounds or 1), "editable": bool(editable), "groups": groups}


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


def flow_clear_event() -> str:
    """A ``[FlowClear]`` line telling the Web UI panel to reset to empty."""
    return "[FlowClear]"


def resolve_node(script, node_id):
    """Resolve a flow node id to the underlying script step dict.

    Returns ``(step_dict, kind)`` where kind is "action" or "condition", or
    ``(None, None)`` if the id does not point at a valid step. Ids follow the
    ai_flow_model scheme: ``{phase}-{i}`` with optional ``-then-{j}`` / ``-else-{j}``
    branch segments (see _build_nodes).
    """
    if not isinstance(script, dict) or not node_id:
        return None, None
    tokens = str(node_id).split("-")
    if len(tokens) < 2:
        return None, None
    phase = tokens[0]
    phase_list = script.get(phase)
    if not isinstance(phase_list, list):
        return None, None
    try:
        idx = int(tokens[1])
    except ValueError:
        return None, None
    if idx < 1 or idx > len(phase_list):
        return None, None
    node = phase_list[idx - 1]

    i = 2
    while i < len(tokens):
        if i + 1 >= len(tokens):
            return None, None
        branch, num_tok = tokens[i], tokens[i + 1]
        if branch not in ("then", "else"):
            return None, None
        try:
            j = int(num_tok)
        except ValueError:
            return None, None
        branch_list = node.get(branch) if isinstance(node, dict) else None
        if not isinstance(branch_list, list) or j < 1 or j > len(branch_list):
            return None, None
        node = branch_list[j - 1]
        i += 2

    if not isinstance(node, dict):
        return None, None
    kind = "condition" if ("if" in node and isinstance(node.get("if"), dict)) else "action"
    return node, kind


def remove_node(script, node_id) -> bool:
    """Delete the step a flow node id points at (a condition node removes its branches too).

    Returns True if a step was removed. Uses the same id scheme as ``resolve_node``.
    """
    if not isinstance(script, dict) or not node_id:
        return False
    tokens = str(node_id).split("-")
    if len(tokens) < 2:
        return False
    phase = tokens[0]
    lst = script.get(phase)
    if not isinstance(lst, list):
        return False
    try:
        idx = int(tokens[1])
    except ValueError:
        return False
    if idx < 1 or idx > len(lst):
        return False

    parent_list, parent_index = lst, idx - 1
    node = lst[idx - 1]
    i = 2
    while i < len(tokens):
        if i + 1 >= len(tokens):
            return False
        branch, num_tok = tokens[i], tokens[i + 1]
        if branch not in ("then", "else"):
            return False
        try:
            j = int(num_tok)
        except ValueError:
            return False
        blist = node.get(branch) if isinstance(node, dict) else None
        if not isinstance(blist, list) or j < 1 or j > len(blist):
            return False
        parent_list, parent_index = blist, j - 1
        node = blist[j - 1]
        i += 2

    del parent_list[parent_index]
    return True


def _coerce_value(value, json_type):
    """Coerce an incoming form value to the schema's JSON type (raises on bad input)."""
    if json_type == "integer":
        if isinstance(value, bool):
            raise ValueError("expected integer")
        return int(value)
    if json_type == "number":
        if isinstance(value, bool):
            raise ValueError("expected number")
        return float(value)
    if json_type == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        raise ValueError("expected boolean")
    if json_type in ("array", "object"):
        if isinstance(value, (list, dict)):
            return value
        parsed = json.loads(value)
        if json_type == "array" and not isinstance(parsed, list):
            raise ValueError("expected array")
        if json_type == "object" and not isinstance(parsed, dict):
            raise ValueError("expected object")
        return parsed
    return value if isinstance(value, str) else value


def coerce_and_validate(schema, args):
    """Validate/coerce ``args`` against a tool JSON schema.

    Returns ``(ok, coerced_args, errors)``. Only the provided keys are checked
    (v1 edits existing parameter values only), plus a blank check on any provided
    key that the schema marks required.
    """
    props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
    required = (schema or {}).get("required", []) if isinstance(schema, dict) else []
    coerced = {}
    errors = []
    for k, v in (args or {}).items():
        spec = props.get(k, {}) if isinstance(props, dict) else {}
        json_type = spec.get("type") if isinstance(spec, dict) else None
        enum = spec.get("enum") if isinstance(spec, dict) else None
        try:
            cv = _coerce_value(v, json_type)
        except (ValueError, TypeError):
            errors.append(f"'{k}' must be of type {json_type}")
            continue
        if enum and cv not in enum:
            errors.append(f"'{k}' must be one of {enum}")
            continue
        coerced[k] = cv
    for rk in required:
        if rk in (args or {}) and coerced.get(rk) in (None, ""):
            errors.append(f"'{rk}' is required")
    return (len(errors) == 0), coerced, errors
