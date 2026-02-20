──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 5
Num Failed Tests : 0
Num Total  Tests : 5
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match file_system.File.
>> Passed Requirement
prepare start_file_path_to_content and end_file_path_to_content from start and
end
state of user.files, then assert the files have been renamed (with proper
content) as per
private_data.start_to_end_file_path. Also lowercase the paths first.
>> Passed Requirement
obtain all the file paths from start_file_path_to_content's keys that are not in
private_data.start_to_end_file_path. These are the file_paths that should not
have been renamed.
Assert their paths and contents are identical in the start and the end state.
>> Passed Requirement
assert none of the file_system.Directory has been deleted by
models.changed_records.
──────────────────────────────────── Fails ─────────────────────────────────────
None