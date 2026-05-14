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

BOARD_WAIT = 2.0   # seconds between word swipes (give UI time to settle)

TILE_COORDS = {
    0:  ( 254, 1151),  1:  ( 511, 1166),  2:  ( 770, 1160),  3:  (1037, 1151),
    4:  ( 235, 1420),  5:  ( 514, 1417),  6:  ( 762, 1401),  7:  (1072, 1429),
    8:  ( 244, 1693),  9:  ( 508, 1686),  10: ( 787, 1686),  11: (1059, 1664),
    12: ( 244, 1956),  13: ( 520, 1950),  14: ( 771, 1940),  15: (1075, 1962),
}

PAD         = 80
OCR_THRESH  = 140
OCR_CONFIG  = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NUDGE_STEP  = 8
NUDGE_RANGE = 48
VOTE_OFFSETS = [(-10,0),(10,0),(0,-10),(0,10),(-10,-10),(10,10),(-10,10),(10,-10),(0,0)]

# Swipe timing
HOLD_MS     = 300   # hold on first tile before dragging
MOVE_MS     = 200   # time to slide between each tile
DWELL_MS    = 80    # pause ON each tile so it registers


# ─────────────────────────────────────────
#  SESSION MANAGEMENT  (auto-create / recover)
# ─────────────────────────────────────────
_session_id: str | None = None


def _create_session() -> str:
    """Ask WDA to create a new session and return its ID."""
    r = requests.post(
        f"{WDA_URL}/session",
        json={"capabilities": {"alwaysMatch": {}}},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    # WDA wraps the response in {"value": {"sessionId": ...}}
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"No sessionId in WDA response: {data}")
    print(f"  [WDA] New session: {sid}")
    return sid


def get_session() -> str:
    """Return a live session ID, (re-)creating one if needed."""
    global _session_id
    if _session_id:
        try:
            r = requests.get(f"{WDA_URL}/session/{_session_id}", timeout=5)
            if r.status_code == 200:
                return _session_id
        except Exception:
            pass
        print("  [WDA] Session dead — creating new one...")
        _session_id = None

    # Check if WDA already has an active session via /status
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=8)
        status = r.json()
        sid = (
            status.get("sessionId")
            or status.get("value", {}).get("sessionId")
        )
        if sid:
            print(f"  [WDA] Reusing existing session: {sid}")
            _session_id = sid
            return _session_id
    except Exception:
        pass

    _session_id = _create_session()
    return _session_id


# ─────────────────────────────────────────
#  SCREENSHOT  (with retry + session recovery)
# ─────────────────────────────────────────
def take_screenshot(retries: int = 3) -> Image.Image:
    for attempt in range(retries):
        try:
            sid = get_session()
            r = requests.get(
                f"{WDA_URL}/session/{sid}/screenshot",
                timeout=15,
            )
            if r.status_code == 404:
                # Session vanished mid-flight
                global _session_id
                _session_id = None
                continue
            r.raise_for_status()
            raw = r.json().get("value") or r.json().get("value", {})
            # WDA returns either {"value": "<base64>"} or nested
            if isinstance(raw, dict):
                raw = raw.get("value", "")
            img = Image.open(BytesIO(base64.b64decode(raw)))
            return img
        except Exception as e:
            print(f"  [screenshot] attempt {attempt+1} failed: {e}")
            time.sleep(1)
    raise RuntimeError("Screenshot failed after all retries.")


# ─────────────────────────────────────────
#  OCR
# ─────────────────────────────────────────
def _read_cell(img: Image.Image, cx: int, cy: int) -> str:
    cell = img.crop((cx - PAD, cy - PAD, cx + PAD, cy + PAD)).convert("L")
    cell_up = cell.resize((cell.width * 4, cell.height * 4), Image.LANCZOS)
    cell_th = cell_up.point(lambda p: 0 if p < OCR_THRESH else 255)
    raw = pytesseract.image_to_string(cell_th, config=OCR_CONFIG).strip()
    return raw[0] if raw else ""


def _vote(img: Image.Image, cx: int, cy: int) -> str:
    votes: dict[str, int] = {}
    for dx, dy in VOTE_OFFSETS:
        letter = _read_cell(img, cx + dx, cy + dy)
        if letter:
            votes[letter] = votes.get(letter, 0) + 1
    return max(votes, key=votes.get) if votes else ""


def ocr_tile(img: Image.Image, tile_idx: int) -> str:
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


def ocr_board(img: Image.Image) -> list[str]:
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
        prefixes: set[str] = set()
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
def _build_neighbours() -> dict[int, list[int]]:
    nb: dict[int, list[int]] = {}
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


