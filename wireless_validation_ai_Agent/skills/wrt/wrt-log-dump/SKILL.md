---
name: wrt-log-dump
description: >
  Use when a test needs to collect WRT (Wireless Reliability Tool) logs: checking
  whether WRT is installed, dumping logs, copying the run's logs into the report
  folder, and scanning for WRT error codes.
---

# WRT Log Dump

WRT logs are produced by the WRT system (`cde.exe`) and are written under
`C:\OSData\SystemData\Temp\WRT2G\Log`.

## 1. Check WRT is installed first
Before dumping any log, call `check_wrt_installed` to confirm the WRT system
(`cde.exe`) exists on this laptop. If `installed` is `false`, the laptop does NOT
have the WRT system installed — skip the WRT log dump steps and note this in the
report.

## 2. Dump the WRT log
Call `dump_wrt_log` to dump the WRT log. The log is written to
`C:\OSData\SystemData\Temp\WRT2G\Log`. Dumping takes at least 60 seconds to
complete, so wait for the tool to return before continuing.

**Dump only ONCE per cycle.** Run `dump_wrt_log` a single time at the END of the
cycle, after ALL of the cycle's steps are finished (for example, after both the
install and the upgrade phases of a driver test). Do NOT dump after each sub-step,
otherwise the same cycle produces duplicate dumps.

## 3. Copy the test run's logs to the report folder
After the test is finished, call `copy_wrt_log_to_file` to copy the WRT log folders
created during the test into the report run folder.

Capture the start time at the BEGINNING of the cycle by calling `get_current_time`
and keeping its `timestamp` (epoch) value. Pass that exact `timestamp` as
`start_time` so only logs created during the cycle are copied, and set `log_path`
to the current report run folder. Never pass `0` or omit the time — that copies the
entire WRT log folder, including logs from previous cycles/tests.

For multi-iteration/cycle tests, capture and use each cycle's own start-time
`timestamp` when copying that cycle's WRT log. This ensures each cycle only captures
the logs produced during that cycle and avoids copying the same WRT logs repeatedly
across cycles. The tool reports `copied_count` and `skipped_count` — a `skipped_count`
of 0 on a later cycle is a sign the start_time was wrong.

## 4. Filter for WRT error codes
After the log copy is finished, call `wrt_error_code_filter` on the copied log path.
If any WRT error codes are detected, record them in the report file (including the
WRT code and the time it happened).

## 5. Never clear logs unless asked
Never clear the WRT log (do NOT call `clear_all_log`) unless the user explicitly
asks for it.
