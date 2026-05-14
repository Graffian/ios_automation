import requests
import time
import base64
from PIL import Image
from io import BytesIO
import pytesseract

# ─────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────
WDA_URL    = "http://localhost:8100"
SESSION_ID = "96445C3F-3150-4258-925D-E0FE5CAFB0C7"
HEADERS    = {"Content-Type": "application/json"}

BOARD_WAIT = 1.4

TILE_COORDS = {
    0:  ( 254, 1151),  1:  ( 511, 1166),  2:  ( 770, 1160),  3:  (1037, 1151),
    4:  ( 235, 1420),  5:  ( 514, 1417),  6:  ( 762, 1401),  7:  (1072, 1429),
    8:  ( 244, 1693),  9:  ( 508, 1686),  10: ( 787, 1686),  11: (1059, 1664),
    12: ( 244, 1956),  13: ( 520, 1950),  14: ( 771, 1940),  15: (1075, 1962),
}

PAD        = 80
OCR_THRESH = 140
# Use psm 8 for most tiles — reliable single-char mode
OCR_CONFIG = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"

NUDGE_STEP  = 8
NUDGE_RANGE = 48
VOTE_OFFSETS = [(-10,0),(10,0),(0,-10),(0,10),(-10,-10),(10,10),(-10,10),(10,-10),(0,0)]


# ─────────────────────────────────────────
#  SCREENSHOT
# ─────────────────────────────────────────
def take_screenshot() -> Image.Image:
    url = f"{WDA_URL}/session/{SESSION_ID}/screenshot"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return Image.open(BytesIO(base64.b64decode(r.json()["value"])))


# ─────────────────────────────────────────
#  OCR - majority vote across nearby offsets
# ─────────────────────────────────────────
def _read_cell(img: Image.Image, cx: int, cy: int) -> str:
    """OCR one crop. Returns letter or empty string."""
    cell = img.crop((cx - PAD, cy - PAD, cx + PAD, cy + PAD)).convert("L")
    cell_up = cell.resize((cell.width * 4, cell.height * 4), Image.LANCZOS)
    cell_th = cell_up.point(lambda p: 0 if p < OCR_THRESH else 255)
    raw = pytesseract.image_to_string(cell_th, config=OCR_CONFIG).strip()
    return raw[0] if raw else ""


def _vote(img: Image.Image, cx: int, cy: int) -> str:
    """
    Sample 9 slightly offset crops and return the most common letter.
    This kills false reads caused by being slightly off-centre.
    """
    votes = {}
    for dx, dy in VOTE_OFFSETS:
        letter = _read_cell(img, cx + dx, cy + dy)
        if letter:
            votes[letter] = votes.get(letter, 0) + 1
    if not votes:
        return ""
    return max(votes, key=votes.get)


def ocr_tile(img: Image.Image, tile_idx: int) -> str:
    """
    Vote-based OCR at base coord. If result is empty, spiral-nudge
    outward and vote again. Saves winning coord back to TILE_COORDS.
    Returns uppercase letter or '?'.
    """
    base_cx, base_cy = TILE_COORDS[tile_idx]

    # Try base coord with voting first
    letter = _vote(img, base_cx, base_cy)
    if letter:
        return letter

    # Spiral nudge
    steps = range(-NUDGE_RANGE, NUDGE_RANGE + 1, NUDGE_STEP)
    for dy in steps:
        for dx in steps:
            if dx == 0 and dy == 0:
                continue
            cx = base_cx + dx
            cy = base_cy + dy
            letter = _vote(img, cx, cy)
            if letter:
                print(f"    nudge tile {tile_idx}: ({dx:+d},{dy:+d}) -> '{letter}' saved")
                TILE_COORDS[tile_idx] = (cx, cy)
                return letter

    return "?"


def ocr_board(img: Image.Image) -> list:
    letters = [ocr_tile(img, i).lower() for i in range(16)]
    board_str = "".join(l.upper() for l in letters)
    print(f"  OCR: {board_str[:4]} {board_str[4:8]} {board_str[8:12]} {board_str[12:]}")
    return letters


# ─────────────────────────────────────────
#  DICTIONARY
# ─────────────────────────────────────────
def load_dictionary(path: str = "words.txt"):
    try:
        with open(path) as f:
            words = {w.strip().lower() for w in f if 3 <= len(w.strip()) <= 8}
        prefixes = set()
        for w in words:
            for i in range(1, len(w) + 1):
                prefixes.add(w[:i])
        print(f"  Loaded {len(words):,} words ({len(prefixes):,} prefixes)")
        return words, prefixes
    except FileNotFoundError:
        print("  words.txt not found! Run:")
        print("  curl -o words.txt https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt")
        raise SystemExit(1)


