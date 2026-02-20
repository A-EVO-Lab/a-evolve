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
'no country for old men'
not in
"the lord of the rings: the two towers, no country for old men, forrest gump,
the lord of the rings: the fellowship of the ring, blade runner 2049,
inglourious basterds, the wolf of wall street, pulp fiction, schindler's list,
the matrix, interstellar, inception, her, a beautiful mind, pan's labyrinth, the
lord of the rings: the return of the king, amélie, the godfather: part ii, the
pianist"

Original values:
'No Country for Old Men'
not in
"The Lord of the Rings: The Two Towers, No Country for Old Men, Forrest Gump,
The Lord of the Rings: The Fellowship of the Ring, Blade Runner 2049,
Inglourious Basterds, The Wolf of Wall Street, Pulp Fiction, Schindler's List,
The Matrix, Interstellar, Inception, Her, A Beautiful Mind, Pan's Labyrinth, The
Lord of the Rings: The Return of the King, Amélie, The Godfather: Part II, The
Pianist"