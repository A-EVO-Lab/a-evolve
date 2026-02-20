──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 3
Num Failed Tests : 1
Num Total  Tests : 4
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match spotify.UserArtistFollowing.
>> Passed Requirement
obtain added, removed spotify.UserArtistFollowing records using
models.changed_records
and assert 0 have been removed.
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert artist_ids of the added artist followings match
private_data.to_be_followed_artist_ids.
```python
with test(
    """
    assert artist_ids of the added artist followings match
private_data.to_be_followed_artist_ids.
    """
):
    added_artist_follow_ids = set_of(added_artist_followings, "artist_id")
```
----------
AssertionError:  {18, 13, 22, 15} == {13, 15, 18, 22, 30}

In right but not left:
[30]