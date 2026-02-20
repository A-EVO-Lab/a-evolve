──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 2
Num Failed Tests : 3
Num Total  Tests : 5
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match venmo.Friendship
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
obtain added, deleted friendships using models.changed_records
and assert friend_ids of the added ones match
private_data.to_befriend_venmo_user_ids (ignore_order)
```python
with test(
    """
    obtain added, deleted friendships using models.changed_records
    and assert friend_ids of the added ones match
private_data.to_befriend_venmo_user_ids (ignore_order)
    """
):
    added_friendships, _, deleted_friendships =
models.changed_records("venmo.Friendship")
    added_friend_ids = list_of(added_friendships, "friend_id")
```
----------
AssertionError:  [21, 22, 24, 25, 35, 43, 47, 65, 83, 90, 92, 95, 97] == [83,
90, 92, 95]

In left but not right:
[21, 22, 24, 25, 35, 43, 47, 65, 97]

Original values:
[43, 95, 90, 83, 92, 35, 97, 21, 22, 47, 65, 24, 25] == [90, 95, 92, 83]
>> Failed Requirement
assert friend_ids of the deleted ones match
private_data.to_unfriend_venmo_user_ids (ignore_order)
```python
with test(
    """
    assert friend_ids of the deleted ones match
private_data.to_unfriend_venmo_user_ids (ignore_order)
    """
):
    deleted_friend_ids = list_of(deleted_friendships, "friend_id")
    test.case(
        deleted_friend_ids, "==", private_data.to_unfriend_venmo_user_ids,
ignore_order=True
```
----------
AssertionError:  [] == [10, 34, 72, 103, 104]

In right but not left:
[10, 34, 72, 103, 104]

Original values:
[] == [72, 103, 34, 104, 10]
>> Failed Requirement
assert all main_user's friends on venmo match
private_data.all_friends_venmo_user_ids in the end state (ignore_order)
```python
with test(
    """
    assert all main_user's friends on venmo match
    private_data.all_friends_venmo_user_ids in the end state (ignore_order)
    """
):
    venmo_user = models.end.venmo.User.find_from(main_user)
    venmo_friend_ids = list_of(venmo_user.friends, "id")
```
----------
AssertionError:
[10, 21, 22, 24, 25, 34, 35, 43, 47, 65, 72, 83, 90, 91, 92, 95, 96, 97, 101,
103, 104]
==
[83, 90, 91, 92, 95, 96, 101]

In left but not right:
[10, 21, 22, 24, 25, 34, 35, 43, 47, 65, 72, 97, 103, 104]

Original values:
[96, 101, 91, 72, 103, 34, 104, 10, 92, 47, 90, 35, 97, 21, 22, 43, 95, 65, 25,
83, 24]
==
[96, 101, 91, 90, 95, 92, 83]