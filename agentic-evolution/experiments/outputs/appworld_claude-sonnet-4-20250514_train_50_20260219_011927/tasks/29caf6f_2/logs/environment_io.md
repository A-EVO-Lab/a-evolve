
### Environment Interaction 1
----------------------------------------------------------------------------
```python
# Check available apps to understand what's available
print(apis.api_docs.show_app_descriptions())
```

```
[
 {
  "name": "api_docs",
  "description": "An app to search and explore API documentation."
 },
 {
  "name": "supervisor",
  "description": "An app to access supervisor's personal information, account credentials, addresses, payment cards, and manage the assigned task."
 },
 {
  "name": "amazon",
  "description": "An online shopping app to buy products and manage orders, returns, etc."
 },
 {
  "name": "phone",
  "description": "An app to find and manage contact information for friends, family members, etc., send and receive messages, and manage alarms."
 },
 {
  "name": "file_system",
  "description": "A file system app to create and manage files and folders."
 },
 {
  "name": "spotify",
  "description": "A music streaming app to stream songs and manage song, album and playlist libraries."
 },
 {
  "name": "venmo",
  "description": "A social payment app to send, receive and request money to and from others."
 },
 {
  "name": "gmail",
  "description": "An email app to draft, send, receive, and manage emails."
 },
 {
  "name": "splitwise",
  "description": "A bill splitting app to track and split expenses with people."
 },
 {
  "name": "simple_note",
  "description": "A note-taking app to create and manage notes"
 },
 {
  "name": "todoist",
  "description": "A task management app to manage todo lists and collaborate on them with others."
 }
]
```


### Environment Interaction 2
----------------------------------------------------------------------------
```python
# Check Simple Note APIs to understand how to access notes
print(apis.api_docs.show_api_descriptions(app_name='simple_note'))
```

```
[
 {
  "name": "show_account",
  "description": "Show your account information. Unlike show_profile, this includes private information."
 },
 {
  "name": "signup",
  "description": "Sign up to create account."
 },
 {
  "name": "delete_account",
  "description": "Delete your account."
 },
 {
  "name": "update_account_name",
  "description": "Update your first or last name in the account profile."
 },
 {
  "name": "login",
  "description": "Login to your account."
 },
 {
  "name": "logout",
  "description": "Logout from your account."
 },
 {
  "name": "send_verification_code",
  "description": "Send account verification code to your email address."
 },
 {
  "name": "verify_account",
  "description": "Verify your account using the verification code sent to your email address."
 },
 {
  "name": "send_password_reset_code",
  "description": "Send password reset code to your email address."
 },
 {
  "name": "reset_password",
  "description": "Reset your password using the password reset code sent to your email address."
 },
 {
  "name": "show_profile",
  "description": "Show public profile information of a user."
 },
 {
  "name": "search_notes",
  "description": "Search your notes. This will not show contents of the notes. Pinned notes will be shown first by default, except when dont_reorder_pinned is true."
 },
 {
  "name": "create_note",
  "description": "Create a new note."
 },
 {
  "name": "show_note",
  "description": "Show detailed information of a note, including its content."
 },
 {
  "name": "delete_note",
  "description": "Delete a note."
 },
 {
  "name": "update_note",
  "description": "Update a note's title, content, tags, and/or pinned status."
 },
 {
  "name": "add_content_to_note",
  "description": "Append or prepend content to a note."
 }
]
```


### Environment Interaction 3
----------------------------------------------------------------------------
```python
# Check phone APIs to understand how to send messages
print(apis.api_docs.show_api_descriptions(app_name='phone'))
```

