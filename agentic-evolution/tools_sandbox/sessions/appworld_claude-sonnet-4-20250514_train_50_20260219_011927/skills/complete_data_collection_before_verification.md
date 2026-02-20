# complete_data_collection_before_verification

**Scope:** 🎯 Tactical Patch

**Description:** Ensures agent completes all data collection steps before attempting verification or comparison operations

**When to use:** when task requires comparing data from multiple sources, when verification is needed, before attempting to reference collected data, when you encounter NameError during execution, at the start of multi-step data collection tasks

## Instructions

# Data Collection Before Verification Protocol

## Core Principle
**ALWAYS complete ALL data collection steps BEFORE attempting any verification, comparison, or analysis.**

## Execution Order

### Phase 1: Data Collection (MUST complete first)
1. Authenticate to all required apps
2. Make all necessary API calls
3. Paginate through ALL results if needed
4. Store collected data in variables
5. Verify that all required variables are defined and populated

### Phase 2: Verification (ONLY after Phase 1 is complete)
6. Now you can compare data
7. Now you can verify conditions
8. Now you can check for matches

## Example Pattern

```python
# ✅ CORRECT ORDER

# Phase 1: Collect ALL data first
spotify_tracks = paginate_api_results(...)  # Get all Spotify tracks
all_saved_tracks = [track for track in spotify_tracks['all_items']]  # Process data

lastfm_tracks = paginate_api_results(...)  # Get all Last.fm tracks  
all_lastfm_tracks = [track for track in lastfm_tracks['all_items']]  # Process data

# Phase 2: NOW verify after all data is collected
matching_tracks = [t for t in all_saved_tracks if t['name'] in lastfm_track_names]
verification_result = len(matching_tracks) > 0
```

```python
# ❌ WRONG ORDER (will cause NameError)

# Started verification before collecting all data
spotify_tracks = paginate_api_results(...)
verification_result = len(all_saved_tracks) > 0  # ERROR: all_saved_tracks not defined yet!

# Then tried to collect more data
all_saved_tracks = [track for track in spotify_tracks['all_items']]
```

## Checklist Before Verification

Before running ANY verification code, ask yourself:
- [ ] Have I collected data from ALL required sources?
- [ ] Are ALL variables I need already defined?
- [ ] Have I completed ALL pagination?
- [ ] Have I processed and stored ALL data?

If ANY answer is NO, continue data collection. Do NOT start verification.

## When This Applies
- Multi-app data comparison tasks
- Tasks requiring data from multiple API endpoints
- Any task with verification requirements
- Tasks involving filtering or matching across datasets

## Red Flags
- Attempting to reference a variable before it's assigned
- Writing verification logic in the middle of data collection
- Using variables in conditions before they're populated
- Checking results before all API calls are complete

---
*Created: 2026-02-19T03:05:35.140346*
*Version: 1*
*Scope: tactical_patch*
*TTL: 150 episodes*
