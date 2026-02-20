──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 1
Num Failed Tests : 6
Num Total  Tests : 7
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert model changes match spotify.Playlist, spotify.PlaylistSong.
```python
with test(
    """
    assert model changes match spotify.Playlist, spotify.PlaylistSong.
    """
):
    test.case(
        models.changed_model_names(),
        "==",
```
----------
AssertionError:  set() == {'spotify.Playlist', 'spotify.PlaylistSong'}

In right but not left:
['spotify.Playlist', 'spotify.PlaylistSong']
>> Failed Requirement
obtain added, updated, deleted spotify.Playlist records using
models.changed_records,
and assert 1 has been added, 0 have been updated or deleted.
```python
with test(
    """
    obtain added, updated, deleted spotify.Playlist records using
models.changed_records,
    and assert 1 has been added, 0 have been updated or deleted.
    """
):
    added_playlists, updated_playlists, deleted_playlists =
models.changed_records(
        "spotify.Playlist"
```
----------
AssertionError:  0 == 1
>> Failed Requirement
assert the added playlist's title is public_data.playlist_title.
```python
with test(
    """
    assert the added playlist's title is public_data.playlist_title.
    """
):
    added_playlist = added_playlists[0]
    test.case(added_playlist.title, "==", public_data.playlist_title)
```
----------
>> Failed Requirement
obtain added, deleted spotify.PlaylistSong records using models.changed_records,
and assert set of added song ids match to set of
private_data.to_archive_song_ids.
```python
with test(
    """
    obtain added, deleted spotify.PlaylistSong records using
models.changed_records,
    and assert set of added song ids match to set of
private_data.to_archive_song_ids.
    """
):
    added_playlist_songs, _, deleted_playlist_songs = models.changed_records(
        "spotify.PlaylistSong"
```
----------
AssertionError:
set()
==
{96, 160, 291, 104, 201, 41, 203, 238, 46, 80, 117, 246, 215, 216, 153, 278, 88,
188}

In right but not left:
[96, 160, 291, 104, 201, 41, 203, 238, 46, 80, 117, 246, 215, 216, 153, 278, 88,
188]
>> Failed Requirement
assert set of deleted song ids also match to set of
private_data.to_archive_song_ids.
```python
with test(
    """
    assert set of deleted song ids also match to set of
private_data.to_archive_song_ids.
    """
):
    deleted_song_ids = set_of(deleted_playlist_songs, "song_id")
    test.case(deleted_song_ids, "==", set(private_data.to_archive_song_ids))
```
----------
AssertionError:
set()
==
{96, 160, 291, 104, 201, 41, 203, 238, 46, 80, 117, 246, 215, 216, 153, 278, 88,
188}

In right but not left:
[96, 160, 291, 104, 201, 41, 203, 238, 46, 80, 117, 246, 215, 216, 153, 278, 88,
188]
>> Failed Requirement
assert playlist_id_to_song_ids of deleted_playlist_songs match
private_data.to_archive_playlist_id_to_song_ids (ignore_order)
```python
with test(
    """
    assert playlist_id_to_song_ids of deleted_playlist_songs match
    private_data.to_archive_playlist_id_to_song_ids (ignore_order)
    """
):
    playlist_id_to_song_ids = dict_list_of(deleted_playlist_songs,
"playlist_id", "song_id")
    test.case(
```
----------
AssertionError:
{}
==
{160: [117, 216, 246], 161: [201, 215], 162: [41, 153, 188], 167: [41, 46], 163:
[96], 164: [80, 203, 238], 165: [46, 88, 104], 166: [160, 278, 291]}

Original values:
defaultdict(<class 'list'>, {})
==
Munch({160: [117, 216, 246], 161: [201, 215], 162: [41, 153, 188], 167: [41,
46], 163: [96], 164: [203, 80, 238], 165: [104, 46, 88], 166: [278, 160, 291]})