```
[
 {
  "name": "show_account",
  "description": "Show your account information. Unlike show_profile, this includes private information."
 },
 {
  "name": "signup",
  "description": "Sign up to create account."
 },
 {
  "name": "delete_account",
  "description": "Delete your account."
 },
 {
  "name": "update_account_name",
  "description": "Update your first or last name in the account profile."
 },
 {
  "name": "login",
  "description": "Login to your account."
 },
 {
  "name": "logout",
  "description": "Logout from your account."
 },
 {
  "name": "send_password_reset_code",
  "description": "Send password reset code to your phone number."
 },
 {
  "name": "reset_password",
  "description": "Reset your password using the password reset code sent to your email address."
 },
 {
  "name": "show_profile",
  "description": "Show public profile information of a user."
 },
 {
  "name": "show_contact_relationships",
  "description": "Get a list of all relationships available in your contact book."
 },
 {
  "name": "search_contacts",
  "description": "Search your contact book for relatives' information."
 },
 {
  "name": "add_contact",
  "description": "Add a new contact."
 },
 {
  "name": "delete_contact",
  "description": "Delete contact information."
 },
 {
  "name": "update_contact",
  "description": "Update contact information."
 },
 {
  "name": "show_text_message_window",
  "description": "Show text messages with a contact around a given date and time."
 },
 {
  "name": "search_text_messages",
  "description": "Show or search your text messages."
 },
 {
  "name": "show_text_message",
  "description": "Show text message details."
 },
 {
  "name": "delete_text_message",
  "description": "Delete a text message."
 },
 {
  "name": "send_text_message",
  "description": "Send a text message on the given phone number."
 },
 {
  "name": "show_alarms",
  "description": "Get a list of alarms."
 },
 {
  "name": "create_alarm",
  "description": "Create a new alarm."
 },
 {
  "name": "show_alarm",
  "description": "Show alarm details."
 },
 {
  "name": "delete_alarm",
  "description": "Delete an alarm."
 },
 {
  "name": "update_alarm",
  "description": "Update an alarm's settings."
 },
 {
  "name": "show_voice_message_window",
  "description": "Show voice messages with a contact around a given date and time."
 },
 {
  "name": "search_voice_messages",
  "description": "Show or search text voice_messages between the user and a contact."
 },
 {
  "name": "show_voice_message",
  "description": "Show voice message details."
 },
 {
  "name": "delete_voice_message",
  "description": "Delete a voice message."
 },
 {
  "name": "send_voice_message",
  "description": "Send a voice message on the given phone number."
 },
 {
  "name": "get_current_date_and_time",
  "description": "Show current date and time."
 }
]
```


### Environment Interaction 4
----------------------------------------------------------------------------
```python
# Get account credentials to see what login information I have
print(apis.api_docs.show_api_doc(app_name='supervisor', api_name='show_account_passwords'))
```

```
{
 "app_name": "supervisor",
 "api_name": "show_account_passwords",
 "path": "/account_passwords",
 "method": "GET",
 "description": "Show your supervisor's app account passwords.",
 "parameters": [],
 "response_schemas": {
  "success": [
   {
    "account_name": "string",
    "password": "string"
   }
  ],
  "failure": {
   "message": "string"
  }
 }
}
```


### Environment Interaction 5
----------------------------------------------------------------------------
```python
# Get my account credentials to see what services I have access to
response = apis.supervisor.show_account_passwords()
print(response)
```

```
[
 {
  "account_name": "amazon",
  "password": "WH=L{FS"
 },
 {
  "account_name": "file_system",
  "password": "hOodwkS"
 },
 {
  "account_name": "gmail",
  "password": "x&xcoCg"
 },
 {
  "account_name": "phone",
  "password": "2I1#6ld"
 },
 {
  "account_name": "simple_note",
  "password": "aVfy)Rr"
 },
 {
  "account_name": "splitwise",
  "password": "KqI[fbx"
 },
 {
  "account_name": "spotify",
  "password": "+=)+0kx"
 },
 {
  "account_name": "todoist",
  "password": "o1Dv3KT"
 },
 {
  "account_name": "venmo",
  "password": "{{YH7SV"
 }
]
```


### Environment Interaction 6
----------------------------------------------------------------------------
```python
# Login to Simple Note using my email and password
response = apis.simple_note.login(email="gl.moore@gmail.com", password="aVfy)Rr")
print(response)
```

```
Execution failed. Traceback:
  File "<python-input>", line 2, in <module>
    response = apis.simple_note.login(email="gl.moore@gmail.com", password="aVfy)Rr")
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Exception: Response status code is 422:
{"message":"Validation error. Reason: \nusername: field required"}
```


### Environment Interaction 7
----------------------------------------------------------------------------
```python
# Check the login API specification for Simple Note
print(apis.api_docs.show_api_doc(app_name='simple_note', api_name='login'))
```

```
{
 "app_name": "simple_note",
 "api_name": "login",
 "path": "/auth/token",
 "method": "POST",
 "description": "Login to your account.",
 "parameters": [
  {
   "name": "username",
   "type": "string",
   "required": true,
   "description": "Your account email.",
   "default": null,
   "constraints": []
  },
  {
   "name": "password",
   "type": "string",
   "required": true,
   "description": "Your account password.",
   "default": null,
   "constraints": []
  }
 ],
 "response_schemas": {
  "success": {
   "access_token": "string",
   "token_type": "string"
  },
  "failure": {
   "message": "string"
  }
 }
}
```


