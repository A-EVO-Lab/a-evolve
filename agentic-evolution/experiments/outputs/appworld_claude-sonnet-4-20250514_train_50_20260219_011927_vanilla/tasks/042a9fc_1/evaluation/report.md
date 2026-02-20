──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 2
Num Failed Tests : 3
Num Total  Tests : 5
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match spotify.PlaylistSong.
>> Passed Requirement
assert added and removed song ids have playlist_id = private_data.playlist_id
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
AssertionError:
"updated the 'road trip with roommates' playlist based on roommate suggestions:
removed 3 songs (reflections in a pool of regret, chasing echoes in the dark,
beyond the echo) and added 2 songs (electric pulse by felix blackwood, lonesome
road by seraphina dawn). paul miller had no suggestions. final playlist has 20
songs."
==
'null'
>> Failed Requirement
obtain added spotify.PlaylistSong records using models.changed_records,
and assert song_ids of the added playlist songs match
private_data.to_add_song_ids.
```python
with test(
    """
    obtain added spotify.PlaylistSong records using models.changed_records,
    and assert song_ids of the added playlist songs match
private_data.to_add_song_ids.
    """
):
    added_playlist_songs, _, removed_playlist_songs = models.changed_records(
        "spotify.PlaylistSong"
```
----------
AssertionError:  {305, 111} == {136, 145, 111, 305}

In right but not left:
[136, 145]
>> Failed Requirement
assert song_ids of the removed playlist songs match
private_data.to_remove_song_ids.
```python
with test(
    """
    assert song_ids of the removed playlist songs match
private_data.to_remove_song_ids.
    """
):
    removed_song_ids = set_of(removed_playlist_songs, "song_id")
    test.case(removed_song_ids, "==", set(private_data.to_remove_song_ids))
```
----------
AssertionError:  {113, 173, 6} == {6, 167, 173, 113, 215, 185}

In right but not left:
[167, 215, 185]