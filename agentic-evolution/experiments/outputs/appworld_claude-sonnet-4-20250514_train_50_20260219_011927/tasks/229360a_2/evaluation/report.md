──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 6
Num Failed Tests : 0
Num Total  Tests : 6
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match spotify.UserLibrarySong, spotify.UserLibraryAlbum.
>> Passed Requirement
obtain added, removed spotify.UserLibrarySong records using
models.changed_records,
and assert 0 have been added.
>> Passed Requirement
assert all removed song_ids match private_data.to_remove_library_song_ids
(ignore_order)
>> Passed Requirement
obtain added, removed spotify.UserLibraryAlbum records using
models.changed_records,
and assert 0 have been added.
>> Passed Requirement
assert all removed album_ids match private_data.to_remove_library_album_ids
(ignore_order)
──────────────────────────────────── Fails ─────────────────────────────────────
None