### Environment Interaction 8
----------------------------------------------------------------------------
```python
# Login to Simple Note using username (which is the email) and password
response = apis.simple_note.login(username="gl.moore@gmail.com", password="aVfy)Rr")
print(response)
```

```
{
 "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzaW1wbGVfbm90ZStnbC5tb29yZUBnbWFpbC5jb20iLCJleHAiOjE2ODQ0MTIwOTh9.4N3-gAMhJZOc9-oXaFfJugUPnIytNIdQd3vqPcYOCSA",
 "token_type": "Bearer"
}
```


### Environment Interaction 9
----------------------------------------------------------------------------
```python
# Store the access token for future API calls
access_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzaW1wbGVfbm90ZStnbC5tb29yZUBnbWFpbC5jb20iLCJleHAiOjE2ODQ0MTIwOTh9.4N3-gAMhJZOc9-oXaFfJugUPnIytNIdQd3vqPcYOCSA"

# Search for notes that might contain movie recommendations
response = apis.simple_note.search_notes(access_token=access_token)
print(response)
```

```
[
 {
  "note_id": 1565,
  "title": "Habit Tracking Log for 2023-05-17",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-17T20:18:19",
  "updated_at": "2023-05-17T20:18:19",
  "pinned": true
 },
 {
  "note_id": 1566,
  "title": "Habit Tracking Log for 2023-05-16",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-16T22:15:05",
  "updated_at": "2023-05-16T22:15:05",
  "pinned": true
 },
 {
  "note_id": 1567,
  "title": "Habit Tracking Log for 2023-05-15",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-15T21:02:51",
  "updated_at": "2023-05-15T21:02:51",
  "pinned": true
 },
 {
  "note_id": 1568,
  "title": "Habit Tracking Log for 2023-05-14",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-14T22:20:18",
  "updated_at": "2023-05-14T22:20:18",
  "pinned": true
 },
 {
  "note_id": 1569,
  "title": "Habit Tracking Log for 2023-05-13",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-13T22:27:22",
  "updated_at": "2023-05-13T22:27:22",
  "pinned": true
 }
]
```


### Environment Interaction 10
----------------------------------------------------------------------------
```python
# Check the search_notes API to see if I can search by keywords
print(apis.api_docs.show_api_doc(app_name='simple_note', api_name='search_notes'))
```

```
{
 "app_name": "simple_note",
 "api_name": "search_notes",
 "path": "/notes",
 "method": "GET",
 "description": "Search your notes. This will not show contents of the notes. Pinned notes will be shown first by default, except when dont_reorder_pinned is true.",
 "parameters": [
  {
   "name": "access_token",
   "type": "string",
   "required": true,
   "description": "Access token obtained from simple_note app login.",
   "default": null,
   "constraints": []
  },
  {
   "name": "query",
   "type": "string",
   "required": false,
   "description": "Search query for the notes.",
   "default": "",
   "constraints": []
  },
  {
   "name": "tags",
   "type": "list[string]",
   "required": false,
   "description": "Tags to filter the notes by. Notes not having any of these tags will be filtered out.",
   "default": null,
   "constraints": []
  },
  {
   "name": "pinned",
   "type": "boolean",
   "required": false,
   "description": "Filter notes by pinned status.",
   "default": null,
   "constraints": []
  },
  {
   "name": "dont_reorder_pinned",
   "type": "boolean",
   "required": false,
   "description": "If true, pinned notes will not be reordered to be shown at the top.",
   "default": null,
   "constraints": []
  },
  {
   "name": "page_index",
   "type": "integer",
   "required": false,
   "description": "The index of the page to return.",
   "default": 0,
   "constraints": [
    "value >= 0.0"
   ]
  },
  {
   "name": "page_limit",
   "type": "integer",
   "required": false,
   "description": "The maximum number of results to return per page.",
   "default": 5,
   "constraints": [
    "value >= 1.0, <= 20.0"
   ]
  },
  {
   "name": "sort_by",
   "type": "string",
   "required": false,
   "description": "The attribute to sort the notes by prefixed with +/- to reflect ascending/descending. Valid attributes: created_at and updated_at. If both query and sort_by are given and non-empty, results will be first ranked by query relevance, then paginated, and will then be sorted by the given attribute within each page. If both query and sort_by are not given, null, or empty, sort_by will default to -updated_at.",
   "default": null,
   "constraints": []
  }
 ],
 "response_schemas": {
  "success": [
   {
    "note_id": 1,
    "title": "string",
    "tags": [
     "string"
    ],
    "created_at": "2019-01-01T00:00:00",
    "updated_at": "2019-01-01T00:00:00",
    "pinned": true
   }
  ],
  "failure": {
   "message": "string"
  }
 }
}
```


