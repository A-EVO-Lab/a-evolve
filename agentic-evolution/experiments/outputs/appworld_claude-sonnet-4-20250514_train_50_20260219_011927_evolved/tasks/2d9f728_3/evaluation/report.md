──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 7
Num Failed Tests : 3
Num Total  Tests : 10
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match venmo.Transaction, venmo.PaymentRequest
>> Passed Requirement
obtain added, updated, removed venmo.Transactions using models.changed_records,
and assert 1 is added, 0 is updated, and 0 is deleted.
>> Passed Requirement
assert the added transaction's receiver_id is
private_data.food_payer_roommate_venmo_user_id.
>> Passed Requirement
assert the added transaction has description
public_data.food_note (normalize_text=True).
>> Passed Requirement
obtain updated, removed venmo.PaymentRequest records using
models.changed_records,
and assert 0 is updated, and 0 is deleted.
>> Passed Requirement
assert all added payment requests have description
public_data.taxi_note (normalize_text=True).
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert the added transaction has amount
public_data.food_cost / (1 + number of roommates) with a tolerance of 0.51
```python
with test(
    """
    assert the added transaction has amount
    public_data.food_cost / (1 + number of roommates) with a tolerance of 0.51
    """
):
    num_roommates = len(private_data.all_roommate_venmo_user_ids)
    expected_food_amount = public_data.food_cost / (1 + num_roommates)
```
----------
AssertionError:  55.33 ± 0.51 == 41.5 ± 0.51

Original values:
55.33 == 41.5
>> Failed Requirement
assert set of receiver ids from added payment requests match set of
private_data.all_roommate_venmo_user_ids
```python
with test(
    """
    assert set of receiver ids from added payment requests match set of
    private_data.all_roommate_venmo_user_ids
    """
):
    added_receiver_ids = list_of(added_payment_requests, "receiver_id")
    test.case(
```
----------
AssertionError:  [58, 95] == [11, 86, 95]

In left but not right:
[58]

In right but not left:
[11, 86]

Original values:
[95, 58] == [86, 11, 95]
>> Failed Requirement
assert all added payment requests have amount
public_data.taxi_cost / (1 + number of roommates) with a tolerance of 0.51.
```python
with test(
    """
    assert all added payment requests have amount
    public_data.taxi_cost / (1 + number of roommates) with a tolerance of 0.51.
    """
):
    expected_taxi_amount = public_data.taxi_cost / (1 + num_roommates)
    test.case(
```
----------
AssertionError:  13.33 ± 0.51 == 10.0 ± 0.51

Original values:
13.33 == 10.0