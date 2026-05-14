import requests
import time
import base64
from PIL import Image
from io import BytesIO
import pytesseract

# ─────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────
WDA_URL   = "http://localhost:8100"
HEADERS   = {"Content-Type": "application/json"}

BOARD_WAIT = 2.2   # seconds between word swipes

TILE_COORDS = {
     0: (  84,  383),   # screen=(254,1151)  ÷ 3.0
     1: ( 170,  388),   # screen=(511,1166)  ÷ 3.0
     2: ( 256,  386),   # screen=(770,1160)  ÷ 3.0
     3: ( 345,  383),   # screen=(1037,1151) ÷ 3.0
     4: (  78,  473),   # screen=(235,1420)  ÷ 3.0
     5: ( 171,  472),   # screen=(514,1417)  ÷ 3.0
     6: ( 254,  467),   # screen=(762,1401)  ÷ 3.0
     7: ( 357,  476),   # screen=(1072,1429) ÷ 3.0
     8: (  81,  564),   # screen=(244,1693)  ÷ 3.0
     9: ( 169,  562),   # screen=(508,1686)  ÷ 3.0
    10: ( 262,  562),   # screen=(787,1686)  ÷ 3.0
    11: ( 353,  554),   # screen=(1059,1664) ÷ 3.0
    12: (  81,  652),   # screen=(244,1956)  ÷ 3.0
    13: ( 173,  650),   # screen=(520,1950)  ÷ 3.0
    14: ( 257,  646),   # screen=(771,1940)  ÷ 3.0
    15: ( 358,  654),   # screen=(1075,1962) ÷ 3.0
}

COORD_SCALE = 3.0  # screenshot pixels per logical point
PAD         = 80   # crop radius in screenshot pixels
OCR_THRESH  = 140
OCR_CONFIG  = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NUDGE_STEP  = 3    # logical pts (≈8 screen px ÷ 3)
NUDGE_RANGE = 16   # logical pts (≈48 screen px ÷ 3)
VOTE_OFFSETS = [(-3,0),(3,0),(0,-3),(0,3),(-3,-3),(3,3),(-3,3),(3,-3),(0,0)]

# ── Swipe timing ──
HOLD_MS      = 350   # press-and-hold on tile 0 before dragging
MOVE_MS      = 60    # ms per interpolated micro-step
DWELL_MS     = 100   # pause on each tile centre
INTERP_STEPS = 6     # waypoints injected between every two tiles


# ─────────────────────────────────────────
#  SESSION MANAGEMENT
# ─────────────────────────────────────────
_session_id = None


def _create_session():
    r = requests.post(
        f"{WDA_URL}/session",
        json={"capabilities": {"alwaysMatch": {}}},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"No sessionId in WDA response: {data}")
    print(f"  [WDA] New session: {sid}")
    return sid


def get_session():
    global _session_id
    if _session_id:
        try:
            r = requests.get(f"{WDA_URL}/session/{_session_id}", timeout=5)
            if r.status_code == 200:
                return _session_id
        except Exception:
            pass
        print("  [WDA] Session expired — creating new one...")
        _session_id = None

    try:
        r = requests.get(f"{WDA_URL}/status", timeout=8)
        sid = (
            r.json().get("sessionId")
            or r.json().get("value", {}).get("sessionId")
        )
        if sid:
            print(f"  [WDA] Reusing session: {sid}")
            _session_id = sid
            return _session_id
    except Exception:
        pass

    _session_id = _create_session()
    return _session_id


# ─────────────────────────────────────────
#  SCREENSHOT
# ─────────────────────────────────────────
def take_screenshot(retries=3):
    for attempt in range(retries):
        try:
            sid = get_session()
            r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=15)
            if r.status_code == 404:
                global _session_id
                _session_id = None
                continue
            r.raise_for_status()
            raw = r.json().get("value", "")
            if isinstance(raw, dict):
                raw = raw.get("value", "")
            return Image.open(BytesIO(base64.b64decode(raw)))
        except Exception as e:
            print(f"  [screenshot] attempt {attempt + 1} failed: {e}")
            time.sleep(1)
    raise RuntimeError("Screenshot failed after all retries.")


# ─────────────────────────────────────────
#  OCR
# ─────────────────────────────────────────
def _read_cell(img, cx, cy):
    # cx/cy are logical points — scale up to screenshot pixels for cropping
    px, py = int(cx * COORD_SCALE), int(cy * COORD_SCALE)
    cell = img.crop((px - PAD, py - PAD, px + PAD, py + PAD)).convert("L")
    cell_up = cell.resize((cell.width * 4, cell.height * 4), Image.LANCZOS)
    cell_th = cell_up.point(lambda p: 0 if p < OCR_THRESH else 255)
    raw = pytesseract.image_to_string(cell_th, config=OCR_CONFIG).strip()
    return raw[0] if raw else ""


def _vote(img, cx, cy):
    votes = {}
    for dx, dy in VOTE_OFFSETS:
        letter = _read_cell(img, cx + dx, cy + dy)
        if letter:
            votes[letter] = votes.get(letter, 0) + 1
    return max(votes, key=votes.get) if votes else ""


