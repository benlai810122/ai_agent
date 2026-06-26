---
name: report-format
description: >
  Use whenever starting a test or writing a test report: how to create the report
  run folder, the required filename pattern, and the mandatory report contents.
---

# Test Report Format

## Run folder
When starting any test, first create a folder under the `report` directory named
`report_YYYYMMDD_HHmmss` (e.g. `report_20260527_143000`). Save all test-related
files — logs, audio recordings, screenshots, and the final report — inside that
test run folder.

## Report file
The report filename must follow this exact pattern: `report_YYYYMMDD_HHmmss`
(same format as the folder name).

## Required contents (in this order)
1. Test Item
2. Test Result
3. Summary

The report must also include **Test Start Time** and **Test End Time**.
If any error happens, the report must include **Test Error Happened Time**.

## File creation tools
When creating files with content, use `create_file` with the `content` parameter,
or use `write_file` with both `file_path` and `content`.
