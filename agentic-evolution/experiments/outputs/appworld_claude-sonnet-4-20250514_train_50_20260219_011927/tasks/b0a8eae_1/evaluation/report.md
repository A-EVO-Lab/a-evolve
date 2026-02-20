──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 3
Num Failed Tests : 2
Num Total  Tests : 5
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match spotify.MusicPlayer.
>> Passed Requirement
assert music_player in the end state is in the playing state.
>> Passed Requirement
assert the music_player's queue_songs' total duration in the end state
is >= data.private.workout_duration_mins.
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
AssertionError:  '<<not_given>>' == 'null'
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
AssertionError:  {191, 69, 230, 10, 139, 112, 274, 212, 30, 95} == {80}

In left but not right:
[191, 69, 230, 10, 139, 112, 274, 212, 30, 95]

In right but not left:
[80]