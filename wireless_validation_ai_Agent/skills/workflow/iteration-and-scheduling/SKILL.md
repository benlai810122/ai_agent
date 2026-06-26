---
name: iteration-and-scheduling
description: >
  Use for multi-iteration/cycle tests and scheduled test flows: per-cycle reporting
  and automatic final consolidated reporting.
---

# Iteration & Scheduled Test Flows

## Iteration (cycle) tests
When running a test that repeats for multiple iterations/cycles:
1. Reset any internal step counter mentally at the start of each new cycle.
2. Record each cycle's start time at the BEGINNING of every cycle, and include
   these start times in the final consolidated report as well.
3. At the END of every completed cycle, call `report_cycle_result` with the cycle
   number, `PASS` or `FAIL`, and a brief 1-2 sentence summary before starting the
   next cycle.

This gives the user real-time per-cycle feedback and makes it clear when one round
finishes and the next begins.

## Scheduled test flows
When running multi-step scheduled test flows, do not require a separate
report-generation schedule. After the last scheduled test task finishes,
automatically generate one final summary report that consolidates all scheduled
test results.
