---
name: civ6-adjacency-optimizer
description: Optimize district placement for adjacency bonuses in Civilization 6 map scenarios
---

# Civ6 Adjacency Bonus Optimizer

## Critical: Time Management
This task is complex. Do NOT spend excessive time analyzing hex conventions or river data. Focus on:
1. Parse the map quickly
2. Identify key features (mountains, geothermal fissures, reefs, rivers, resources)  
3. Place districts to maximize adjacency bonuses
4. **Accurately calculate adjacency bonuses** — the submitted values MUST match what the verifier calculates

## Adjacency Mismatch is Fatal
The verifier independently calculates adjacency bonuses. If your submitted total doesn't match, the solution is **invalid (score=0)**. Always let the verifier's rules determine your numbers. When in doubt, calculate conservatively and verify.

## Key Adjacency Rules (Civ6)

### CAMPUS
- +2 per adjacent Geothermal Fissure
- +2 per adjacent Reef  
- +1 per adjacent Mountain
- +1 per 2 adjacent districts (including City Center)

### COMMERCIAL_HUB
- +2 per adjacent Harbor
- +2 per adjacent river (tile is on a river)
- +1 per 2 adjacent districts

### HARBOR
- +2 per adjacent City Center
- +1 per 2 adjacent districts
- Must be placed on coast

### INDUSTRIAL_ZONE  
- +1 per adjacent Mine/Quarry
- +1 per adjacent district
- +2 per adjacent Aqueduct

### HOLY_SITE
- +2 per adjacent Natural Wonder
- +1 per adjacent Mountain
- +1 per 2 adjacent Woods/adjacent districts

## Hex Grid (.Civ6Map is SQLite)
The map file is a SQLite database. Key tables:
- `Plots`: ID, TerrainType, IsImpassable
- `PlotFeatures`: ID, FeatureType  
- `PlotRivers`: river edge data
- `MapSize` or infer from data: WIDTH, HEIGHT

Plot ID = y * WIDTH + x (row-major, 0-indexed)

### Hex Neighbors (offset coordinates)
The game uses offset hex coordinates. For even-row offset (even_r):
```python
# even row (y % 2 == 0):
neighbors = [(x+1,y), (x+1,y-1), (x,y-1), (x-1,y), (x,y+1), (x+1,y+1)]
# odd row (y % 2 == 1):  
neighbors = [(x+1,y), (x,y-1), (x-1,y-1), (x-1,y), (x-1,y+1), (x,y+1)]
```

## Strategy
1. Parse map, identify all land tiles and features
2. For single city: try placing city center at promising locations (near mountains, features)
3. For each city center candidate, enumerate valid district placements within 3-tile radius
4. Score each configuration and pick the best
5. **Double-check adjacency calculation before submitting**

## Output Format
Single city:
```json
{
  "city_center": [x, y],
  "placements": {"CAMPUS": [x, y], ...},
  "adjacency_bonuses": {"CAMPUS": 6, ...},
  "total_adjacency": 11
}
```
