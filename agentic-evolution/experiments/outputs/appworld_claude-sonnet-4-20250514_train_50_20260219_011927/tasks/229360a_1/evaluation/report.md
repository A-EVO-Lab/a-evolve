──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 1
Num Failed Tests : 5
Num Total  Tests : 6
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match spotify.UserLibrarySong, spotify.UserLibraryAlbum.
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
'spotify library cleanup completed. song library now contains only songs that
are liked or downloaded. album library now contains only liked albums (no albums
were fully downloaded). playlist library left unchanged as requested.'
==
'null'
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
AssertionError:  7 == 0
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
[49, 52, 69, 75, 77, 111]
==
[52, 69, 75, 77, 111, 113, 166, 184, 206, 228, 250, 252]

In left but not right:
[49]

In right but not left:
[113, 166, 184, 206, 228, 250, 252]

Original values:
[52, 75, 77, 49, 69, 111]
==
[228, 69, 166, 75, 77, 206, 111, 113, 52, 184, 250, 252]
>> Failed Requirement
obtain added, removed spotify.UserLibraryAlbum records using
models.changed_records,
and assert 0 have been added.
```python
with test(
    """
    obtain added, removed spotify.UserLibraryAlbum records using
models.changed_records,
    and assert 0 have been added.
    """
):
    added_library_albums, _, removed_library_albums = models.changed_records(
        "spotify.UserLibraryAlbum"
```
----------
AssertionError:  3 == 0
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
AssertionError:  [4, 5, 10, 15, 18] == [4, 10, 15, 18]

In left but not right:
[5]

Original values:
[10, 4, 15, 5, 18] == [10, 18, 4, 15]