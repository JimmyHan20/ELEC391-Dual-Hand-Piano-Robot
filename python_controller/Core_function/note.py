LEFT_KEY_DEGREES = {
    "C2": 430,
    "D2": 625,
    "E2": 820,
    "F2": 1015,
    "G2": 1210,
    "A2": 1405,
    "B2": 1600,

    "C3": 1795,
    "D3": 1990,
    "E3": 2185,
    # "F3": 1227.4,
    # "G3": 1323.7,
    # "A3": 1420.0,
    # "B3": 1516.3,
}

RIGHT_KEY_DEGREES = {
    "F4": 2340,
    "F#4": 1093,
    "G4": 2145,
    "G#4": 998,
    "A4": 1950,
    "A#4": 903,
    "B4": 1755,

    "C5": 1560,
    "C#5": 713,
    "D5": 1365,
    "D#5": 618,
    "E5": 1170,
    "F5": 975,
    "F#5": 428,
    "G5": 780,
    "G#5": 333,
    "A5": 585,

    "A#5": 238,
    "B5": 390,
    "C6": 195,
    "C#6": 48,
    "D6": 0,
}

_FLAT_MAP = {
    "DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#",

    "DB2": "C#2", "EB2": "D#2", "GB2": "F#2", "AB2": "G#2", "BB2": "A#2",
    "DB3": "C#3", "EB3": "D#3", "GB3": "F#3", "AB3": "G#3", "BB3": "A#3",
    "DB4": "C#4", "EB4": "D#4", "GB4": "F#4", "AB4": "G#4", "BB4": "A#4",
    "DB5": "C#5", "EB5": "D#5", "GB5": "F#5", "AB5": "G#5", "BB5": "A#5",
    "DB6": "C#6", "EB6": "D#6", "GB6": "F#6", "AB6": "G#6", "BB6": "A#6",
}

def _normalise_note(note_name: str) -> str:
    n = str(note_name).strip().upper()
    return _FLAT_MAP.get(n, n)

def note_to_angle_left(note_name: str) -> float:
    n = _normalise_note(note_name)
    if n not in LEFT_KEY_DEGREES:
        raise ValueError(f"{note_name!r} not in left-hand range")
    return float(LEFT_KEY_DEGREES[n])

def note_to_angle_right(note_name: str) -> float:
    n = _normalise_note(note_name)
    if n not in RIGHT_KEY_DEGREES:
        raise ValueError(f"{note_name!r} not in right-hand range")
    return float(RIGHT_KEY_DEGREES[n])

def note_to_angle(note_name: str, hand: str = "right") -> float:
    hand = str(hand).strip().lower()
    if hand == "left":
        return note_to_angle_left(note_name)
    return note_to_angle_right(note_name)

def angle_to_note_left(degrees: float) -> str:
    if not LEFT_KEY_DEGREES:
        raise ValueError("LEFT_KEY_DEGREES is empty")
    closest = min(LEFT_KEY_DEGREES.items(), key=lambda kv: abs(float(kv[1]) - float(degrees)))
    return closest[0]

def angle_to_note_right(degrees: float) -> str:
    if not RIGHT_KEY_DEGREES:
        raise ValueError("RIGHT_KEY_DEGREES is empty")
    closest = min(RIGHT_KEY_DEGREES.items(), key=lambda kv: abs(float(kv[1]) - float(degrees)))
    return closest[0]

def angle_to_note(degrees: float, hand: str = "right") -> str:
    hand = str(hand).strip().lower()
    if hand == "left":
        return angle_to_note_left(degrees)
    return angle_to_note_right(degrees)