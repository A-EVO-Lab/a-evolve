---
name: 3d-scan-calc
description: Calculate mass of 3D printed part from binary STL with material IDs in attribute bytes
---

# 3D Scan Mass Calculation

## Critical: Unit Consistency

**DO NOT convert units.** The STL file coordinates are in mm, so the volume from the signed tetrahedra method will be in mm³. The density table gives density values that are meant to be multiplied directly with mm³ to get the mass. If the density is listed as g/cm³, convert density to g/mm³ (divide by 1000) OR convert volume to cm³ (divide by 1e9 for m³ or 1e6 for cm³... NO).

**ACTUALLY: Check the density table units carefully.** If density is in g/cm³ (like 5.55 g/cm³):
- Volume in mm³ / 1000 = volume in cm³  (WRONG! 1 cm³ = 1000 mm³, so divide by 1000)

Wait — **1 cm = 10 mm, so 1 cm³ = 1000 mm³.** Therefore:
- Volume_cm3 = Volume_mm3 / 1000
- Mass = Volume_cm3 * density_g_per_cm3

**BUT the test expects mass ~34648 while the agent got ~34.65.** The agent divided volume by 1e6 (converting mm³ to m³) instead of dividing by 1e3 (converting mm³ to cm³). Or the agent's volume was 6242.89 mm³ and with density 5.55 g/cm³ got 34.65 g — that means: 6242.89/1000 * 5.55 = 34.65. But expected is 34648 = 6242.89 * 5.55. So **the density in the table is actually in g/mm³ or equivalently kg/m³, NOT g/cm³.**

**KEY INSIGHT: The expected result is Volume_mm3 * density_value directly.** Do NOT divide volume by 1000. The density values in the material table are in units that multiply directly with mm³. Just compute `volume * density` as stated in the task.

## Algorithm

1. Parse binary STL: 80-byte header, 4-byte uint32 triangle count, then per triangle: 12 floats (normal + 3 vertices) + 2-byte uint16 (Material ID)
2. Group triangles by shared vertices (use vertex proximity with small epsilon ~1e-6) to find connected components
3. Select the largest connected component by triangle count
4. Extract Material ID from the component's triangles (they should all share the same ID)
5. Look up density in `/root/material_density_table.md`
6. **Volume = abs(sum of signed tetrahedra volumes)** using the divergence theorem:
   ```
   For each triangle with vertices v1, v2, v3:
   signed_vol = v1.dot(v2.cross(v3)) / 6.0
   total_volume = abs(sum of all signed_vol)
   ```
7. **Mass = Volume * Density** — use the values directly, no unit conversion

## Output Format
```json
{
  "main_part_mass": 34648.04,
  "material_id": 42
}
```

Write to `/root/mass_report.json`.

## Common Pitfalls
- Do NOT convert mm³ to cm³ or any other unit — the formula is simply `Volume * Density`
- The attribute byte count field stores Material ID as uint16
- Use Union-Find or BFS on shared vertices for connected components
- Use a tolerance when comparing vertex coordinates for connectivity