# ─────────────────────────────────────────
#  SOLVER
# ─────────────────────────────────────────
def _build_neighbours():
    nb = {}
    for idx in range(16):
        r, c = divmod(idx, 4)
        nb[idx] = [
            (r + dr) * 4 + (c + dc)
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            if (dr != 0 or dc != 0)
            and 0 <= r + dr < 4
            and 0 <= c + dc < 4
        ]
    return nb

NEIGHBOURS = _build_neighbours()


def solve_board(letters, words, prefixes):
    found = {}

    def dfs(idx, word, path, visited):
        if word not in prefixes:
            return
        if len(word) >= 3 and word in words:
            if word not in found or len(path) > len(found[word]):
                found[word] = list(path)
        if len(word) == 8:
            return
        for nb in NEIGHBOURS[idx]:
            if nb not in visited:
                visited.add(nb)
                path.append(nb)
                dfs(nb, word + letters[nb], path, visited)
                path.pop()
                visited.remove(nb)

    for i, ch in enumerate(letters):
        if ch != "?":
            dfs(i, ch, [i], {i})

    return dict(sorted(found.items(), key=lambda x: len(x[0]), reverse=True))


# ─────────────────────────────────────────
#  SWIPE  — fixed W3C format for WDA
# ─────────────────────────────────────────
def swipe_path(indices: list) -> bool:
    """
    Sends a W3C Actions touch swipe to WebDriverAgent.
    Holds the first tile, then slowly drags through the rest.
    """
    url = f"{WDA_URL}/session/{SESSION_ID}/actions"

    sx, sy = TILE_COORDS[indices[0]]

    pointer_actions = [
        # 1. Move finger to the first tile (instant, no drag yet)
        {"type": "pointerMove", "duration": 0, "x": sx, "y": sy, "origin": "viewport"},
        # 2. Press down
        {"type": "pointerDown", "button": 0},
        # 3. HOLD — give the game time to register the first tile (crucial!)
        {"type": "pause", "duration": 200},
    ]

    for idx in indices[1:]:
        tx, ty = TILE_COORDS[idx]
        pointer_actions.append({
            "type": "pointerMove",
            "duration": 180,      # slow enough for the game to register each tile
            "x": tx,
            "y": ty,
            "origin": "viewport",
        })
        # Small pause on each tile so it's registered before moving off
        pointer_actions.append({"type": "pause", "duration": 60})

    # 4. Lift finger
    pointer_actions.append({"type": "pointerUp", "button": 0})

    payload = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": pointer_actions,
            }
        ]
    }

    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"    swipe error {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"    swipe exception: {e}")
        return False


# ─────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────
def run():
    words, prefixes = load_dictionary("words.txt")

    print("\n" + "=" * 50)
    print("   Boggle Bot  -  Screenshot -> OCR -> Solve -> Swipe")
    print("=" * 50)
    print("Ctrl+C to stop.\n")

    while True:
        try:
            # 1. Screenshot
            print("Taking screenshot...")
            img = take_screenshot()

            # 2. OCR with majority voting + auto-nudge
            letters = ocr_board(img)
            bad = letters.count("?")
            if bad:
                print(f"  {bad} tile(s) unreadable after nudging - retrying in 1s...")
                time.sleep(1)
                continue

            # 3. Solve
            t0 = time.perf_counter()
            results = solve_board(letters, words, prefixes)
            elapsed = time.perf_counter() - t0
            print(f"  {len(results)} words in {elapsed:.3f}s")

            if not results:
                print("  No words found")
                time.sleep(2)
                continue

            top = list(results.items())[:20]
            preview = ", ".join(w.upper() for w, _ in top[:8])
            print(f"  Top: {preview}{'...' if len(top) > 8 else ''}\n")

            # 4. Swipe each word
            for word, path in top:
                print(f"  > {word.upper():<10} tiles={path}", end="  ")
                ok = swipe_path(path)
                print("OK" if ok else "SWIPE FAILED")
                time.sleep(BOARD_WAIT)

            print("\n  Done - grabbing next board...\n")
            time.sleep(0.3)

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run()
