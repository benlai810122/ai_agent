"""Agent skill loading (progressive disclosure).

Instead of sending one giant system prompt every request, the detailed
instructions live in skills/<name>/SKILL.md files. Only each skill's short
name + description is always in the system prompt. The model loads the full
body of a skill ON DEMAND by calling the load_skill tool. This keeps each
request small and avoids hitting the model context limit.

This module holds all the discovery/loading helpers so ai_agent.py can stay
focused on the agent loop.
"""

import os
import yaml


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


def discover_skills(skills_dir: str) -> dict:
    """Recursively scan skills_dir for all SKILL.md files and return {name: skill}."""
    skills: dict = {}
    if not os.path.isdir(skills_dir):
        return skills

    for root, dirnames, filenames in os.walk(skills_dir):
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


def build_skill_catalog(skills: dict) -> str:
    """Build the always-on catalog text listing each skill name + description."""
    if not skills:
        return "No skills are currently available.\n"

    lines = []
    for skill in skills.values():
        # Collapse whitespace so multi-line YAML descriptions read as one line.
        desc = " ".join(skill["description"].split())
        lines.append(f"- {skill['name']}: {desc}")
    return "\n".join(lines)


def make_load_skill(skills: dict):
    """Return a load_skill(name) tool implementation bound to the given skills."""

    def load_skill(name: str) -> dict:
        """Tool: return the full instructions (body) for a named skill."""
        skill = skills.get(name)
        if not skill:
            available = ", ".join(skills.keys()) or "(none)"
            return {
                "status": "error",
                "error": f"Unknown skill '{name}'. Available skills: {available}",
            }
        return {
            "status": "success",
            "name": skill["name"],
            "instructions": skill["body"],
        }

    return load_skill


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