### Environment Interaction 11
----------------------------------------------------------------------------
```python
# Search for notes containing "movie" keyword
response = apis.simple_note.search_notes(access_token=access_token, query="movie", page_limit=20)
print(response)
```

```
[
 {
  "note_id": 1561,
  "title": "Inspirational Quotes Collection",
  "tags": [
   "quotes"
  ],
  "created_at": "2022-06-11T05:09:35",
  "updated_at": "2022-06-11T05:09:35",
  "pinned": true
 },
 {
  "note_id": 1564,
  "title": "My Bucket List ([x] = done, [ ] = not done))",
  "tags": [
   "life"
  ],
  "created_at": "2022-08-14T14:14:11",
  "updated_at": "2022-08-14T14:14:11",
  "pinned": true
 },
 {
  "note_id": 1565,
  "title": "Habit Tracking Log for 2023-05-17",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-17T20:18:19",
  "updated_at": "2023-05-17T20:18:19",
  "pinned": true
 },
 {
  "note_id": 1566,
  "title": "Habit Tracking Log for 2023-05-16",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-16T22:15:05",
  "updated_at": "2023-05-16T22:15:05",
  "pinned": true
 },
 {
  "note_id": 1567,
  "title": "Habit Tracking Log for 2023-05-15",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-15T21:02:51",
  "updated_at": "2023-05-15T21:02:51",
  "pinned": true
 },
 {
  "note_id": 1568,
  "title": "Habit Tracking Log for 2023-05-14",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-14T22:20:18",
  "updated_at": "2023-05-14T22:20:18",
  "pinned": true
 },
 {
  "note_id": 1569,
  "title": "Habit Tracking Log for 2023-05-13",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-13T22:27:22",
  "updated_at": "2023-05-13T22:27:22",
  "pinned": true
 },
 {
  "note_id": 1570,
  "title": "Habit Tracking Log for 2023-05-12",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-12T21:50:28",
  "updated_at": "2023-05-12T21:50:28",
  "pinned": true
 },
 {
  "note_id": 1571,
  "title": "Habit Tracking Log for 2023-05-11",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-11T22:44:42",
  "updated_at": "2023-05-11T22:44:42",
  "pinned": true
 },
 {
  "note_id": 1572,
  "title": "Habit Tracking Log for 2023-05-10",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-10T21:05:40",
  "updated_at": "2023-05-10T21:05:40",
  "pinned": true
 },
 {
  "note_id": 1573,
  "title": "Habit Tracking Log for 2023-05-09",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-09T22:10:55",
  "updated_at": "2023-05-09T22:10:55",
  "pinned": true
 },
 {
  "note_id": 1574,
  "title": "Habit Tracking Log for 2023-05-08",
  "tags": [
   "habit-tracker"
  ],
  "created_at": "2023-05-08T21:52:46",
  "updated_at": "2023-05-08T21:52:46",
  "pinned": true
 },
 {
  "note_id": 1563,
  "title": "Movie Quotes Collection",
  "tags": [
   "quotes"
  ],
  "created_at": "2022-11-02T02:02:06",
  "updated_at": "2022-11-02T02:02:06",
  "pinned": false
 },
 {
  "note_id": 1556,
  "title": "Movie Recommendations",
  "tags": [
   "leisure",
   "list"
  ],
  "created_at": "2022-08-12T12:21:39",
  "updated_at": "2022-08-12T12:21:39",
  "pinned": false
 },
 {
  "note_id": 1558,
  "title": "Gift Ideas for Various Occasions",
  "tags": [
   "shopping",
   "list"
  ],
  "created_at": "2022-09-02T10:43:19",
  "updated_at": "2022-09-02T10:43:19",
  "pinned": false
 },
 {
  "note_id": 1555,
  "title": "Book Reading Lists",
  "tags": [
   "leisure",
   "list"
  ],
  "created_at": "2022-07-11T07:54:42",
  "updated_at": "2022-07-11T07:54:42",
  "pinned": false
 },
 {
  "note_id": 1557,
  "title": "Grocery List",
  "tags": [
   "household",
   "list"
  ],
  "created_at": "2022-05-25T22:48:52",
  "updated_at": "2022-05-25T22:48:52",
  "pinned": false
 },
 {
  "note_id": 1559,
  "title": "Weekly Workout Plan",
  "tags": [
   "health"
  ],
  "created_at": "2022-06-22T17:33:37",
  "updated_at": "2022-06-22T17:33:37",
  "pinned": false
 },
 {
  "note_id": 1560,
  "title": "Food Recipes",
  "tags": [
   "cooking"
  ],
  "created_at": "2022-08-30T19:04:51",
  "updated_at": "2022-08-30T19:04:51",
  "pinned": false
 },
 {
  "note_id": 1562,
  "title": "Funny Quotes Collection",
  "tags": [
   "quotes"
  ],
  "created_at": "2022-09-01T10:53:54",
  "updated_at": "2022-09-01T10:53:54",
  "pinned": false
 }
]
```


