# Skills

This folder demonstrates the official Claude **Agent Skills** structure. Each
subfolder contains a `SKILL.md` (YAML frontmatter + Markdown instructions) that was
extracted from the monolithic `SYSTEM_INSTRUCTION` string in `ai_agent.py`.

## How it works (progressive disclosure)
- Only each skill's short `name` + `description` is always visible to the model.
- The full body of a `SKILL.md` is loaded **only when that skill is relevant** to the
  user's request — so a simple "hello" or a pure Bluetooth test never pulls in the
  audio/driver/report instructions.
- This keeps per-request context small and helps avoid the 200K token limit.

## Skills in this folder
| Skill | Source block in `ai_agent.py` |
|-------|-------------------------------|
| `bluetooth-validation` | `# ── Bluetooth ──` |
| `audio-playback` | `# ── Audio / Playback ──` |
| `isst-driver-install` | `# ── Drivers ──` + `# ── File creation ──` |
| `arduino-control` | `# ── Arduino ──` |
| `report-format` | `# ── Report format ──` + `# ── File creation ──` |
| `iteration-and-scheduling` | `# ── Iteration tests ──` + `# ── Scheduled tasks ──` |

The general `# ── Role ──` block would remain in a small base system prompt that is
always sent.

## Using these skills
- **Claude Agent SDK:** point it at this `skills/` folder; it auto-discovers every
  `SKILL.md`.
- **Current Messages-API loop:** you can load only the relevant `SKILL.md` body into
  the `system` blocks per request instead of always sending the full instruction.

> Note: `ai_agent.py` is unchanged. These files are an illustration of the structure.
