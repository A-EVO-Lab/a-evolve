# select_playlists_for_duration

**Scope:** 📚 Strategic Skill

**Description:** Select and queue multiple playlists to cover a required duration with distinct songs

**When to use:** task requires music/playlist for specific duration, task mentions 'enough songs for X minutes', task involves workout, study session, or extended listening period, before implementing any duration-based music solution

## Instructions

Step 1: Identify the required duration from the task (e.g., 85 minutes for a workout).

Step 2: Search for relevant playlists matching the task context (e.g., workout playlists, genre preferences).

Step 3: For each candidate playlist, retrieve its duration/length information.

Step 4: Calculate cumulative duration:
- Start with an empty list of selected playlists
- Add playlists one by one, tracking total duration
- Continue until total duration >= required duration
- Aim for 10-20% buffer above required duration for safety

Step 5: Verify distinct song coverage:
- If possible, check that selected playlists have different songs (not duplicates)
- Prefer diverse playlists over similar ones

Step 6: Queue all selected playlists in order:
- Use appropriate API calls to add each playlist to the queue
- Verify each playlist was successfully added

Step 7: Start playback of the queued content.

Step 8: Verify final state:
- Total queued duration >= required duration
- Multiple playlists queued (not just one looping)
- Playback has started

Example: For 85-minute workout:
- Find workout playlist A (38 min)
- Find workout playlist B (32 min)
- Find workout playlist C (25 min)
- Total: 95 minutes (sufficient)
- Queue all three playlists
- Start playback

---
*Created: 2026-02-19T02:48:38.388439*
*Version: 1*
*Scope: strategic_skill*
