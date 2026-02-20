──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 6
Num Failed Tests : 4
Num Total  Tests : 10
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert model changes match spotify.User, file_system.File, file_system.Directory
ignoring spotify.SongReview,spotify.UserLibrarySong,
spotify.UserDownloadedSong,spotify.PaymentCard,spotify.User,spotify.AlbumReview,
file_system.Directory,spotify.Album,spotify.Recommendation,spotify.MusicPlayer,
spotify.Playlist,spotify.UserArtistFollowing,spotify.UserLibraryAlbum,spotify.So
ng
spotify.PlaylistReview, spotify.PremiumSubscription
>> Passed Requirement
obtain added, updated, removed file_system.File records using
models.changed_records,
and assert 1 has been added, 0 have been updated or removed.
>> Passed Requirement
assert the added file's path is public_data.backup_file_path.
>> Passed Requirement
parse the content of the file into lines and
assert the first line of the parsed csv lines is "Title, Artists"
with ignore_white_space=True, ignore_case=True.
>> Passed Requirement
assert each song_title in csv_lines is mentioned only once.
>> Passed Requirement
obtain updated, deleted spotify.User records using models.changed_records,
and assert 1 has been deleted, 0 have been added or updated.
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
AssertionError:
'successfully exported 72 unique songs to ~/backups/spotify.csv and terminated
spotify account'
==
'null'
>> Failed Requirement
make a dict of song_title_to_artist_names from the csv_lines and assert it
matches private_data.song_title_to_artist_names (ignore_order, normalize_text).
```python
with test(
    """
    make a dict of song_title_to_artist_names from the csv_lines and assert it
    matches private_data.song_title_to_artist_names (ignore_order,
normalize_text).
    """
):
    song_title_to_artist_names = {
        line.split(",")[0]: line.split(",")[1].strip().split("|") for line in
csv_lines[1:]
```
----------
AssertionError:
{'"a love that never was"': ['"mia sullivan', 'carter knight"'], '"a tale of
unspoken longing"': ['"emily rivers"'], '"astral serenity"': ['"orion steele"'],
'"beneath the stars of reminiscence"': ['"astrid nightshade"'], '"bleeding
sun"': ['"aria sterling"'], '"caught in a web of lies"': ['"hazel winter', 'ava
morgan"'], '"cherry blossom tears"': ['"aria sterling"'], '"cold embrace"':
['"ava morgan"'], '"dancing through the veil of dreams"': ['"noah bennett"'],
'"destiny\'s game"': ['"aria sterling"'], '"distant love"': ['"ava morgan"'],
'"dream\'s end"': ['"mia sullivan"'], '"drifting through eternal skies"':
['"eliana harper"'], '"echoes of the whispering wind"': ['"felix blackwood"'],
'"eclipsed"': ['"aria sterling"'], '"elusive joy"': ['"orion steele"'], '"end of
the line"': ['"violet cascade"'], '"eternal solitude"': ['"hazel winter', 'ava
morgan"'], '"fading glow"': ['"soulful reverie"'], '"falling through the cracks
of time"': ['"astrid nightshade"'], '"fleeting touch"': ['"mia sullivan',
'carter knight"'], '"fragile bonds"': ['"jasper skye"'], '"heartstrings
symphony"': ['"ava morgan"'], '"in the chambers of my mind"': ['"jasper skye"'],
'"in the embrace of midnight"': ['"luna starlight"'], '"in the theater of broken
promises"': ['"isabella cruz"'], '"in the tunnels of despair"': ['"apollo
serenade"'], '"journey through the unknown"': ['"hazel winter', 'ava morgan"'],
'"lonely skies"': ['"aria sterling"'], '"longing for another chance"': ['"aria
sterling"'], '"lost in a sea of echoes"': ['"zoey james"'], '"lost in the
twilight of hope"': ['"felix blackwood"'], '"mortal coil"': ['"zoe mitchell"'],
'"neon reverie"': ['"violet cascade"'], '"on the border of reality"': ['"lily
moon', 'zoey james"'], '"paper planes"': ['"seraphina dawn"'], '"phantom pain"':
['"hazel winter', 'ava morgan"'], '"reflections in a pool of regret"': ['"jasper
skye', 'isabella cruz', 'seraphina dawn"'], '"sailing on the sea of dreams"':
['"silent thunder"'], '"secrets we dare not speak"': ['"noah bennett"'],
'"silent sorrow"': ['"aria sterling"'], '"silver lining"': ['"ava morgan"'],
'"summer\'s end"': ['"ethan wallace"'], '"symphony of the twilight forest"':
['"aria sterling"'], '"tangled lies"': ['"ava morgan"'], '"the complexity of
simple things"': ['"nova harmony"'], '"the curse of loving you"': ['"lucas
grey"'], '"the forgotten pages of time"': ['"luna starlight"'], '"the illusion
of forever"': ['"liam palmer"'], '"the last waltz of a broken heart"':
['"soulful reverie"'], '"the silence after the storm"': ['"lucas diaz"'], '"the
silence between us"': ['"aria sterling"'], '"the silence that speaks volumes"':
['"violet cascade"'], '"the spiral of unending thoughts"': ['"carter knight"'],
'"the threads of a frayed bond"': ['"oceanic odyssey"'], '"the unbearable
lightness of loss"': ['"zoe mitchell"'], '"time\'s hold"': ['"aria sterling"'],
'"torn between two worlds"': ['"oceanic odyssey"'], '"trapped in a daydream"':
['"nova harmony"'], '"under the gaze of a watchful moon"': ['"eliana harper"'],
'"under the willow"': ['"nova harmony"'], '"unearthed secrets"': ['"aria
sterling"'], '"wading through rivers of tears"': ['"noah bennett', 'ethan
wallace', 'nova harmony"'], '"walking through the valley of shadows"': ['"lily
moon', 'zoey james"'], '"wandering the streets alone"': ['"felix blackwood"'],
'"weeping sky"': ['"luna starlight"'], '"when all hope seems lost"':
['"seraphina dawn"'], '"whispers in the wind"': ['"hazel winter"'], '"whispers
of tomorrow"': ['"phoenix rivers"'], '"whispers of the enchanted forest"':
['"aria sterling"'], '"whispers of the heart"': ['"lily moon', 'zoey james"'],
'"whispers of the soul"': ['"mia sullivan', 'carter knight"']}
==
{'silent sorrow': ['aria sterling'], 'in the theater of broken promises':
['isabella cruz'], 'longing for another chance': ['aria sterling'], 'the spiral
of unending thoughts': ['carter knight'], 'lost in a sea of echoes': ['zoey
james'], 'reflections in a pool of regret': ['isabella cruz', 'jasper skye',
'seraphina dawn'], 'the curse of loving you': ['lucas grey'], 'the unbearable
lightness of loss': ['zoe mitchell'], 'mortal coil': ['zoe mitchell'], 'dancing
through the veil of dreams': ['noah bennett'], 'secrets we dare not speak':
['noah bennett'], 'drifting through eternal skies': ['eliana harper'], "dream's
end": ['mia sullivan'], 'the silence after the storm': ['lucas diaz'], 'the
forgotten pages of time': ['luna starlight'], 'wading through rivers of tears':
['ethan wallace', 'noah bennett', 'nova harmony'], 'in the embrace of midnight':
['luna starlight'], 'under the gaze of a watchful moon': ['eliana harper'],
'whispers in the wind': ['hazel winter'], 'weeping sky': ['luna starlight'],
"destiny's game": ['aria sterling'], 'the silence that speaks volumes': ['violet
cascade'], 'end of the line': ['violet cascade'], 'neon reverie': ['violet
cascade'], 'eclipsed': ['aria sterling'], 'symphony of the twilight forest':
['aria sterling'], "time's hold": ['aria sterling'], 'cherry blossom tears':
['aria sterling'], 'whispers of the enchanted forest': ['aria sterling'],
'lonely skies': ['aria sterling'], 'the silence between us': ['aria sterling'],
'bleeding sun': ['aria sterling'], 'unearthed secrets': ['aria sterling'],
'heartstrings symphony': ['ava morgan'], 'tangled lies': ['ava morgan'],
'distant love': ['ava morgan'], 'silver lining': ['ava morgan'], 'in the tunnels
of despair': ['apollo serenade'], 'the complexity of simple things': ['nova
harmony'], 'trapped in a daydream': ['nova harmony'], 'journey through the
unknown': ['ava morgan', 'hazel winter'], 'caught in a web of lies': ['ava
morgan', 'hazel winter'], 'eternal solitude': ['ava morgan', 'hazel winter'],
'phantom pain': ['ava morgan', 'hazel winter'], 'under the willow': ['nova
harmony'], 'falling through the cracks of time': ['astrid nightshade'], 'beneath
the stars of reminiscence': ['astrid nightshade'], "summer's end": ['ethan
wallace'], 'on the border of reality': ['lily moon', 'zoey james'], 'walking
through the valley of shadows': ['lily moon', 'zoey james'], 'whispers of the
heart': ['lily moon', 'zoey james'], 'whispers of the soul': ['carter knight',
'mia sullivan'], 'a love that never was': ['carter knight', 'mia sullivan'],
'fleeting touch': ['carter knight', 'mia sullivan'], 'wandering the streets
alone': ['felix blackwood'], 'echoes of the whispering wind': ['felix
blackwood'], 'lost in the twilight of hope': ['felix blackwood'], 'a tale of
unspoken longing': ['emily rivers'], 'torn between two worlds': ['oceanic
odyssey'], 'whispers of tomorrow': ['phoenix rivers'], 'the threads of a frayed
bond': ['oceanic odyssey'], 'sailing on the sea of dreams': ['silent thunder'],
'in the chambers of my mind': ['jasper skye'], 'fragile bonds': ['jasper skye'],
'the last waltz of a broken heart': ['soulful reverie'], 'fading glow':
['soulful reverie'], 'cold embrace': ['ava morgan'], 'the illusion of forever':
['liam palmer'], 'when all hope seems lost': ['seraphina dawn'], 'paper planes':
['seraphina dawn'], 'elusive joy': ['orion steele'], 'astral serenity': ['orion
steele']}

Original values:
{'"A Love That Never Was"': ['"Mia Sullivan', 'Carter Knight"'], '"A Tale of
Unspoken Longing"': ['"Emily Rivers"'], '"Astral Serenity"': ['"Orion Steele"'],
'"Beneath the Stars of Reminiscence"': ['"Astrid Nightshade"'], '"Bleeding
Sun"': ['"Aria Sterling"'], '"Caught in a Web of Lies"': ['"Hazel Winter', 'Ava
Morgan"'], '"Cherry Blossom Tears"': ['"Aria Sterling"'], '"Cold Embrace"':
['"Ava Morgan"'], '"Dancing Through the Veil of Dreams"': ['"Noah Bennett"'],
'"Destiny\'s Game"': ['"Aria Sterling"'], '"Distant Love"': ['"Ava Morgan"'],
'"Dream\'s End"': ['"Mia Sullivan"'], '"Drifting Through Eternal Skies"':
['"Eliana Harper"'], '"Echoes of the Whispering Wind"': ['"Felix Blackwood"'],
'"Eclipsed"': ['"Aria Sterling"'], '"Elusive Joy"': ['"Orion Steele"'], '"End of
the Line"': ['"Violet Cascade"'], '"Eternal Solitude"': ['"Hazel Winter', 'Ava
Morgan"'], '"Fading Glow"': ['"Soulful Reverie"'], '"Falling Through the Cracks
of Time"': ['"Astrid Nightshade"'], '"Fleeting Touch"': ['"Mia Sullivan',
'Carter Knight"'], '"Fragile Bonds"': ['"Jasper Skye"'], '"Heartstrings
Symphony"': ['"Ava Morgan"'], '"In the Chambers of My Mind"': ['"Jasper Skye"'],
'"In the Embrace of Midnight"': ['"Luna Starlight"'], '"In the Theater of Broken
Promises"': ['"Isabella Cruz"'], '"In the Tunnels of Despair"': ['"Apollo
Serenade"'], '"Journey Through the Unknown"': ['"Hazel Winter', 'Ava Morgan"'],
'"Lonely Skies"': ['"Aria Sterling"'], '"Longing for Another Chance"': ['"Aria
Sterling"'], '"Lost in a Sea of Echoes"': ['"Zoey James"'], '"Lost in the
Twilight of Hope"': ['"Felix Blackwood"'], '"Mortal Coil"': ['"Zoe Mitchell"'],
'"Neon Reverie"': ['"Violet Cascade"'], '"On the Border of Reality"': ['"Lily
Moon', 'Zoey James"'], '"Paper Planes"': ['"Seraphina Dawn"'], '"Phantom Pain"':
['"Hazel Winter', 'Ava Morgan"'], '"Reflections in a Pool of Regret"': ['"Jasper
Skye', 'Isabella Cruz', 'Seraphina Dawn"'], '"Sailing on the Sea of Dreams"':
['"Silent Thunder"'], '"Secrets We Dare Not Speak"': ['"Noah Bennett"'],
'"Silent Sorrow"': ['"Aria Sterling"'], '"Silver Lining"': ['"Ava Morgan"'],
'"Summer\'s End"': ['"Ethan Wallace"'], '"Symphony of the Twilight Forest"':
['"Aria Sterling"'], '"Tangled Lies"': ['"Ava Morgan"'], '"The Complexity of
Simple Things"': ['"Nova Harmony"'], '"The Curse of Loving You"': ['"Lucas
Grey"'], '"The Forgotten Pages of Time"': ['"Luna Starlight"'], '"The Illusion
of Forever"': ['"Liam Palmer"'], '"The Last Waltz of a Broken Heart"':
['"Soulful Reverie"'], '"The Silence After the Storm"': ['"Lucas Diaz"'], '"The
Silence Between Us"': ['"Aria Sterling"'], '"The Silence that Speaks Volumes"':
['"Violet Cascade"'], '"The Spiral of Unending Thoughts"': ['"Carter Knight"'],
'"The Threads of a Frayed Bond"': ['"Oceanic Odyssey"'], '"The Unbearable
Lightness of Loss"': ['"Zoe Mitchell"'], '"Time\'s Hold"': ['"Aria Sterling"'],
'"Torn Between Two Worlds"': ['"Oceanic Odyssey"'], '"Trapped in a Daydream"':
['"Nova Harmony"'], '"Under the Gaze of a Watchful Moon"': ['"Eliana Harper"'],
'"Under the Willow"': ['"Nova Harmony"'], '"Unearthed Secrets"': ['"Aria
Sterling"'], '"Wading Through Rivers of Tears"': ['"Noah Bennett', 'Ethan
Wallace', 'Nova Harmony"'], '"Walking Through the Valley of Shadows"': ['"Lily
Moon', 'Zoey James"'], '"Wandering the Streets Alone"': ['"Felix Blackwood"'],
'"Weeping Sky"': ['"Luna Starlight"'], '"When All Hope Seems Lost"':
['"Seraphina Dawn"'], '"Whispers in the Wind"': ['"Hazel Winter"'], '"Whispers
of Tomorrow"': ['"Phoenix Rivers"'], '"Whispers of the Enchanted Forest"':
['"Aria Sterling"'], '"Whispers of the Heart"': ['"Lily Moon', 'Zoey James"'],
'"Whispers of the Soul"': ['"Mia Sullivan', 'Carter Knight"']}
==
Munch({'Silent Sorrow': ['Aria Sterling'], 'In the Theater of Broken Promises':
['Isabella Cruz'], 'Longing for Another Chance': ['Aria Sterling'], 'The Spiral
of Unending Thoughts': ['Carter Knight'], 'Lost in a Sea of Echoes': ['Zoey
James'], 'Reflections in a Pool of Regret': ['Jasper Skye', 'Isabella Cruz',
'Seraphina Dawn'], 'The Curse of Loving You': ['Lucas Grey'], 'The Unbearable
Lightness of Loss': ['Zoe Mitchell'], 'Mortal Coil': ['Zoe Mitchell'], 'Dancing
Through the Veil of Dreams': ['Noah Bennett'], 'Secrets We Dare Not Speak':
['Noah Bennett'], 'Drifting Through Eternal Skies': ['Eliana Harper'], "Dream's
End": ['Mia Sullivan'], 'The Silence After the Storm': ['Lucas Diaz'], 'The
Forgotten Pages of Time': ['Luna Starlight'], 'Wading Through Rivers of Tears':
['Noah Bennett', 'Ethan Wallace', 'Nova Harmony'], 'In the Embrace of Midnight':
['Luna Starlight'], 'Under the Gaze of a Watchful Moon': ['Eliana Harper'],
'Whispers in the Wind': ['Hazel Winter'], 'Weeping Sky': ['Luna Starlight'],
"Destiny's Game": ['Aria Sterling'], 'The Silence that Speaks Volumes': ['Violet
Cascade'], 'End of the Line': ['Violet Cascade'], 'Neon Reverie': ['Violet
Cascade'], 'Eclipsed': ['Aria Sterling'], 'Symphony of the Twilight Forest':
['Aria Sterling'], "Time's Hold": ['Aria Sterling'], 'Cherry Blossom Tears':
['Aria Sterling'], 'Whispers of the Enchanted Forest': ['Aria Sterling'],
'Lonely Skies': ['Aria Sterling'], 'The Silence Between Us': ['Aria Sterling'],
'Bleeding Sun': ['Aria Sterling'], 'Unearthed Secrets': ['Aria Sterling'],
'Heartstrings Symphony': ['Ava Morgan'], 'Tangled Lies': ['Ava Morgan'],
'Distant Love': ['Ava Morgan'], 'Silver Lining': ['Ava Morgan'], 'In the Tunnels
of Despair': ['Apollo Serenade'], 'The Complexity of Simple Things': ['Nova
Harmony'], 'Trapped in a Daydream': ['Nova Harmony'], 'Journey Through the
Unknown': ['Hazel Winter', 'Ava Morgan'], 'Caught in a Web of Lies': ['Hazel
Winter', 'Ava Morgan'], 'Eternal Solitude': ['Hazel Winter', 'Ava Morgan'],
'Phantom Pain': ['Hazel Winter', 'Ava Morgan'], 'Under the Willow': ['Nova
Harmony'], 'Falling Through the Cracks of Time': ['Astrid Nightshade'], 'Beneath
the Stars of Reminiscence': ['Astrid Nightshade'], "Summer's End": ['Ethan
Wallace'], 'On the Border of Reality': ['Lily Moon', 'Zoey James'], 'Walking
Through the Valley of Shadows': ['Lily Moon', 'Zoey James'], 'Whispers of the
Heart': ['Lily Moon', 'Zoey James'], 'Whispers of the Soul': ['Mia Sullivan',
'Carter Knight'], 'A Love That Never Was': ['Mia Sullivan', 'Carter Knight'],
'Fleeting Touch': ['Mia Sullivan', 'Carter Knight'], 'Wandering the Streets
Alone': ['Felix Blackwood'], 'Echoes of the Whispering Wind': ['Felix
Blackwood'], 'Lost in the Twilight of Hope': ['Felix Blackwood'], 'A Tale of
Unspoken Longing': ['Emily Rivers'], 'Torn Between Two Worlds': ['Oceanic
Odyssey'], 'Whispers of Tomorrow': ['Phoenix Rivers'], 'The Threads of a Frayed
Bond': ['Oceanic Odyssey'], 'Sailing on the Sea of Dreams': ['Silent Thunder'],
'In the Chambers of My Mind': ['Jasper Skye'], 'Fragile Bonds': ['Jasper Skye'],
'The Last Waltz of a Broken Heart': ['Soulful Reverie'], 'Fading Glow':
['Soulful Reverie'], 'Cold Embrace': ['Ava Morgan'], 'The Illusion of Forever':
['Liam Palmer'], 'When All Hope Seems Lost': ['Seraphina Dawn'], 'Paper Planes':
['Seraphina Dawn'], 'Elusive Joy': ['Orion Steele'], 'Astral Serenity': ['Orion
Steele']})
>> Failed Requirement
assert keys of song_title_to_artist_names match keys of
private_data.song_title_to_artist_names
(ignore_order, normalize_text).
```python
with test(
    """
    assert keys of song_title_to_artist_names match keys of
private_data.song_title_to_artist_names
    (ignore_order, normalize_text).
    """
):
    test.case(
        list(song_title_to_artist_names.keys()),
```
----------
AssertionError:
['"a love that never was"', '"a tale of unspoken longing"', '"astral serenity"',
'"beneath the stars of reminiscence"', '"bleeding sun"', '"caught in a web of
lies"', '"cherry blossom tears"', '"cold embrace"', '"dancing through the veil
of dreams"', '"destiny\'s game"', '"distant love"', '"dream\'s end"', '"drifting
through eternal skies"', '"echoes of the whispering wind"', '"eclipsed"',
'"elusive joy"', '"end of the line"', '"eternal solitude"', '"fading glow"',
'"falling through the cracks of time"', '"fleeting touch"', '"fragile bonds"',
'"heartstrings symphony"', '"in the chambers of my mind"', '"in the embrace of
midnight"', '"in the theater of broken promises"', '"in the tunnels of
despair"', '"journey through the unknown"', '"lonely skies"', '"longing for
another chance"', '"lost in a sea of echoes"', '"lost in the twilight of hope"',
'"mortal coil"', '"neon reverie"', '"on the border of reality"', '"paper
planes"', '"phantom pain"', '"reflections in a pool of regret"', '"sailing on
the sea of dreams"', '"secrets we dare not speak"', '"silent sorrow"', '"silver
lining"', '"summer\'s end"', '"symphony of the twilight forest"', '"tangled
lies"', '"the complexity of simple things"', '"the curse of loving you"', '"the
forgotten pages of time"', '"the illusion of forever"', '"the last waltz of a
broken heart"', '"the silence after the storm"', '"the silence between us"',
'"the silence that speaks volumes"', '"the spiral of unending thoughts"', '"the
threads of a frayed bond"', '"the unbearable lightness of loss"', '"time\'s
hold"', '"torn between two worlds"', '"trapped in a daydream"', '"under the gaze
of a watchful moon"', '"under the willow"', '"unearthed secrets"', '"wading
through rivers of tears"', '"walking through the valley of shadows"',
'"wandering the streets alone"', '"weeping sky"', '"when all hope seems lost"',
'"whispers in the wind"', '"whispers of the enchanted forest"', '"whispers of
the heart"', '"whispers of the soul"', '"whispers of tomorrow"']
==
['a love that never was', 'a tale of unspoken longing', 'astral serenity',
'beneath the stars of reminiscence', 'bleeding sun', 'caught in a web of lies',
'cherry blossom tears', 'cold embrace', 'dancing through the veil of dreams',
"destiny's game", 'distant love', "dream's end", 'drifting through eternal
skies', 'echoes of the whispering wind', 'eclipsed', 'elusive joy', 'end of the
line', 'eternal solitude', 'fading glow', 'falling through the cracks of time',
'fleeting touch', 'fragile bonds', 'heartstrings symphony', 'in the chambers of
my mind', 'in the embrace of midnight', 'in the theater of broken promises', 'in
the tunnels of despair', 'journey through the unknown', 'lonely skies', 'longing
for another chance', 'lost in a sea of echoes', 'lost in the twilight of hope',
'mortal coil', 'neon reverie', 'on the border of reality', 'paper planes',
'phantom pain', 'reflections in a pool of regret', 'sailing on the sea of
dreams', 'secrets we dare not speak', 'silent sorrow', 'silver lining',
"summer's end", 'symphony of the twilight forest', 'tangled lies', 'the
complexity of simple things', 'the curse of loving you', 'the forgotten pages of
time', 'the illusion of forever', 'the last waltz of a broken heart', 'the
silence after the storm', 'the silence between us', 'the silence that speaks
volumes', 'the spiral of unending thoughts', 'the threads of a frayed bond',
'the unbearable lightness of loss', "time's hold", 'torn between two worlds',
'trapped in a daydream', 'under the gaze of a watchful moon', 'under the
willow', 'unearthed secrets', 'wading through rivers of tears', 'walking through
the valley of shadows', 'wandering the streets alone', 'weeping sky', 'when all
hope seems lost', 'whispers in the wind', 'whispers of the enchanted forest',
'whispers of the heart', 'whispers of the soul', 'whispers of tomorrow']

In left but not right:
['"a love that never was"', '"a tale of unspoken longing"', '"astral serenity"',
'"beneath the stars of reminiscence"', '"bleeding sun"', '"caught in a web of
lies"', '"cherry blossom tears"', '"cold embrace"', '"dancing through the veil
of dreams"', '"destiny\'s game"', '"distant love"', '"dream\'s end"', '"drifting
through eternal skies"', '"echoes of the whispering wind"', '"eclipsed"',
'"elusive joy"', '"end of the line"', '"eternal solitude"', '"fading glow"',
'"falling through the cracks of time"', '"fleeting touch"', '"fragile bonds"',
'"heartstrings symphony"', '"in the chambers of my mind"', '"in the embrace of
midnight"', '"in the theater of broken promises"', '"in the tunnels of
despair"', '"journey through the unknown"', '"lonely skies"', '"longing for
another chance"', '"lost in a sea of echoes"', '"lost in the twilight of hope"',
'"mortal coil"', '"neon reverie"', '"on the border of reality"', '"paper
planes"', '"phantom pain"', '"reflections in a pool of regret"', '"sailing on
the sea of dreams"', '"secrets we dare not speak"', '"silent sorrow"', '"silver
lining"', '"summer\'s end"', '"symphony of the twilight forest"', '"tangled
lies"', '"the complexity of simple things"', '"the curse of loving you"', '"the
forgotten pages of time"', '"the illusion of forever"', '"the last waltz of a
broken heart"', '"the silence after the storm"', '"the silence between us"',
'"the silence that speaks volumes"', '"the spiral of unending thoughts"', '"the
threads of a frayed bond"', '"the unbearable lightness of loss"', '"time\'s
hold"', '"torn between two worlds"', '"trapped in a daydream"', '"under the gaze
of a watchful moon"', '"under the willow"', '"unearthed secrets"', '"wading
through rivers of tears"', '"walking through the valley of shadows"',
'"wandering the streets alone"', '"weeping sky"', '"when all hope seems lost"',
'"whispers in the wind"', '"whispers of the enchanted forest"', '"whispers of
the heart"', '"whispers of the soul"', '"whispers of tomorrow"']

In right but not left:
['a love that never was', 'a tale of unspoken longing', 'astral serenity',
'beneath the stars of reminiscence', 'bleeding sun', 'caught in a web of lies',
'cherry blossom tears', 'cold embrace', 'dancing through the veil of dreams',
"destiny's game", 'distant love', "dream's end", 'drifting through eternal
skies', 'echoes of the whispering wind', 'eclipsed', 'elusive joy', 'end of the
line', 'eternal solitude', 'fading glow', 'falling through the cracks of time',
'fleeting touch', 'fragile bonds', 'heartstrings symphony', 'in the chambers of
my mind', 'in the embrace of midnight', 'in the theater of broken promises', 'in
the tunnels of despair', 'journey through the unknown', 'lonely skies', 'longing
for another chance', 'lost in a sea of echoes', 'lost in the twilight of hope',
'mortal coil', 'neon reverie', 'on the border of reality', 'paper planes',
'phantom pain', 'reflections in a pool of regret', 'sailing on the sea of
dreams', 'secrets we dare not speak', 'silent sorrow', 'silver lining',
"summer's end", 'symphony of the twilight forest', 'tangled lies', 'the
complexity of simple things', 'the curse of loving you', 'the forgotten pages of
time', 'the illusion of forever', 'the last waltz of a broken heart', 'the
silence after the storm', 'the silence between us', 'the silence that speaks
volumes', 'the spiral of unending thoughts', 'the threads of a frayed bond',
'the unbearable lightness of loss', "time's hold", 'torn between two worlds',
'trapped in a daydream', 'under the gaze of a watchful moon', 'under the
willow', 'unearthed secrets', 'wading through rivers of tears', 'walking through
the valley of shadows', 'wandering the streets alone', 'weeping sky', 'when all
hope seems lost', 'whispers in the wind', 'whispers of the enchanted forest',
'whispers of the heart', 'whispers of the soul', 'whispers of tomorrow']

Original values:
['"a love that never was"', '"a tale of unspoken longing"', '"astral serenity"',
'"beneath the stars of reminiscence"', '"bleeding sun"', '"caught in a web of
lies"', '"cherry blossom tears"', '"cold embrace"', '"dancing through the veil
of dreams"', '"destiny\'s game"', '"distant love"', '"dream\'s end"', '"drifting
through eternal skies"', '"echoes of the whispering wind"', '"eclipsed"',
'"elusive joy"', '"end of the line"', '"eternal solitude"', '"fading glow"',
'"falling through the cracks of time"', '"fleeting touch"', '"fragile bonds"',
'"heartstrings symphony"', '"in the chambers of my mind"', '"in the embrace of
midnight"', '"in the theater of broken promises"', '"in the tunnels of
despair"', '"journey through the unknown"', '"lonely skies"', '"longing for
another chance"', '"lost in a sea of echoes"', '"lost in the twilight of hope"',
'"mortal coil"', '"neon reverie"', '"on the border of reality"', '"paper
planes"', '"phantom pain"', '"reflections in a pool of regret"', '"sailing on
the sea of dreams"', '"secrets we dare not speak"', '"silent sorrow"', '"silver
lining"', '"summer\'s end"', '"symphony of the twilight forest"', '"tangled
lies"', '"the complexity of simple things"', '"the curse of loving you"', '"the
forgotten pages of time"', '"the illusion of forever"', '"the last waltz of a
broken heart"', '"the silence after the storm"', '"the silence between us"',
'"the silence that speaks volumes"', '"the spiral of unending thoughts"', '"the
threads of a frayed bond"', '"the unbearable lightness of loss"', '"time\'s
hold"', '"torn between two worlds"', '"trapped in a daydream"', '"under the gaze
of a watchful moon"', '"under the willow"', '"unearthed secrets"', '"wading
through rivers of tears"', '"walking through the valley of shadows"',
'"wandering the streets alone"', '"weeping sky"', '"when all hope seems lost"',
'"whispers in the wind"', '"whispers of tomorrow"', '"whispers of the enchanted
forest"', '"whispers of the heart"', '"whispers of the soul"']
==
['silent sorrow', 'in the theater of broken promises', 'longing for another
chance', 'the spiral of unending thoughts', 'lost in a sea of echoes',
'reflections in a pool of regret', 'the curse of loving you', 'the unbearable
lightness of loss', 'mortal coil', 'dancing through the veil of dreams',
'secrets we dare not speak', 'drifting through eternal skies', "dream's end",
'the silence after the storm', 'the forgotten pages of time', 'wading through
rivers of tears', 'in the embrace of midnight', 'under the gaze of a watchful
moon', 'whispers in the wind', 'weeping sky', "destiny's game", 'the silence
that speaks volumes', 'end of the line', 'neon reverie', 'eclipsed', 'symphony
of the twilight forest', "time's hold", 'cherry blossom tears', 'whispers of the
enchanted forest', 'lonely skies', 'the silence between us', 'bleeding sun',
'unearthed secrets', 'heartstrings symphony', 'tangled lies', 'distant love',
'silver lining', 'in the tunnels of despair', 'the complexity of simple things',
'trapped in a daydream', 'journey through the unknown', 'caught in a web of
lies', 'eternal solitude', 'phantom pain', 'under the willow', 'falling through
the cracks of time', 'beneath the stars of reminiscence', "summer's end", 'on
the border of reality', 'walking through the valley of shadows', 'whispers of
the heart', 'whispers of the soul', 'a love that never was', 'fleeting touch',
'wandering the streets alone', 'echoes of the whispering wind', 'lost in the
twilight of hope', 'a tale of unspoken longing', 'torn between two worlds',
'whispers of tomorrow', 'the threads of a frayed bond', 'sailing on the sea of
dreams', 'in the chambers of my mind', 'fragile bonds', 'the last waltz of a
broken heart', 'fading glow', 'cold embrace', 'the illusion of forever', 'when
all hope seems lost', 'paper planes', 'elusive joy', 'astral serenity']
>> Failed Requirement
assert values of song_title_to_artist_names match values of
private_data.song_title_to_artist_names
(ignore_order, normalize_text).
```python
with test(
    """
    assert values of song_title_to_artist_names match values of
private_data.song_title_to_artist_names
    (ignore_order, normalize_text).
    """
):
    test.case(
        list(song_title_to_artist_names.values()),
```
----------
AssertionError:
[['"apollo serenade"'], ['"aria sterling"'], ['"aria sterling"'], ['"aria
sterling"'], ['"aria sterling"'], ['"aria sterling"'], ['"aria sterling"'],
['"aria sterling"'], ['"aria sterling"'], ['"aria sterling"'], ['"aria
sterling"'], ['"aria sterling"'], ['"aria sterling"'], ['"astrid nightshade"'],
['"astrid nightshade"'], ['"ava morgan"'], ['"ava morgan"'], ['"ava morgan"'],
['"ava morgan"'], ['"ava morgan"'], ['"carter knight"'], ['"eliana harper"'],
['"eliana harper"'], ['"emily rivers"'], ['"ethan wallace"'], ['"felix
blackwood"'], ['"felix blackwood"'], ['"felix blackwood"'], ['"hazel winter',
'ava morgan"'], ['"hazel winter', 'ava morgan"'], ['"hazel winter', 'ava
morgan"'], ['"hazel winter', 'ava morgan"'], ['"hazel winter"'], ['"isabella
cruz"'], ['"jasper skye', 'isabella cruz', 'seraphina dawn"'], ['"jasper
skye"'], ['"jasper skye"'], ['"liam palmer"'], ['"lily moon', 'zoey james"'],
['"lily moon', 'zoey james"'], ['"lily moon', 'zoey james"'], ['"lucas diaz"'],
['"lucas grey"'], ['"luna starlight"'], ['"luna starlight"'], ['"luna
starlight"'], ['"mia sullivan', 'carter knight"'], ['"mia sullivan', 'carter
knight"'], ['"mia sullivan', 'carter knight"'], ['"mia sullivan"'], ['"noah
bennett', 'ethan wallace', 'nova harmony"'], ['"noah bennett"'], ['"noah
bennett"'], ['"nova harmony"'], ['"nova harmony"'], ['"nova harmony"'],
['"oceanic odyssey"'], ['"oceanic odyssey"'], ['"orion steele"'], ['"orion
steele"'], ['"phoenix rivers"'], ['"seraphina dawn"'], ['"seraphina dawn"'],
['"silent thunder"'], ['"soulful reverie"'], ['"soulful reverie"'], ['"violet
cascade"'], ['"violet cascade"'], ['"violet cascade"'], ['"zoe mitchell"'],
['"zoe mitchell"'], ['"zoey james"']]
==
[['apollo serenade'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['astrid nightshade'], ['astrid nightshade'], ['ava morgan'],
['ava morgan'], ['ava morgan'], ['ava morgan'], ['ava morgan'], ['ava morgan',
'hazel winter'], ['ava morgan', 'hazel winter'], ['ava morgan', 'hazel winter'],
['ava morgan', 'hazel winter'], ['carter knight'], ['carter knight', 'mia
sullivan'], ['carter knight', 'mia sullivan'], ['carter knight', 'mia
sullivan'], ['eliana harper'], ['eliana harper'], ['emily rivers'], ['ethan
wallace'], ['ethan wallace', 'noah bennett', 'nova harmony'], ['felix
blackwood'], ['felix blackwood'], ['felix blackwood'], ['hazel winter'],
['isabella cruz'], ['isabella cruz', 'jasper skye', 'seraphina dawn'], ['jasper
skye'], ['jasper skye'], ['liam palmer'], ['lily moon', 'zoey james'], ['lily
moon', 'zoey james'], ['lily moon', 'zoey james'], ['lucas diaz'], ['lucas
grey'], ['luna starlight'], ['luna starlight'], ['luna starlight'], ['mia
sullivan'], ['noah bennett'], ['noah bennett'], ['nova harmony'], ['nova
harmony'], ['nova harmony'], ['oceanic odyssey'], ['oceanic odyssey'], ['orion
steele'], ['orion steele'], ['phoenix rivers'], ['seraphina dawn'], ['seraphina
dawn'], ['silent thunder'], ['soulful reverie'], ['soulful reverie'], ['violet
cascade'], ['violet cascade'], ['violet cascade'], ['zoe mitchell'], ['zoe
mitchell'], ['zoey james']]

In left but not right:
[['"apollo serenade"'], ['"aria sterling"'], ['"aria sterling"'], ['"aria
sterling"'], ['"aria sterling"'], ['"aria sterling"'], ['"aria sterling"'],
['"aria sterling"'], ['"aria sterling"'], ['"aria sterling"'], ['"aria
sterling"'], ['"aria sterling"'], ['"aria sterling"'], ['"astrid nightshade"'],
['"astrid nightshade"'], ['"ava morgan"'], ['"ava morgan"'], ['"ava morgan"'],
['"ava morgan"'], ['"ava morgan"'], ['"carter knight"'], ['"eliana harper"'],
['"eliana harper"'], ['"emily rivers"'], ['"ethan wallace"'], ['"felix
blackwood"'], ['"felix blackwood"'], ['"felix blackwood"'], ['"hazel winter',
'ava morgan"'], ['"hazel winter', 'ava morgan"'], ['"hazel winter', 'ava
morgan"'], ['"hazel winter', 'ava morgan"'], ['"hazel winter"'], ['"isabella
cruz"'], ['"jasper skye', 'isabella cruz', 'seraphina dawn"'], ['"jasper
skye"'], ['"jasper skye"'], ['"liam palmer"'], ['"lily moon', 'zoey james"'],
['"lily moon', 'zoey james"'], ['"lily moon', 'zoey james"'], ['"lucas diaz"'],
['"lucas grey"'], ['"luna starlight"'], ['"luna starlight"'], ['"luna
starlight"'], ['"mia sullivan', 'carter knight"'], ['"mia sullivan', 'carter
knight"'], ['"mia sullivan', 'carter knight"'], ['"mia sullivan"'], ['"noah
bennett', 'ethan wallace', 'nova harmony"'], ['"noah bennett"'], ['"noah
bennett"'], ['"nova harmony"'], ['"nova harmony"'], ['"nova harmony"'],
['"oceanic odyssey"'], ['"oceanic odyssey"'], ['"orion steele"'], ['"orion
steele"'], ['"phoenix rivers"'], ['"seraphina dawn"'], ['"seraphina dawn"'],
['"silent thunder"'], ['"soulful reverie"'], ['"soulful reverie"'], ['"violet
cascade"'], ['"violet cascade"'], ['"violet cascade"'], ['"zoe mitchell"'],
['"zoe mitchell"'], ['"zoey james"']]

In right but not left:
[['apollo serenade'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['astrid nightshade'], ['astrid nightshade'], ['ava morgan'],
['ava morgan'], ['ava morgan'], ['ava morgan'], ['ava morgan'], ['ava morgan',
'hazel winter'], ['ava morgan', 'hazel winter'], ['ava morgan', 'hazel winter'],
['ava morgan', 'hazel winter'], ['carter knight'], ['carter knight', 'mia
sullivan'], ['carter knight', 'mia sullivan'], ['carter knight', 'mia
sullivan'], ['eliana harper'], ['eliana harper'], ['emily rivers'], ['ethan
wallace'], ['ethan wallace', 'noah bennett', 'nova harmony'], ['felix
blackwood'], ['felix blackwood'], ['felix blackwood'], ['hazel winter'],
['isabella cruz'], ['isabella cruz', 'jasper skye', 'seraphina dawn'], ['jasper
skye'], ['jasper skye'], ['liam palmer'], ['lily moon', 'zoey james'], ['lily
moon', 'zoey james'], ['lily moon', 'zoey james'], ['lucas diaz'], ['lucas
grey'], ['luna starlight'], ['luna starlight'], ['luna starlight'], ['mia
sullivan'], ['noah bennett'], ['noah bennett'], ['nova harmony'], ['nova
harmony'], ['nova harmony'], ['oceanic odyssey'], ['oceanic odyssey'], ['orion
steele'], ['orion steele'], ['phoenix rivers'], ['seraphina dawn'], ['seraphina
dawn'], ['silent thunder'], ['soulful reverie'], ['soulful reverie'], ['violet
cascade'], ['violet cascade'], ['violet cascade'], ['zoe mitchell'], ['zoe
mitchell'], ['zoey james']]

Original values:
[['"mia sullivan', 'carter knight"'], ['"emily rivers"'], ['"orion steele"'],
['"astrid nightshade"'], ['"aria sterling"'], ['"hazel winter', 'ava morgan"'],
['"aria sterling"'], ['"ava morgan"'], ['"noah bennett"'], ['"aria sterling"'],
['"ava morgan"'], ['"mia sullivan"'], ['"eliana harper"'], ['"felix
blackwood"'], ['"aria sterling"'], ['"orion steele"'], ['"violet cascade"'],
['"hazel winter', 'ava morgan"'], ['"soulful reverie"'], ['"astrid
nightshade"'], ['"mia sullivan', 'carter knight"'], ['"jasper skye"'], ['"ava
morgan"'], ['"jasper skye"'], ['"luna starlight"'], ['"isabella cruz"'],
['"apollo serenade"'], ['"hazel winter', 'ava morgan"'], ['"aria sterling"'],
['"aria sterling"'], ['"zoey james"'], ['"felix blackwood"'], ['"zoe
mitchell"'], ['"violet cascade"'], ['"lily moon', 'zoey james"'], ['"seraphina
dawn"'], ['"hazel winter', 'ava morgan"'], ['"jasper skye', 'isabella cruz',
'seraphina dawn"'], ['"silent thunder"'], ['"noah bennett"'], ['"aria
sterling"'], ['"ava morgan"'], ['"ethan wallace"'], ['"aria sterling"'], ['"ava
morgan"'], ['"nova harmony"'], ['"lucas grey"'], ['"luna starlight"'], ['"liam
palmer"'], ['"soulful reverie"'], ['"lucas diaz"'], ['"aria sterling"'],
['"violet cascade"'], ['"carter knight"'], ['"oceanic odyssey"'], ['"zoe
mitchell"'], ['"aria sterling"'], ['"oceanic odyssey"'], ['"nova harmony"'],
['"eliana harper"'], ['"nova harmony"'], ['"aria sterling"'], ['"noah bennett',
'ethan wallace', 'nova harmony"'], ['"lily moon', 'zoey james"'], ['"felix
blackwood"'], ['"luna starlight"'], ['"seraphina dawn"'], ['"hazel winter"'],
['"phoenix rivers"'], ['"aria sterling"'], ['"lily moon', 'zoey james"'], ['"mia
sullivan', 'carter knight"']]
==
[['aria sterling'], ['isabella cruz'], ['aria sterling'], ['carter knight'],
['zoey james'], ['jasper skye', 'isabella cruz', 'seraphina dawn'], ['lucas
grey'], ['zoe mitchell'], ['zoe mitchell'], ['noah bennett'], ['noah bennett'],
['eliana harper'], ['mia sullivan'], ['lucas diaz'], ['luna starlight'], ['noah
bennett', 'ethan wallace', 'nova harmony'], ['luna starlight'], ['eliana
harper'], ['hazel winter'], ['luna starlight'], ['aria sterling'], ['violet
cascade'], ['violet cascade'], ['violet cascade'], ['aria sterling'], ['aria
sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria
sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'], ['ava
morgan'], ['ava morgan'], ['ava morgan'], ['ava morgan'], ['apollo serenade'],
['nova harmony'], ['nova harmony'], ['hazel winter', 'ava morgan'], ['hazel
winter', 'ava morgan'], ['hazel winter', 'ava morgan'], ['hazel winter', 'ava
morgan'], ['nova harmony'], ['astrid nightshade'], ['astrid nightshade'],
['ethan wallace'], ['lily moon', 'zoey james'], ['lily moon', 'zoey james'],
['lily moon', 'zoey james'], ['mia sullivan', 'carter knight'], ['mia sullivan',
'carter knight'], ['mia sullivan', 'carter knight'], ['felix blackwood'],
['felix blackwood'], ['felix blackwood'], ['emily rivers'], ['oceanic odyssey'],
['phoenix rivers'], ['oceanic odyssey'], ['silent thunder'], ['jasper skye'],
['jasper skye'], ['soulful reverie'], ['soulful reverie'], ['ava morgan'],
['liam palmer'], ['seraphina dawn'], ['seraphina dawn'], ['orion steele'],
['orion steele']]