def solve_board(letters: list[str], words: set, prefixes: set) -> dict:
    found: dict[str, list[int]] = {}

    def dfs(idx: int, word: str, path: list[int], visited: set[int]):
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
#  SWIPE  — JSONWP touch/perform (most reliable for WDA game gestures)
#
#  This uses the legacy JSONWP TouchAction API which WDA still supports
#  and which works far better than W3C actions for continuous drag gestures.
#
#  Gesture shape:
#    press (hold HOLD_MS) → moveTo tile2 (MOVE_MS) → wait DWELL_MS
#                         → moveTo tile3 (MOVE_MS) → wait DWELL_MS ...
#                         → release
# ─────────────────────────────────────────
def _swipe_jsonwp(indices: list[int]) -> bool:
    sid = get_session()
    url = f"{WDA_URL}/session/{sid}/touch/perform"

    sx, sy = TILE_COORDS[indices[0]]

    actions = [
        # Press and hold on first tile — this is the key gesture the game needs
        {"action": "press",  "options": {"x": sx, "y": sy}},
        {"action": "wait",   "options": {"ms": HOLD_MS}},
    ]

    for idx in indices[1:]:
        tx, ty = TILE_COORDS[idx]
        actions.append({"action": "moveTo", "options": {"x": tx, "y": ty, "duration": MOVE_MS}})
        actions.append({"action": "wait",   "options": {"ms": DWELL_MS}})

    actions.append({"action": "release"})

    try:
        r = requests.post(url, json={"actions": actions}, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return True
        print(f"    [JSONWP swipe] {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"    [JSONWP swipe] exception: {e}")
        return False


def _swipe_w3c(indices: list[int]) -> bool:
    """
    W3C Actions fallback — used only if JSONWP fails.
    NOTE: do NOT pass 'origin' — it breaks on many WDA versions.
    """
    sid = get_session()
    url = f"{WDA_URL}/session/{sid}/actions"

    sx, sy = TILE_COORDS[indices[0]]

    acts = [
        {"type": "pointerMove", "duration": 0, "x": sx, "y": sy},
        {"type": "pointerDown", "button": 0},
        {"type": "pause",       "duration": HOLD_MS},
    ]
    for idx in indices[1:]:
        tx, ty = TILE_COORDS[idx]
        acts.append({"type": "pointerMove", "duration": MOVE_MS, "x": tx, "y": ty})
        acts.append({"type": "pause",       "duration": DWELL_MS})
    acts.append({"type": "pointerUp", "button": 0})

    payload = {
        "actions": [{
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": acts,
        }]
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return True
        print(f"    [W3C swipe] {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"    [W3C swipe] exception: {e}")
        return False


def swipe_path(indices: list[int]) -> bool:
    """Try JSONWP first (best for games), fall back to W3C."""
    if _swipe_jsonwp(indices):
        return True
    print("    JSONWP failed — trying W3C fallback...")
    return _swipe_w3c(indices)


# ─────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────
def run():
    words, prefixes = load_dictionary("words.txt")

    print("\n" + "=" * 54)
    print("   Boggle Bot — Screenshot → OCR → Solve → Swipe")
    print("=" * 54)
    print("Ctrl+C to stop.\n")

    # Warm up session early so first screenshot doesn't fail
    try:
        get_session()
    except Exception as e:
        print(f"  [WDA] Could not connect: {e}")
        print("  Make sure WebDriverAgent is running on port 8100.\n")

    while True:
        try:
            # ── 1. Screenshot ──────────────────────────
            print("Taking screenshot...")
            img = take_screenshot()

            # ── 2. OCR ────────────────────────────────
            letters = ocr_board(img)
            bad = letters.count("?")
            if bad:
                print(f"  {bad} tile(s) unreadable — retrying in 1s...")
                time.sleep(1)
                continue

            # ── 3. Solve ──────────────────────────────
            t0 = time.perf_counter()
            results = solve_board(letters, words, prefixes)
            elapsed = time.perf_counter() - t0
            print(f"  {len(results)} words found in {elapsed:.3f}s")

            if not results:
                print("  No words — waiting for new board...")
                time.sleep(2)
                continue

            top = list(results.items())[:20]
            preview = ", ".join(w.upper() for w, _ in top[:8])
            print(f"  Top: {preview}{'...' if len(top) > 8 else ''}\n")

            # ── 4. Swipe each word ────────────────────
            for word, path in top:
                print(f"  ▶  {word.upper():<10}  tiles={path}", end="  ")
                ok = swipe_path(path)
                status = "✓" if ok else "✗ FAILED"
                print(status)
                time.sleep(BOARD_WAIT)

            print("\n  Round done — grabbing next board...\n")
            time.sleep(0.3)

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run()