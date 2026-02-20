──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 8
Num Failed Tests : 0
Num Total  Tests : 8
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match file_system.Directory, file_system.File.
>> Passed Requirement
obtain added, updated, removed file_system.File records using
models.changed_records,
and assert 0 were updated.
>> Passed Requirement
assert all added files have paths that match private_data.compressed_file_paths
(ignoring order).
>> Passed Requirement
assert all removed files have paths that match keys of
private_data.decompressed_file_path_to_content (ignoring order).
>> Passed Requirement
assert path to content mapping of removed files match
private_data.decompressed_file_path_to_content.
>> Passed Requirement
obtain added, removed file_system.Directory records using
models.changed_records.
and assert 0 were added.
>> Passed Requirement
assert all removed file_system.Directory records have tilde_paths that match
private_data.decompressed_directory_paths (ignoring order).
──────────────────────────────────── Fails ─────────────────────────────────────
None