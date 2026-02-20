──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 4
Num Failed Tests : 2
Num Total  Tests : 6
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match venmo.Transaction, venmo.TransactionLike
>> Passed Requirement
assert all newly liked transaction_ids are in
private_data.recent_transaction_ids.
>> Passed Requirement
assert all newly liked transaction_ids are in
private_data.relative_transaction_ids.
>> Passed Requirement
assert no new venmo.Transaction was added using models.changed_records.
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
AssertionError:  '7' == 'null'
>> Failed Requirement
assert set of all new transaction likes (obtained via models.changed_records)
is identical to set of private_data.recent_relative_transaction_ids.
```python
with test(
    """
    assert set of all new transaction likes (obtained via
models.changed_records)
    is identical to set of private_data.recent_relative_transaction_ids.
    """
):
    added_transaction_likes, _, _ =
models.changed_records("venmo.TransactionLike")
    liked_transaction_ids = set_of(added_transaction_likes, "transaction_id")
```
----------
AssertionError:
{3299, 5224, 8220, 4243, 8218, 8223, 5215}
==
{3299, 5224, 4243, 8223, 8218, 8220, 6909, 5215}

In right but not left:
[6909]