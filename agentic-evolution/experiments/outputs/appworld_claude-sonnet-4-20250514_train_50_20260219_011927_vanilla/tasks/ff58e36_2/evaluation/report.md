──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 5
Num Failed Tests : 1
Num Total  Tests : 6
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
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
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert all of private_data.to_befriend_venmo_user_ids_2 are in
newly_befriended_venmo_user_ids.
```python
with test(
    """
    assert all of private_data.to_befriend_venmo_user_ids_2 are in
newly_befriended_venmo_user_ids.
    """
):
    test.case(
        private_data.to_befriend_venmo_user_ids_2,
        "all in",
```
----------
AssertionError:  78 in [68, 49, 92, 51, 35, 79]