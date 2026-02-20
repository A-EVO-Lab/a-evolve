──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 4
Num Failed Tests : 3
Num Total  Tests : 7
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
obtain added, updated, removed venmo.Transaction records using
models.changed_records,
and assert 0 were updated or removed.
>> Passed Requirement
assert receiver_id_to_amount obtained from added_transactions matches the
data.private.friend_venmo_id_to_money_i_owe.
>> Passed Requirement
assert all transactions have
description "For {data.public.vacation_spot} trip" (normalize_text=True)
>> Passed Requirement
obtain added, updated, removed venmo.PaymentRequest records using
models.changed_records,
and assert 0 were updated or removed.
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
AssertionError:  '<<not_given>>' == 'null'
>> Failed Requirement
assert model changes match venmo.Transaction, venmo.PaymentRequest, venmo.User,
venmo.Notification
```python
with test(
    """
    assert model changes match venmo.Transaction, venmo.PaymentRequest,
venmo.User, venmo.Notification
    """
):
    changed_model_names = models.changed_model_names()
    test.case(
        changed_model_names,
```
----------
AssertionError:
{'venmo.Notification', 'venmo.Transaction', 'venmo.User'}
==
{'venmo.Notification', 'venmo.Transaction', 'venmo.User',
'venmo.PaymentRequest'}

In right but not left:
['venmo.PaymentRequest']
>> Failed Requirement
assert receiver_id_to_amount obtained from added_payment_requests
matches the data.private.friend_venmo_id_to_money_they_owe.
```python
with test(
    """
    assert receiver_id_to_amount obtained from added_payment_requests
    matches the data.private.friend_venmo_id_to_money_they_owe.
    """
):
    receiver_id_to_amount = dict_of(added_payment_requests, "receiver_id",
"amount")
    test.case(
```
----------
AssertionError:  {} == Munch({87: 149, 90: 81, 77: 143})