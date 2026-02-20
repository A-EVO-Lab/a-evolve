──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 5
Num Failed Tests : 1
Num Total  Tests : 6
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match venmo.Friendship
>> Passed Requirement
obtain all added, and deleted friendships using models.changed_records
and assert none are deleted.
>> Passed Requirement
assert all of added friend ids are in private_data.to_befriend_venmo_user_ids.
>> Passed Requirement
assert all of private_data.to_befriend_venmo_user_ids_1 are in
newly_befriended_venmo_user_ids.
>> Passed Requirement
assert all of private_data.to_befriend_venmo_user_ids_2 are in
newly_befriended_venmo_user_ids.
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
'successfully added 6 friends and roommates to venmo. all 13 contacts are now
friends on venmo (6 newly added, 7 were already friends).'
==
'null'