### Environment Interaction 12
----------------------------------------------------------------------------
```python
# Get the details of the Movie Recommendations note
response = apis.simple_note.show_note(access_token=access_token, note_id=1556)
print(response)
```

```
{
 "note_id": 1556,
 "title": "Movie Recommendations",
 "content": "# Movie Recommendations\n\nInterstellar\n - director: Christopher Nolan\n - genre: Sci-Fi, Drama\n\nInception\n - director: Christopher Nolan\n - genre: Sci-Fi, Action\n\nBlack Swan\n - director: Darren Aronofsky\n - genre: Drama, Thriller\n\nThe Shawshank Redemption\n - director: Frank Darabont\n - genre: Drama\n\nThe Wolf of Wall Street\n - director: Martin Scorsese\n - genre: Biography, Crime, Drama\n\nDrive\n - director: Nicolas Winding Refn\n - genre: Crime, Drama\n\nHer\n - director: Spike Jonze\n - genre: Drama, Romance, Sci-Fi\n\nThe Dark Knight Rises\n - director: Christopher Nolan\n - genre: Action, Crime\n\nThe Big Lebowski\n - director: Joel and Ethan Coen\n - genre: Comedy, Crime\n\nThe Dark Knight\n - director: Christopher Nolan\n - genre: Action, Crime\n\nDjango Unchained\n - director: Quentin Tarantino\n - genre: Drama, Western\n\nInception\n - director: Christopher Nolan\n - genre: Sci-Fi, Action\n\nGladiator\n - director: Ridley Scott\n - genre: Action, Drama\n\nFight Club\n - director: David Fincher\n - genre: Drama\n\nThe Revenant\n - director: Alejandro Gonz\u00e1lez I\u00f1\u00e1rritu\n - genre: Action, Adventure, Drama\n\nPulp Fiction\n - director: Quentin Tarantino\n - genre: Crime, Drama\n\nGood Will Hunting\n - director: Gus Van Sant\n - genre: Drama\n\nAvatar\n - director: James Cameron\n - genre: Action, Adventure, Fantasy\n\nThe Dark Knight Rises\n - director: Christopher Nolan\n - genre: Action, Crime\n\nSpirited Away\n - director: Hayao Miyazaki\n - genre: Animation, Adventure, Family\n\nForrest Gump\n - director: Robert Zemeckis\n - genre: Drama, Romance\n\nA Beautiful Mind\n - director: Ron Howard\n - genre: Biography, Drama\n\nThe Green Mile\n - director: Frank Darabont\n - genre: Crime, Drama, Fantasy",
 "tags": [
  "leisure",
  "list"
 ],
 "created_at": "2022-08-12T12:21:39",
 "updated_at": "2022-08-12T12:21:39",
 "pinned": false
}
```


