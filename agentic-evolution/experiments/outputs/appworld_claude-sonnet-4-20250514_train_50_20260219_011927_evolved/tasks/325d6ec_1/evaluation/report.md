──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 5
Num Failed Tests : 0
Num Total  Tests : 5
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match spotify.MusicPlayer.
>> Passed Requirement
obtain added, updated, removed spotify.MusicPlayer records using
models.changed_records,
and assert 1 has been updated, and 0 have been added or removed.
>> Passed Requirement
assert music player's cursor is on the song with private_data.target_song_id.
>> Passed Requirement
assert updated music_player's changed_field_names is only {"cursor_position"}.
──────────────────────────────────── Fails ─────────────────────────────────────
None