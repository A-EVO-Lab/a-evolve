──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 7
Num Failed Tests : 2
Num Total  Tests : 9
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match venmo.Transaction, venmo.TransactionLike,
venmo.TransactionComment
>> Passed Requirement
obtain added, updated, removed venmo.Transaction records using
models.changed_records,
and assert 0 transactions were added or removed.
>> Passed Requirement
assert updated transactions have only changed in like_count field using
models.changed_field_names.
>> Passed Requirement
obtain added, removed venmo.TransactionLike records using
models.changed_records,
and assert 0 transaction likes were removed.
>> Passed Requirement
obtain added, updated, removed venmo.TransactionComment records using
models.changed_records,
and assert 0 transaction comments were updated or removed.
>> Passed Requirement
assert all added transaction comments have comment public_data.comment
(normalize_text=True)
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert transaction_ids of added transaction likes match exactly to
private_data.after_threshold_contact_transaction_ids (ignore order).
```python
with test(
    """
    assert transaction_ids of added transaction likes match exactly to
    private_data.after_threshold_contact_transaction_ids (ignore order).
    """
):
    added_transaction_like_ids = list_of(added_transaction_likes,
"transaction_id")
    test.case(
```
----------
AssertionError:
[8246, 8248, 8251, 8252, 8264, 8266]
==
[8246, 8247, 8248, 8251, 8252, 8263, 8264, 8266, 8267]

In right but not left:
[8247, 8263, 8267]

Original values:
[8266, 8248, 8251, 8246, 8264, 8252]
==
[8246, 8247, 8248, 8251, 8252, 8263, 8264, 8266, 8267]
>> Failed Requirement
assert set of transaction_ids of added transaction comments match exactly
to private_data.after_threshold_contact_transaction_ids (ignore order).
```python
with test(
    """
    assert set of transaction_ids of added transaction comments match exactly
    to private_data.after_threshold_contact_transaction_ids (ignore order).
    """
):
    added_transaction_comment_ids = list_of(added_transaction_comments,
"transaction_id")
    test.case(
```
----------
AssertionError:
[8246, 8248, 8251, 8252, 8264, 8266]
==
[8246, 8247, 8248, 8251, 8252, 8263, 8264, 8266, 8267]

In right but not left:
[8247, 8263, 8267]

Original values:
[8251, 8246, 8252, 8264, 8266, 8248]
==
[8246, 8247, 8248, 8251, 8252, 8263, 8264, 8266, 8267]