### Environment Interaction 13
----------------------------------------------------------------------------
```python
import re

# Extract movie titles from the content
content = """# Movie Recommendations

Interstellar
 - director: Christopher Nolan
 - genre: Sci-Fi, Drama

Inception
 - director: Christopher Nolan
 - genre: Sci-Fi, Action

Black Swan
 - director: Darren Aronofsky
 - genre: Drama, Thriller

The Shawshank Redemption
 - director: Frank Darabont
 - genre: Drama

The Wolf of Wall Street
 - director: Martin Scorsese
 - genre: Biography, Crime, Drama

Drive
 - director: Nicolas Winding Refn
 - genre: Crime, Drama

Her
 - director: Spike Jonze
 - genre: Drama, Romance, Sci-Fi

The Dark Knight Rises
 - director: Christopher Nolan
 - genre: Action, Crime

The Big Lebowski
 - director: Joel and Ethan Coen
 - genre: Comedy, Crime

The Dark Knight
 - director: Christopher Nolan
 - genre: Action, Crime

Django Unchained
 - director: Quentin Tarantino
 - genre: Drama, Western

Inception
 - director: Christopher Nolan
 - genre: Sci-Fi, Action

Gladiator
 - director: Ridley Scott
 - genre: Action, Drama

Fight Club
 - director: David Fincher
 - genre: Drama

The Revenant
 - director: Alejandro González Iñárritu
 - genre: Action, Adventure, Drama

Pulp Fiction
 - director: Quentin Tarantino
 - genre: Crime, Drama

Good Will Hunting
 - director: Gus Van Sant
 - genre: Drama

Avatar
 - director: James Cameron
 - genre: Action, Adventure, Fantasy

The Dark Knight Rises
 - director: Christopher Nolan
 - genre: Action, Crime

Spirited Away
 - director: Hayao Miyazaki
 - genre: Animation, Adventure, Family

Forrest Gump
 - director: Robert Zemeckis
 - genre: Drama, Romance

A Beautiful Mind
 - director: Ron Howard
 - genre: Biography, Drama

The Green Mile
 - director: Frank Darabont
 - genre: Crime, Drama, Fantasy"""

# Split content into lines and find movie titles (lines that don't start with # or -)
lines = content.split('\n')
movie_titles = []

for line in lines:
    line = line.strip()
    # Skip empty lines, headers, and lines starting with -
    if line and not line.startswith('#') and not line.startswith('-'):
        movie_titles.append(line)

# Remove duplicates while preserving order
seen = set()
unique_movies = []
for movie in movie_titles:
    if movie not in seen:
        seen.add(movie)
        unique_movies.append(movie)

# Create comma-separated list
movie_list = ", ".join(unique_movies)
print("Movie titles extracted:")
print(movie_list)
```

```
Movie titles extracted:
Interstellar, Inception, Black Swan, The Shawshank Redemption, The Wolf of Wall Street, Drive, Her, The Dark Knight Rises, The Big Lebowski, The Dark Knight, Django Unchained, Gladiator, Fight Club, The Revenant, Pulp Fiction, Good Will Hunting, Avatar, Spirited Away, Forrest Gump, A Beautiful Mind, The Green Mile
```


### Environment Interaction 14
----------------------------------------------------------------------------
```python
# Login to phone app
response = apis.phone.login(username="gl.moore@gmail.com", password="2I1#6ld")
print(response)
```

```
Execution failed. Traceback:
  File "<python-input>", line 2, in <module>
    response = apis.phone.login(username="gl.moore@gmail.com", password="2I1#6ld")
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Exception: Response status code is 401:
{"message":"Invalid credentials"}
```


### Environment Interaction 15
----------------------------------------------------------------------------
```python
# Check the phone login API specification
print(apis.api_docs.show_api_doc(app_name='phone', api_name='login'))
```

```
{
 "app_name": "phone",
 "api_name": "login",
 "path": "/auth/token",
 "method": "POST",
 "description": "Login to your account.",
 "parameters": [
  {
   "name": "username",
   "type": "string",
   "required": true,
   "description": "Your account phone_number.",
   "default": null,
   "constraints": []
  },
  {
   "name": "password",
   "type": "string",
   "required": true,
   "description": "Your account password.",
   "default": null,
   "constraints": []
  }
 ],
 "response_schemas": {
  "success": {
   "access_token": "string",
   "token_type": "string"
  },
  "failure": {
   "message": "string"
  }
 }
}
```


### Environment Interaction 16
----------------------------------------------------------------------------
```python
# Login to phone app using phone number as username
response = apis.phone.login(username="8346699249", password="2I1#6ld")
print(response)
```

