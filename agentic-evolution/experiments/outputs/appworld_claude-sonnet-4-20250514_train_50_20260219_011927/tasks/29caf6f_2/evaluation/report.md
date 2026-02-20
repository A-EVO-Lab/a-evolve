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
'interstellar, inception, black swan, the shawshank redemption, the wolf of wall
street, drive, her, the dark knight rises, the big lebowski, the dark knight,
django unchained, gladiator, fight club, the revenant, pulp fiction, good will
hunting, avatar, spirited away, forrest gump, a beautiful mind, the green mile'

Original values:
'Interstellar'
not in
'Interstellar, Inception, Black Swan, The Shawshank Redemption, The Wolf of Wall
Street, Drive, Her, The Dark Knight Rises, The Big Lebowski, The Dark Knight,
Django Unchained, Gladiator, Fight Club, The Revenant, Pulp Fiction, Good Will
Hunting, Avatar, Spirited Away, Forrest Gump, A Beautiful Mind, The Green Mile'