──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 3
Num Failed Tests : 3
Num Total  Tests : 6
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match spotify.UserLibrarySong, spotify.UserLibraryAlbum.
>> Passed Requirement
obtain added, removed spotify.UserLibraryAlbum records using
models.changed_records,
and assert 0 have been added.
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
obtain added, removed spotify.UserLibrarySong records using
models.changed_records,
and assert 0 have been added.
```python
with test(
    """
    obtain added, removed spotify.UserLibrarySong records using
models.changed_records,
    and assert 0 have been added.
    """
):
    added_library_songs, _, removed_library_songs = models.changed_records(
        "spotify.UserLibrarySong"
```
----------
AssertionError:  3 == 0
>> Failed Requirement
assert all removed song_ids match private_data.to_remove_library_song_ids
(ignore_order)
```python
with test(
    """
    assert all removed song_ids match private_data.to_remove_library_song_ids
(ignore_order)
    """
):
    removed_library_song_ids = list_of(removed_library_songs, "song_id")
    test.case(
        removed_library_song_ids,
```
----------
AssertionError:
[125, 139, 143, 149, 214, 228, 257, 259, 310, 317, 319]
==
[23, 105, 139, 143, 214, 259, 310]

In left but not right:
[125, 149, 228, 257, 317, 319]

In right but not left:
[23, 105]

Original values:
[319, 317, 125, 310, 214, 257, 149, 228, 143, 259, 139]
==
[259, 105, 139, 143, 214, 23, 310]
>> Failed Requirement
assert all removed album_ids match private_data.to_remove_library_album_ids
(ignore_order)
```python
with test(
    """
    assert all removed album_ids match private_data.to_remove_library_album_ids
(ignore_order)
    """
):
    removed_library_album_ids = list_of(removed_library_albums, "album_id")
    test.case(
        removed_library_album_ids,
```
----------
AssertionError:  [1, 4, 7, 8, 16, 17, 18] == [4, 7, 8, 16]

In left but not right:
[1, 17, 18]

Original values:
[18, 1, 16, 8, 17, 7, 4] == [8, 16, 4, 7]