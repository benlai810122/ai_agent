---
name: reboot
description: >
  Use when a test or task requires rebooting the laptop. Saves unfinished tasks
  to a file, registers a logon task so the agent auto-starts after reboot, then
  reboots; on next startup the saved tasks are reloaded and resumed automatically.
---

# Reboot Flow (with task resumption)

Use this skill whenever a step requires restarting the laptop so the validation
continues seamlessly after the machine comes back up.

## Steps
1. **Save unfinished tasks first.** Before rebooting, persist the remaining
   tasks/flow to disk so nothing is lost. Call `reboot_laptop` with
   `save_tasks=true`; the agent automatically passes the in-memory
   `pending_tasks` to be written to `test_assets/task_state/pending_tasks.json`.
   The names of any skills loaded for this task are saved too, so they are
   reloaded automatically after the reboot.
2. **Register auto-start on logon.** Call `schedule_ai_agent_on_startup` so the
   AI agent (Launch Agent.bat) launches automatically the next time the user
   logs in. Do this once before the first reboot in a flow.
3. **Reboot.** `reboot_laptop` issues the Windows reboot with a delay
   (`delay_seconds`, default 20). Tell the user the machine will restart.
4. **Resume after startup.** On launch the agent reads
   `test_assets/task_state/pending_tasks.json`, loads any unfinished tasks, and
   continues them. Re-load the skills listed in the resume prompt first, then
   pick up exactly where the flow left off — do NOT restart finished cycles.
   Re-read the saved task state and confirm remaining steps before continuing.

## Reboot as a step INSIDE a cycle
When the reboot is only one step of a larger cycle (e.g. `step1: reboot`,
`step2: turn off BT`, `step3: turn on BT`, `step4: check BT`), the machine
restarts in the MIDDLE of the cycle. On resume you MUST:
- Treat the reboot step for the current cycle as ALREADY DONE. Do **not** reboot
  again for that same cycle — doing so causes an infinite reboot loop.
- Continue from the step immediately AFTER the reboot (e.g. `step2`) and run the
  remaining steps in order.
- Call `report_cycle_result` for that cycle once its remaining steps finish. This
  records the cycle in the task state so the next reboot advances to a new cycle
  instead of repeating.
- The resume prompt and the saved task state expose a `reboot_state` marker with
  `cycle_in_progress` and `next_action`; use them to know which cycle you are in
  and what to do next.


## Notes
- Always save tasks BEFORE rebooting; never reboot with `save_tasks=false`
  unless the user explicitly has no pending work.
- Ensure the logon task is scheduled, otherwise the agent will not reopen and
  cannot resume.
- After resuming, once ALL remaining cycles/tasks complete, you MUST write the
  final consolidated report to a file in the report run folder (using
  `create_file`/`write_file`) following the report-format skill — do not finish
  by only printing the report to chat.
