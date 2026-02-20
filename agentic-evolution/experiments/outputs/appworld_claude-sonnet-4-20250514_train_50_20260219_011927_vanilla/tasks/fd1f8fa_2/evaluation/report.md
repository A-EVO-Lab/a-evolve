──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 4
Num Failed Tests : 1
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
assert music player is in playing state.
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert song_ids in the end state of the music player's queue match
private_data.expected_queue_song_ids, ignoring order.
```python
with test(
    """
    assert song_ids in the end state of the music player's queue match
    private_data.expected_queue_song_ids, ignoring order.
    """
):
    music_player = updated_music_players[0]
    queue_song_ids = list_of(music_player.queue_songs, "id")
```
----------
AssertionError:
[1, 5, 15, 60, 102]
==
[1, 5, 15, 60, 102, 104, 136, 176, 206, 214, 283, 295]

In right but not left:
[104, 136, 176, 206, 214, 283, 295]

Original values:
[60, 1, 15, 5, 102]
==
[60, 1, 104, 15, 176, 5, 295, 136, 214, 102, 283, 206]