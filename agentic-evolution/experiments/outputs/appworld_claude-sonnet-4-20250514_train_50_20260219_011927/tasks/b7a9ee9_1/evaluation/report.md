──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 4
Num Failed Tests : 0
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
>> Passed Requirement
assert artist_ids of the added artist followings match
private_data.to_be_followed_artist_ids.
──────────────────────────────────── Fails ─────────────────────────────────────
None