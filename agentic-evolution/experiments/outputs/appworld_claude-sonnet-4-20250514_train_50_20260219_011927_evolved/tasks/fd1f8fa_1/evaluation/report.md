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
[26, 28, 82, 93, 126, 167, 184, 197, 204, 206, 229, 232, 242, 255, 258, 272,
299, 301]
==
[26, 28, 82, 93, 126, 184, 197, 232, 255, 258, 272, 299]

In left but not right:
[167, 204, 206, 229, 242, 301]

Original values:
[26, 255, 82, 184, 272, 301, 93, 204, 167, 299, 242, 28, 197, 126, 232, 258,
206, 229]
==
[258, 197, 232, 26, 82, 126, 184, 299, 93, 272, 255, 28]