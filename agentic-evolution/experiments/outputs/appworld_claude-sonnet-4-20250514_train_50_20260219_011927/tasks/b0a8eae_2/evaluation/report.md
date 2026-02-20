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
assert music_player in the end state is in the playing state.
>> Passed Requirement
assert the music_player's queue_songs' total duration in the end state
is >= data.private.workout_duration_mins.
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert the set music_player's queue_songs' song_ids in the end state
match set of private_data.long_enough_playlist_song_ids.
```python
with test(
    """
    assert the set music_player's queue_songs' song_ids in the end state
    match set of private_data.long_enough_playlist_song_ids.
    """
):
    queue_song_ids = set_of(queue_songs, "id")
```
----------
AssertionError:
{256, 259, 133, 10, 139, 15, 274, 19, 20, 28, 30, 159, 33, 290, 165, 297, 312,
316, 63, 319, 191, 194, 69, 70, 207, 212, 223, 95, 226, 227, 230, 112, 240}
==
{322}

In left but not right:
[256, 259, 133, 10, 139, 15, 274, 19, 20, 28, 30, 159, 33, 290, 165, 297, 312,
316, 63, 319, 191, 194, 69, 70, 207, 212, 223, 95, 226, 227, 230, 112, 240]

In right but not left:
[322]