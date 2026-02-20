──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 3
Num Failed Tests : 1
Num Total  Tests : 4
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match file_system.File.
>> Passed Requirement
prepare start_file_path_to_content and end_file_path_to_content from start and
end state of user.files,
then assert the files have been renamed (with proper content) as per
private_data.meeting_start_to_end_file_path.
>> Passed Requirement
obtain all the file paths from start_file_path_to_content's keys that are not in
private_data.meeting_start_to_end_file_path. These are the file_paths that
should not have been renamed.
Assert their paths and contents are identical in the start and the end state.
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert answers match.
```python
with test(
    """
    assert answers match.
    """
):
    test.answer(predicted_answer, ground_truth_answer)
```
----------
AssertionError:
"files successfully reorganized from '<date>__<file_name>.<extension>' format to
'<file_name>/<date>.<extension>' format. created 'slides/' and
'video_recording/' directories containing all files organized by type with dates
as filenames."
==
'null'