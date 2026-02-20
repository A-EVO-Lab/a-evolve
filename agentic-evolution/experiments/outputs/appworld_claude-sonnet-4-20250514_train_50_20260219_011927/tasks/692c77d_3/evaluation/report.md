──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 6
Num Failed Tests : 1
Num Total  Tests : 7
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match spotify.SongReview.
>> Passed Requirement
obtain added, updated, removed spotify.SongReview records using
models.changed_records,
and assert 0 have been removed.
>> Passed Requirement
assert song_ids of the updated song reviews match
private_data.to_update_review_song_ids (ignore_order)
>> Passed Requirement
assert song_ids of the added song reviews match
private_data.to_add_review_song_ids (ignore_order)
>> Passed Requirement
assert all added_review_song_ids have rating of public_data.target_rating.
>> Passed Requirement
assert all updated_review_song_ids have rating of public_data.target_rating.
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
'successfully gave 4-star ratings to all 20 liked songs in the spotify album
library. updated 3 existing lower ratings to 4 stars and created 17 new 4-star
reviews.'
==
'null'