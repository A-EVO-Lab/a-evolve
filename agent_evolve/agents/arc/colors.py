"""Color constants for ARC-AGI-3 grids.

Adapted from symbolica-ai/ARC-AGI-3-Agents.
"""

COLOR_NAMES: tuple[str, ...] = (
    "white",       # 0
    "off-white",   # 1
    "light gray",  # 2
    "gray",        # 3
    "off-black",   # 4
    "black",       # 5
    "magenta",     # 6
    "light magenta",  # 7
    "red",         # 8
    "blue",        # 9
    "light blue",  # 10
    "yellow",      # 11
    "orange",      # 12
    "maroon",      # 13
    "green",       # 14
    "purple",      # 15
)

PALETTE_HEX: tuple[str, ...] = (
    "#FFFFFF",  # 0  White
    "#CCCCCC",  # 1  Off-white
    "#999999",  # 2  Neutral light
    "#666666",  # 3  Neutral
    "#333333",  # 4  Off-black
    "#000000",  # 5  Black
    "#E53AA3",  # 6  Magenta
    "#FF7BCC",  # 7  Magenta light
    "#F93C31",  # 8  Red
    "#1E93FF",  # 9  Blue
    "#88D8F1",  # 10 Blue light
    "#FFDC00",  # 11 Yellow
    "#FF851B",  # 12 Orange
    "#921231",  # 13 Maroon
    "#4FCC30",  # 14 Green
    "#A356D6",  # 15 Purple
)

PALETTE_RGBA: list[tuple[int, int, int, int]] = [
    (0xFF, 0xFF, 0xFF, 0xFF),  # 0  White
    (0xCC, 0xCC, 0xCC, 0xFF),  # 1  Off-white
    (0x99, 0x99, 0x99, 0xFF),  # 2  Neutral light
    (0x66, 0x66, 0x66, 0xFF),  # 3  Neutral
    (0x33, 0x33, 0x33, 0xFF),  # 4  Off-black
    (0x00, 0x00, 0x00, 0xFF),  # 5  Black
    (0xE5, 0x3A, 0xA3, 0xFF),  # 6  Magenta
    (0xFF, 0x7B, 0xCC, 0xFF),  # 7  Magenta light
    (0xF9, 0x3C, 0x31, 0xFF),  # 8  Red
    (0x1E, 0x93, 0xFF, 0xFF),  # 9  Blue
    (0x88, 0xD8, 0xF1, 0xFF),  # 10 Blue light
    (0xFF, 0xDC, 0x00, 0xFF),  # 11 Yellow
    (0xFF, 0x85, 0x1B, 0xFF),  # 12 Orange
    (0x92, 0x12, 0x31, 0xFF),  # 13 Maroon
    (0x4F, 0xCC, 0x30, 0xFF),  # 14 Green
    (0xA3, 0x56, 0xD6, 0xFF),  # 15 Purple
]

COLOR_LEGEND: str = ", ".join(f"{i}: {n}" for i, n in enumerate(COLOR_NAMES))
