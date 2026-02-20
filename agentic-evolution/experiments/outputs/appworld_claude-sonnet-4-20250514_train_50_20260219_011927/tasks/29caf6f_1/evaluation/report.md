──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 7
Num Failed Tests : 1
Num Total  Tests : 8
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
>> Passed Requirement
assert model changes match phone.GlobalTextMessage, phone.UserTextMessage.
>> Passed Requirement
assert 0 phone.UserTextMessage records were removed using
models.changed_records.
>> Passed Requirement
obtain 1 phone.GlobalTextMessage record was added using models.changed_records.
>> Passed Requirement
assert the added global_text_message has
private_data.related_person_phone_user_id as the receiver_id.
>> Passed Requirement
assert the receiver of the added global_text_message is the related_person
corresponding to data.public.related_person_first_name.
>> Passed Requirement
assert that all private_data.director_movie_titles occur as a substring in
added global_text_message (normalize_text=True)
──────────────────────────────────── Fails ─────────────────────────────────────
>> Failed Requirement
assert that none of private_data.non_director_movie_titles occur as a substring
in
added global_text_message (normalize_text=True)
```python
with test(
    """
    assert that none of private_data.non_director_movie_titles occur as a
substring in
    added global_text_message (normalize_text=True)
    """
):
    test.case(
        private_data.non_director_movie_titles,
```
----------
AssertionError:
'interstellar'
not in
"here are my movie recommendations: interstellar, forrest gump, blade runner
2049, no country for old men, schindler's list, the revenant, the wolf of wall
street, the green mile, fight club, pulp fiction, spirited away, the social
network, the godfather, django unchained"

Original values:
'Interstellar'
not in
"Here are my movie recommendations: Interstellar, Forrest Gump, Blade Runner
2049, No Country for Old Men, Schindler's List, The Revenant, The Wolf of Wall
Street, The Green Mile, Fight Club, Pulp Fiction, Spirited Away, The Social
Network, The Godfather, Django Unchained"