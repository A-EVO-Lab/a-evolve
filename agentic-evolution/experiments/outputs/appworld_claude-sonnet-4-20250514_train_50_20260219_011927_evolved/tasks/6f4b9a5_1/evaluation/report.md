──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 8
Num Failed Tests : 0
Num Total  Tests : 8
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match simple_note.Note.
>> Passed Requirement
obtain added, updated, removed simple_note.Note records using
models.changed_records,
and assert 0 were added or removed.
>> Passed Requirement
assert exactly 1 simple_note.Note was updated.
>> Passed Requirement
assert the updated note has ID private_data.note_id.
>> Passed Requirement
parse the contents of the updated note to obtain song_name_to_artist_names,
song_name_to_release_month_str
and assert the song_name_to_artist_names match
private_data.song_name_to_artist_names (ignore order, normalize_text).
>> Passed Requirement
assert the song_name_to_release_month_str match
private_data.song_name_to_release_month_str (ignore order, normalize_text).
>> Passed Requirement
assert the udpated note has not changed in any other field ignoring content,
updated_at,
using models.changed_field_names.
──────────────────────────────────── Fails ─────────────────────────────────────
None