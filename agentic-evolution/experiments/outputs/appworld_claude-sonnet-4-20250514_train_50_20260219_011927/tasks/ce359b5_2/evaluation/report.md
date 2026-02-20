──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 8
Num Failed Tests : 0
Num Total  Tests : 8
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match spotify.UserLibrarySong, spotify.PlaylistSong.
>> Passed Requirement
obtain added, updated, removed spotify.UserLibrarySong records using
models.changed_records,
and assert 0 have been added or updated.
>> Passed Requirement
assert song_ids of the removed library songs match
private_data.to_delete_library_song_link_ids.
>> Passed Requirement
obtain added, updated, removed spotify.PlaylistSong records using
models.changed_records,
and assert 0 have been added or updated.
>> Passed Requirement
assert id of the removed playlist_songs match
private_data.to_delete_playlist_song_link_ids.
>> Passed Requirement
if public_data.before_after is "after",
assert all songs in song library are released on or before public_data.year, in
the end state.
if public_data.before_after is "before",
assert all songs in song library are released on or after public_data.year, in
the end state.
if public_data.before_after is "in or after",
assert all songs in song library are released on before public_data.year, in the
end state.
if public_data.before_after is "in or before",
assert all songs in song library are released on after public_data.year, in the
end state.
>> Passed Requirement
if public_data.before_after is "after",
assert all songs in playlists are released on or before public_data.year, in the
end state.
if public_data.before_after is "before",
assert all songs in playlists are released on or after public_data.year, in the
end state.
if public_data.before_after is "in or after",
assert all songs in playlists are released on before public_data.year, in the
end state.
if public_data.before_after is "in or before",
assert all songs in playlists are released on after public_data.year, in the end
state.
──────────────────────────────────── Fails ─────────────────────────────────────
None