──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 9
Num Failed Tests : 0
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
assert transaction_ids of added transaction likes match exactly to
private_data.after_threshold_contact_transaction_ids (ignore order).
>> Passed Requirement
obtain added, updated, removed venmo.TransactionComment records using
models.changed_records,
and assert 0 transaction comments were updated or removed.
>> Passed Requirement
assert set of transaction_ids of added transaction comments match exactly
to private_data.after_threshold_contact_transaction_ids (ignore order).
>> Passed Requirement
assert all added transaction comments have comment public_data.comment
(normalize_text=True)
──────────────────────────────────── Fails ─────────────────────────────────────
None