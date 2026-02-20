──────────────────────────────── Overall Stats ─────────────────────────────────
Num Passed Tests : 7
Num Failed Tests : 3
Num Total  Tests : 10
──────────────────────────────────── Passes ────────────────────────────────────
>> Passed Requirement
assert answers match.
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
{'the curse of loving you': ['lucas grey'], 'endless roads': ['aria sterling'],
'heartstrings symphony': ['ava morgan'], 'eternal solitude': ['ava morgan',
'hazel winter'], 'crimson skies of longing': ['phoenix rivers']}
==
{'memories stitched in time': ['isabella cruz', 'jasper skye', 'seraphina
dawn'], "hope's edge": ['carter knight'], "heaven's tears": ['carter knight'],
'the curse of loving you': ['lucas grey'], 'twisted paths': ['zoe mitchell'],
'empty pages': ['evelyn rose'], 'bittersweet goodbye': ['evelyn rose'], 'longing
for a touch unfelt': ['eliana harper'], 'yearning for a glimpse of heaven':
['noah bennett'], 'dancing through the veil of dreams': ['noah bennett'],
'secrets we dare not speak': ['noah bennett'], 'forged in fire': ['noah
bennett'], 'between the depths and heights': ['noah bennett'], 'lost in the
embrace of night': ['noah bennett'], 'echoes from the abyss': ['noah bennett'],
'melodies from a distant past': ['eliana harper'], 'in the arms of a stranger':
['eliana harper'], 'the enigma of love': ['mia sullivan'], 'when all words fail
us': ['marcus lane'], 'violet rain': ['ethan wallace', 'noah bennett', 'nova
harmony'], 'eternal flame': ['ethan wallace', 'noah bennett', 'nova harmony'],
'waves of yesterday': ['ethan wallace', 'noah bennett', 'nova harmony'], 'wading
through rivers of tears': ['ethan wallace', 'noah bennett', 'nova harmony'],
'weeping sky': ['luna starlight'], 'veiled sorrow': ['grace harrison'], 'a
whisper in the midnight air': ['eliana harper'], 'endless roads': ['aria
sterling'], 'the resonance of a silent cry': ['violet cascade'], 'eclipsed':
['aria sterling'], "time's hold": ['aria sterling'], 'cherry blossom tears':
['aria sterling'], 'whispers of the enchanted forest': ['aria sterling'],
'lonely skies': ['aria sterling'], 'the silence between us': ['aria sterling'],
'bleeding sun': ['aria sterling'], 'unearthed secrets': ['aria sterling'],
'heartstrings symphony': ['ava morgan'], 'reckless love': ['apollo serenade'],
'the edge of eternity': ['dylan stone'], 'cursed love': ['zoey james'], "love's
decline": ['zoey james'], 'the haunting echo of goodbye': ['zoey james'], 'ocean
eyes': ['zoey james'], 'crimson sunset sonata': ['zoey james'], 'final goodbye':
['dylan stone'], 'eternal solitude': ['ava morgan', 'hazel winter'], 'lost in
the labyrinth of love': ['noah bennett'], 'fading echoes': ['astrid
nightshade'], 'lost in the wilderness of love': ['lily moon', 'zoey james'],
'surrendering to the night': ['astrid nightshade'], 'sacred ground': ['noah
bennett'], "wandering through time's embrace": ['lucas diaz', 'orion steele'],
'whispers of the heart': ['lily moon', 'zoey james'], 'wandering the streets
alone': ['felix blackwood'], 'echoes of the whispering wind': ['felix
blackwood'], 'lost in the twilight of hope': ['felix blackwood'], 'caught in the
tangle of time': ['emily rivers'], 'eternal melancholy': ['olivia roberts'],
'crimson skies of longing': ['phoenix rivers'], 'glass castles': ['phoenix
rivers'], 'sailing on the sea of dreams': ['silent thunder'], 'bridges burned
and rivers crossed': ['jasper skye'], 'fading glow': ['soulful reverie'], 'the
mirage of a perfect life': ['soulful reverie'], 'azure skies': ['liam palmer'],
'lonesome road': ['seraphina dawn'], 'wilted roses on the vine': ['velvet
echo'], 'cosmic drift': ['velvet echo'], 'a symphony written in tears': ['aria
sterling'], 'blind trust': ['isabella cruz']}

Original values:
{'The Curse of Loving You': ['Lucas Grey'], 'Endless Roads': ['Aria Sterling'],
'Heartstrings Symphony': ['Ava Morgan'], 'Eternal Solitude': ['Hazel Winter',
'Ava Morgan'], 'Crimson Skies of Longing': ['Phoenix Rivers']}
==
Munch({'Memories Stitched in Time': ['Jasper Skye', 'Isabella Cruz', 'Seraphina
Dawn'], "Hope's Edge": ['Carter Knight'], "Heaven's Tears": ['Carter Knight'],
'The Curse of Loving You': ['Lucas Grey'], 'Twisted Paths': ['Zoe Mitchell'],
'Empty Pages': ['Evelyn Rose'], 'Bittersweet Goodbye': ['Evelyn Rose'], 'Longing
for a Touch Unfelt': ['Eliana Harper'], 'Yearning for a Glimpse of Heaven':
['Noah Bennett'], 'Dancing Through the Veil of Dreams': ['Noah Bennett'],
'Secrets We Dare Not Speak': ['Noah Bennett'], 'Forged in Fire': ['Noah
Bennett'], 'Between the Depths and Heights': ['Noah Bennett'], 'Lost in the
Embrace of Night': ['Noah Bennett'], 'Echoes from the Abyss': ['Noah Bennett'],
'Melodies from a Distant Past': ['Eliana Harper'], 'In the Arms of a Stranger':
['Eliana Harper'], 'The Enigma of Love': ['Mia Sullivan'], 'When All Words Fail
Us': ['Marcus Lane'], 'Violet Rain': ['Noah Bennett', 'Ethan Wallace', 'Nova
Harmony'], 'Eternal Flame': ['Noah Bennett', 'Ethan Wallace', 'Nova Harmony'],
'Waves of Yesterday': ['Noah Bennett', 'Ethan Wallace', 'Nova Harmony'], 'Wading
Through Rivers of Tears': ['Noah Bennett', 'Ethan Wallace', 'Nova Harmony'],
'Weeping Sky': ['Luna Starlight'], 'Veiled Sorrow': ['Grace Harrison'], 'A
Whisper in the Midnight Air': ['Eliana Harper'], 'Endless Roads': ['Aria
Sterling'], 'The Resonance of a Silent Cry': ['Violet Cascade'], 'Eclipsed':
['Aria Sterling'], "Time's Hold": ['Aria Sterling'], 'Cherry Blossom Tears':
['Aria Sterling'], 'Whispers of the Enchanted Forest': ['Aria Sterling'],
'Lonely Skies': ['Aria Sterling'], 'The Silence Between Us': ['Aria Sterling'],
'Bleeding Sun': ['Aria Sterling'], 'Unearthed Secrets': ['Aria Sterling'],
'Heartstrings Symphony': ['Ava Morgan'], 'Reckless Love': ['Apollo Serenade'],
'The Edge of Eternity': ['Dylan Stone'], 'Cursed Love': ['Zoey James'], "Love's
Decline": ['Zoey James'], 'The Haunting Echo of Goodbye': ['Zoey James'], 'Ocean
Eyes': ['Zoey James'], 'Crimson Sunset Sonata': ['Zoey James'], 'Final Goodbye':
['Dylan Stone'], 'Eternal Solitude': ['Hazel Winter', 'Ava Morgan'], 'Lost in
the Labyrinth of Love': ['Noah Bennett'], 'Fading Echoes': ['Astrid
Nightshade'], 'Lost in the Wilderness of Love': ['Lily Moon', 'Zoey James'],
'Surrendering to the Night': ['Astrid Nightshade'], 'Sacred Ground': ['Noah
Bennett'], "Wandering Through Time's Embrace": ['Lucas Diaz', 'Orion Steele'],
'Whispers of the Heart': ['Lily Moon', 'Zoey James'], 'Wandering the Streets
Alone': ['Felix Blackwood'], 'Echoes of the Whispering Wind': ['Felix
Blackwood'], 'Lost in the Twilight of Hope': ['Felix Blackwood'], 'Caught in the
Tangle of Time': ['Emily Rivers'], 'Eternal Melancholy': ['Olivia Roberts'],
'Crimson Skies of Longing': ['Phoenix Rivers'], 'Glass Castles': ['Phoenix
Rivers'], 'Sailing on the Sea of Dreams': ['Silent Thunder'], 'Bridges Burned
and Rivers Crossed': ['Jasper Skye'], 'Fading Glow': ['Soulful Reverie'], 'The
Mirage of a Perfect Life': ['Soulful Reverie'], 'Azure Skies': ['Liam Palmer'],
'Lonesome Road': ['Seraphina Dawn'], 'Wilted Roses on the Vine': ['Velvet
Echo'], 'Cosmic Drift': ['Velvet Echo'], 'A Symphony Written in Tears': ['Aria
Sterling'], 'Blind Trust': ['Isabella Cruz']})
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
['crimson skies of longing', 'endless roads', 'eternal solitude', 'heartstrings
symphony', 'the curse of loving you']
==
['a symphony written in tears', 'a whisper in the midnight air', 'azure skies',
'between the depths and heights', 'bittersweet goodbye', 'bleeding sun', 'blind
trust', 'bridges burned and rivers crossed', 'caught in the tangle of time',
'cherry blossom tears', 'cosmic drift', 'crimson skies of longing', 'crimson
sunset sonata', 'cursed love', 'dancing through the veil of dreams', 'echoes
from the abyss', 'echoes of the whispering wind', 'eclipsed', 'empty pages',
'endless roads', 'eternal flame', 'eternal melancholy', 'eternal solitude',
'fading echoes', 'fading glow', 'final goodbye', 'forged in fire', 'glass
castles', 'heartstrings symphony', "heaven's tears", "hope's edge", 'in the arms
of a stranger', 'lonely skies', 'lonesome road', 'longing for a touch unfelt',
'lost in the embrace of night', 'lost in the labyrinth of love', 'lost in the
twilight of hope', 'lost in the wilderness of love', "love's decline", 'melodies
from a distant past', 'memories stitched in time', 'ocean eyes', 'reckless
love', 'sacred ground', 'sailing on the sea of dreams', 'secrets we dare not
speak', 'surrendering to the night', 'the curse of loving you', 'the edge of
eternity', 'the enigma of love', 'the haunting echo of goodbye', 'the mirage of
a perfect life', 'the resonance of a silent cry', 'the silence between us',
"time's hold", 'twisted paths', 'unearthed secrets', 'veiled sorrow', 'violet
rain', 'wading through rivers of tears', 'wandering the streets alone',
"wandering through time's embrace", 'waves of yesterday', 'weeping sky', 'when
all words fail us', 'whispers of the enchanted forest', 'whispers of the heart',
'wilted roses on the vine', 'yearning for a glimpse of heaven']

In right but not left:
['a symphony written in tears', 'a whisper in the midnight air', 'azure skies',
'between the depths and heights', 'bittersweet goodbye', 'bleeding sun', 'blind
trust', 'bridges burned and rivers crossed', 'caught in the tangle of time',
'cherry blossom tears', 'cosmic drift', 'crimson sunset sonata', 'cursed love',
'dancing through the veil of dreams', 'echoes from the abyss', 'echoes of the
whispering wind', 'eclipsed', 'empty pages', 'eternal flame', 'eternal
melancholy', 'fading echoes', 'fading glow', 'final goodbye', 'forged in fire',
'glass castles', "heaven's tears", "hope's edge", 'in the arms of a stranger',
'lonely skies', 'lonesome road', 'longing for a touch unfelt', 'lost in the
embrace of night', 'lost in the labyrinth of love', 'lost in the twilight of
hope', 'lost in the wilderness of love', "love's decline", 'melodies from a
distant past', 'memories stitched in time', 'ocean eyes', 'reckless love',
'sacred ground', 'sailing on the sea of dreams', 'secrets we dare not speak',
'surrendering to the night', 'the edge of eternity', 'the enigma of love', 'the
haunting echo of goodbye', 'the mirage of a perfect life', 'the resonance of a
silent cry', 'the silence between us', "time's hold", 'twisted paths',
'unearthed secrets', 'veiled sorrow', 'violet rain', 'wading through rivers of
tears', 'wandering the streets alone', "wandering through time's embrace",
'waves of yesterday', 'weeping sky', 'when all words fail us', 'whispers of the
enchanted forest', 'whispers of the heart', 'wilted roses on the vine',
'yearning for a glimpse of heaven']

Original values:
['the curse of loving you', 'endless roads', 'heartstrings symphony', 'eternal
solitude', 'crimson skies of longing']
==
['memories stitched in time', "hope's edge", "heaven's tears", 'the curse of
loving you', 'twisted paths', 'empty pages', 'bittersweet goodbye', 'longing for
a touch unfelt', 'yearning for a glimpse of heaven', 'dancing through the veil
of dreams', 'secrets we dare not speak', 'forged in fire', 'between the depths
and heights', 'lost in the embrace of night', 'echoes from the abyss', 'melodies
from a distant past', 'in the arms of a stranger', 'the enigma of love', 'when
all words fail us', 'violet rain', 'eternal flame', 'waves of yesterday',
'wading through rivers of tears', 'weeping sky', 'veiled sorrow', 'a whisper in
the midnight air', 'endless roads', 'the resonance of a silent cry', 'eclipsed',
"time's hold", 'cherry blossom tears', 'whispers of the enchanted forest',
'lonely skies', 'the silence between us', 'bleeding sun', 'unearthed secrets',
'heartstrings symphony', 'reckless love', 'the edge of eternity', 'cursed love',
"love's decline", 'the haunting echo of goodbye', 'ocean eyes', 'crimson sunset
sonata', 'final goodbye', 'eternal solitude', 'lost in the labyrinth of love',
'fading echoes', 'lost in the wilderness of love', 'surrendering to the night',
'sacred ground', "wandering through time's embrace", 'whispers of the heart',
'wandering the streets alone', 'echoes of the whispering wind', 'lost in the
twilight of hope', 'caught in the tangle of time', 'eternal melancholy',
'crimson skies of longing', 'glass castles', 'sailing on the sea of dreams',
'bridges burned and rivers crossed', 'fading glow', 'the mirage of a perfect
life', 'azure skies', 'lonesome road', 'wilted roses on the vine', 'cosmic
drift', 'a symphony written in tears', 'blind trust']
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
[['aria sterling'], ['ava morgan'], ['ava morgan', 'hazel winter'], ['lucas
grey'], ['phoenix rivers']]
==
[['apollo serenade'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['astrid nightshade'],
['astrid nightshade'], ['ava morgan'], ['ava morgan', 'hazel winter'], ['carter
knight'], ['carter knight'], ['dylan stone'], ['dylan stone'], ['eliana
harper'], ['eliana harper'], ['eliana harper'], ['eliana harper'], ['emily
rivers'], ['ethan wallace', 'noah bennett', 'nova harmony'], ['ethan wallace',
'noah bennett', 'nova harmony'], ['ethan wallace', 'noah bennett', 'nova
harmony'], ['ethan wallace', 'noah bennett', 'nova harmony'], ['evelyn rose'],
['evelyn rose'], ['felix blackwood'], ['felix blackwood'], ['felix blackwood'],
['grace harrison'], ['isabella cruz'], ['isabella cruz', 'jasper skye',
'seraphina dawn'], ['jasper skye'], ['liam palmer'], ['lily moon', 'zoey
james'], ['lily moon', 'zoey james'], ['lucas diaz', 'orion steele'], ['lucas
grey'], ['luna starlight'], ['marcus lane'], ['mia sullivan'], ['noah bennett'],
['noah bennett'], ['noah bennett'], ['noah bennett'], ['noah bennett'], ['noah
bennett'], ['noah bennett'], ['noah bennett'], ['noah bennett'], ['olivia
roberts'], ['phoenix rivers'], ['phoenix rivers'], ['seraphina dawn'], ['silent
thunder'], ['soulful reverie'], ['soulful reverie'], ['velvet echo'], ['velvet
echo'], ['violet cascade'], ['zoe mitchell'], ['zoey james'], ['zoey james'],
['zoey james'], ['zoey james'], ['zoey james']]

In right but not left:
[['apollo serenade'], ['astrid nightshade'], ['astrid nightshade'], ['carter
knight'], ['carter knight'], ['dylan stone'], ['dylan stone'], ['eliana
harper'], ['eliana harper'], ['eliana harper'], ['eliana harper'], ['emily
rivers'], ['ethan wallace', 'noah bennett', 'nova harmony'], ['ethan wallace',
'noah bennett', 'nova harmony'], ['ethan wallace', 'noah bennett', 'nova
harmony'], ['ethan wallace', 'noah bennett', 'nova harmony'], ['evelyn rose'],
['evelyn rose'], ['felix blackwood'], ['felix blackwood'], ['felix blackwood'],
['grace harrison'], ['isabella cruz'], ['isabella cruz', 'jasper skye',
'seraphina dawn'], ['jasper skye'], ['liam palmer'], ['lily moon', 'zoey
james'], ['lily moon', 'zoey james'], ['lucas diaz', 'orion steele'], ['luna
starlight'], ['marcus lane'], ['mia sullivan'], ['noah bennett'], ['noah
bennett'], ['noah bennett'], ['noah bennett'], ['noah bennett'], ['noah
bennett'], ['noah bennett'], ['noah bennett'], ['noah bennett'], ['olivia
roberts'], ['seraphina dawn'], ['silent thunder'], ['soulful reverie'],
['soulful reverie'], ['velvet echo'], ['velvet echo'], ['violet cascade'], ['zoe
mitchell'], ['zoey james'], ['zoey james'], ['zoey james'], ['zoey james'],
['zoey james']]

Original values:
[['lucas grey'], ['aria sterling'], ['ava morgan'], ['hazel winter', 'ava
morgan'], ['phoenix rivers']]
==
[['jasper skye', 'isabella cruz', 'seraphina dawn'], ['carter knight'], ['carter
knight'], ['lucas grey'], ['zoe mitchell'], ['evelyn rose'], ['evelyn rose'],
['eliana harper'], ['noah bennett'], ['noah bennett'], ['noah bennett'], ['noah
bennett'], ['noah bennett'], ['noah bennett'], ['noah bennett'], ['eliana
harper'], ['eliana harper'], ['mia sullivan'], ['marcus lane'], ['noah bennett',
'ethan wallace', 'nova harmony'], ['noah bennett', 'ethan wallace', 'nova
harmony'], ['noah bennett', 'ethan wallace', 'nova harmony'], ['noah bennett',
'ethan wallace', 'nova harmony'], ['luna starlight'], ['grace harrison'],
['eliana harper'], ['aria sterling'], ['violet cascade'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['aria sterling'],
['aria sterling'], ['aria sterling'], ['aria sterling'], ['ava morgan'],
['apollo serenade'], ['dylan stone'], ['zoey james'], ['zoey james'], ['zoey
james'], ['zoey james'], ['zoey james'], ['dylan stone'], ['hazel winter', 'ava
morgan'], ['noah bennett'], ['astrid nightshade'], ['lily moon', 'zoey james'],
['astrid nightshade'], ['noah bennett'], ['lucas diaz', 'orion steele'], ['lily
moon', 'zoey james'], ['felix blackwood'], ['felix blackwood'], ['felix
blackwood'], ['emily rivers'], ['olivia roberts'], ['phoenix rivers'], ['phoenix
rivers'], ['silent thunder'], ['jasper skye'], ['soulful reverie'], ['soulful
reverie'], ['liam palmer'], ['seraphina dawn'], ['velvet echo'], ['velvet
echo'], ['aria sterling'], ['isabella cruz']]