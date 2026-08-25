---
name: reboot
description: >
  Use when a test requires rebooting the laptop. In the test-script runner, add
  reboot_laptop as a top-level step; the runner checkpoints its position (script,
  round, next step) before rebooting and resumes the run automatically after logon.
---

# Reboot Flow (test-script runner, with auto-resume)

Use this skill whenever a test step requires restarting the laptop. In the new
test-script-running structure, reboot is a normal step and resumption is handled
deterministically by the runner — no LLM is needed to continue after reboot.

## How it works
1. **Add `reboot_laptop` as a top-level step** in the script's `setup`, `steps`,
   or `teardown` (NOT inside an `if/then/else` branch — nested reboots can't be
   resumed and will be rejected).
2. **Automatic checkpoint.** When the runner reaches the reboot step it writes
   `test_assets/task_state/runner_state.json` containing:
   - the full script and its path,
   - `rounds` (total) and `current_round`,
   - `phase` (`setup` / `steps` / `teardown`) and `next_step_index` (the step to
     run next, i.e. AFTER the reboot),
   - the report folder and results collected so far.
3. **Auto-start on logon.** The runner registers the logon task
   (`schedule_ai_agent_on_startup`) automatically before rebooting, so the agent
   relaunches after the machine comes back up.
4. **Reboot.** `reboot_laptop` issues the Windows restart (`delay_seconds`,
   default 20) and the runner stops that process.
5. **Automatic resume.** On next launch the app detects the checkpoint, waits for
   the system/network to be ready, and calls the runner with `resume_state=...`.
   It continues from `next_step_index` in the saved phase/round — treating the
   reboot step as already done — finishes the remaining steps, rounds, and
   teardown, writes the final report, and clears the checkpoint.

## Reboot inside a repeated cycle
If the per-round `steps` include a reboot, each round re-hits it: the runner saves
a fresh checkpoint with the new `current_round`, reboots, and resumes AFTER the
reboot step. Because resume always continues past the reboot, cycles never loop
forever, and every round writes into the same report folder.

## Notes
- Reboot must be a **top-level step**; a reboot inside a conditional branch is
  rejected with an error (its position can't be resumed reliably).
- The report folder is preserved across reboots so the final report is a single
  consolidated document for all rounds.
- The final report (with Echo analysis when available) is generated only after
  the whole run — including all post-reboot rounds/teardown — completes.