def ocr_tile(img, tile_idx):
    base_cx, base_cy = TILE_COORDS[tile_idx]
    letter = _vote(img, base_cx, base_cy)
    if letter:
        return letter
    steps = range(-NUDGE_RANGE, NUDGE_RANGE + 1, NUDGE_STEP)
    for dy in steps:
        for dx in steps:
            if dx == 0 and dy == 0:
                continue
            cx, cy = base_cx + dx, base_cy + dy
            letter = _vote(img, cx, cy)
            if letter:
                print(f"    nudge tile {tile_idx}: ({dx:+d},{dy:+d}) -> '{letter}'")
                TILE_COORDS[tile_idx] = (cx, cy)
                return letter
    return "?"


def ocr_board(img):
    letters = [ocr_tile(img, i).lower() for i in range(16)]
    board_str = "".join(l.upper() for l in letters)
    print(f"  OCR: {board_str[:4]} {board_str[4:8]} {board_str[8:12]} {board_str[12:]}")
    return letters


# ─────────────────────────────────────────
#  DICTIONARY
# ─────────────────────────────────────────
def load_dictionary(path="words.txt"):
    try:
        with open(path) as f:
            words = {w.strip().lower() for w in f if 3 <= len(w.strip()) <= 8}
        prefixes = set()
        for w in words:
            for i in range(1, len(w) + 1):
                prefixes.add(w[:i])
        print(f"  Loaded {len(words):,} words  ({len(prefixes):,} prefixes)")
        return words, prefixes
    except FileNotFoundError:
        print("  words.txt not found — run:")
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
#  SWIPE  — W3C Actions with manual interpolation
#
#  Key fixes vs previous versions:
#
#  1. NO 'button' field in pointerDown/Up
#     touch pointers don't have buttons; sending button:0 makes some WDA
#     builds silently ignore the whole action sequence.
#
#  2. Manual waypoint interpolation (INTERP_STEPS micro-moves per tile pair)
#     WDA does NOT move through intermediate screen positions when you only
#     give start → end coords. The Boggle game detects tiles via hit-boxes,
#     so we physically walk the finger through each tile ourselves.
#
#  3. DELETE /actions before each swipe
#     Clears any stuck touch state left over from a previous crashed gesture.
# ─────────────────────────────────────────
def swipe_path(indices):
    sid = get_session()

    # Clear stuck touch state
    try:
        requests.delete(f"{WDA_URL}/session/{sid}/actions", timeout=5)
    except Exception:
        pass

    sx, sy = TILE_COORDS[indices[0]]

    acts = [
        {"type": "pointerMove", "duration": 0, "x": int(sx), "y": int(sy)},
        {"type": "pointerDown"},          # ← no 'button' field
        {"type": "pause", "duration": HOLD_MS},
    ]

    prev_x, prev_y = sx, sy
    for idx in indices[1:]:
        tx, ty = TILE_COORDS[idx]

        # Walk the finger through INTERP_STEPS positions so every
        # tile's hit-box is crossed during the drag
        for step in range(1, INTERP_STEPS + 1):
            t = step / INTERP_STEPS
            ix = int(prev_x + (tx - prev_x) * t)
            iy = int(prev_y + (ty - prev_y) * t)
            acts.append({"type": "pointerMove", "duration": MOVE_MS, "x": ix, "y": iy})

        # Dwell on the tile centre
        acts.append({"type": "pause", "duration": DWELL_MS})
        prev_x, prev_y = tx, ty

    acts.append({"type": "pointerUp"})    # ← no 'button' field

    payload = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": acts,
            }
        ]
    }

    try:
        r = requests.post(
            f"{WDA_URL}/session/{sid}/actions",
            json=payload,
            headers=HEADERS,
            timeout=20,
        )
        if r.status_code == 200:
            return True
        print(f"    [swipe] {r.status_code}: {r.text[:300]}")
        return False
    except Exception as e:
        print(f"    [swipe] exception: {e}")
        return False


# ─────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────
def run():
    words, prefixes = load_dictionary("words.txt")

    print("\n" + "=" * 54)
    print("   Boggle Bot — Screenshot → OCR → Solve → Swipe")
    print("=" * 54)
    print("Ctrl+C to stop.\n")

    try:
        get_session()
    except Exception as e:
        print(f"  [WDA] Could not connect: {e}")
        print("  Make sure WebDriverAgent is running on port 8100.\n")

    played: set[str] = set()   # words already used on this board
    last_letters: list = []

    while True:
        try:
            # ── Screenshot + OCR ──────────────────────────────────────
            img = take_screenshot()
            letters = ocr_board(img)

            if letters.count("?") > 0:
                print(f"  Unreadable tiles — retrying...")
                time.sleep(0.8)
                continue

            # ── If the board changed, reset played set ─────────────────
            if letters != last_letters:
                played.clear()
                last_letters = letters[:]
                t0 = time.perf_counter()
                results = solve_board(letters, words, prefixes)
                elapsed = time.perf_counter() - t0
                preview = ", ".join(w.upper() for w in list(results)[:6])
                print(f"  {len(results)} words  ({elapsed:.3f}s)  Top: {preview}")

            # ── Pick best word not yet played ─────────────────────────
            # Sort: longest first, then alphabetical as tiebreak
            remaining = [(w, p) for w, p in results.items() if w not in played]
            if not remaining:
                print("  All words played — waiting for new board...")
                time.sleep(1.5)
                continue

            word, path = remaining[0]
            played.add(word)

            print(f"  ▶  {word.upper():<10}  tiles={path}", end="  ")
            ok = swipe_path(path)
            print("✓" if ok else "✗ FAILED")

            # Wait for the tile-replacement animation to finish
            time.sleep(BOARD_WAIT)

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run()