```
{
 "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJwaG9uZSs4MzQ2Njk5MjQ5IiwiZXhwIjoxNjg0NDEyNzQxfQ.JEphpxe1fqynsS8I4dHFxxTG8HsIW7x4g1-NVu-zUgk",
 "token_type": "Bearer"
}
```


### Environment Interaction 17
----------------------------------------------------------------------------
```python
# Store phone access token and search for Laura in contacts
phone_access_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJwaG9uZSs4MzQ2Njk5MjQ5IiwiZXhwIjoxNjg0NDEyNzQxfQ.JEphpxe1fqynsS8I4dHFxxTG8HsIW7x4g1-NVu-zUgk"

# Search for Laura in contacts
response = apis.phone.search_contacts(access_token=phone_access_token, query="Laura")
print(response)
```

```
[
 {
  "contact_id": 1106,
  "first_name": "Laura",
  "last_name": "Mccoy",
  "email": "la-mcco@gmail.com",
  "phone_number": "2873148336",
  "relationships": [
   "coworker"
  ],
  "birthday": "1953-09-11",
  "home_address": "3928 Justin Bridge Suite 149\nSeattle\nWashington\nUnited States\n59253",
  "work_address": "80547 Cynthia Square Suite 842\nSeattle\nWashington\nUnited States\n78964",
  "created_at": "2023-02-05T18:39:57"
 },
 {
  "contact_id": 1107,
  "first_name": "Christina",
  "last_name": "Harrison",
  "email": "chrharrison@gmail.com",
  "phone_number": "7487401121",
  "relationships": [
   "coworker"
  ],
  "birthday": "1957-09-17",
  "home_address": "5918 Johnson Land Suite 815\nSeattle\nWashington\nUnited States\n18995",
  "work_address": "80547 Cynthia Square Suite 842\nSeattle\nWashington\nUnited States\n78964",
  "created_at": "2023-02-05T18:39:57"
 },
 {
  "contact_id": 1108,
  "first_name": "Vickie",
  "last_name": "Powell",
  "email": "vicpowe@gmail.com",
  "phone_number": "8924356613",
  "relationships": [
   "coworker"
  ],
  "birthday": "1959-01-26",
  "home_address": "6989 Kimberly Grove Suite 892\nSeattle\nWashington\nUnited States\n11726",
  "work_address": "80547 Cynthia Square Suite 842\nSeattle\nWashington\nUnited States\n78964",
  "created_at": "2023-02-05T18:39:57"
 },
 {
  "contact_id": 1109,
  "first_name": "Caitlin",
  "last_name": "Burch",
  "email": "caiburc@gmail.com",
  "phone_number": "5896230193",
  "relationships": [
   "manager",
   "coworker"
  ],
  "birthday": "1962-09-05",
  "home_address": "4380 Sosa Orchard Suite 194\nSeattle\nWashington\nUnited States\n73131",
  "work_address": "80547 Cynthia Square Suite 842\nSeattle\nWashington\nUnited States\n78964",
  "created_at": "2023-02-05T18:39:57"
 },
 {
  "contact_id": 1110,
  "first_name": "Jacob",
  "last_name": "Blackburn",
  "email": "jac-blackburn@gmail.com",
  "phone_number": "7375474944",
  "relationships": [
   "coworker"
  ],
  "birthday": "1953-08-20",
  "home_address": "8705 Jonathan Corners Suite 675\nSeattle\nWashington\nUnited States\n94623",
  "work_address": "80547 Cynthia Square Suite 842\nSeattle\nWashington\nUnited States\n78964",
  "created_at": "2023-02-05T18:39:57"
 }
]
```


### Environment Interaction 18
----------------------------------------------------------------------------
```python
# Send text message to Laura with movie recommendations
movie_recommendations = "Interstellar, Inception, Black Swan, The Shawshank Redemption, The Wolf of Wall Street, Drive, Her, The Dark Knight Rises, The Big Lebowski, The Dark Knight, Django Unchained, Gladiator, Fight Club, The Revenant, Pulp Fiction, Good Will Hunting, Avatar, Spirited Away, Forrest Gump, A Beautiful Mind, The Green Mile"

response = apis.phone.send_text_message(
    access_token=phone_access_token,
    phone_number="2873148336",
    message=movie_recommendations
)
print(response)
```

```
{"message": "Text message sent.", "text_message_id": 16803}
```


### Environment Interaction 19
----------------------------------------------------------------------------
```python
# Complete the task
apis.supervisor.complete_task()
```

```
Execution successful.
```

