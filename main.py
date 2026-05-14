"""
boggle_bot.py — Screenshot → Claude OCR → Solve → Swipe
=========================================================
Requires:
    pip install requests pillow python-dotenv
    .env file in the same folder containing:
        ANTHROPIC_API_KEY=sk-ant-...
    words.txt  (see load_dictionary for download command)
"""

import base64
import os
import time
from io import BytesIO
from itertools import product as iterproduct

import requests
from dotenv import load_dotenv
from PIL import Image

# Load variables from .env into os.environ before anything else
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

WDA_URL = "http://localhost:8100"
WDA_HEADERS = {"Content-Type": "application/json"}

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
}
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Tile centres in LOGICAL POINTS (screen coords ÷ COORD_SCALE)
# Layout: 4×4 grid, index 0 = top-left, 15 = bottom-right
TILE_COORDS: dict[int, tuple[int, int]] = {
    0:  ( 84,  383),
    1:  (170,  388),
    2:  (256,  386),
    3:  (345,  383),
    4:  ( 78,  473),
    5:  (171,  472),
    6:  (254,  467),
    7:  (357,  476),
    8:  ( 81,  564),
    9:  (169,  562),
    10: (262,  562),
    11: (353,  554),
    12: ( 81,  652),
    13: (173,  650),
    14: (257,  646),
    15: (358,  654),
}

COORD_SCALE = 3.0   # screenshot pixels per logical point

# Swipe gesture timing (ms)
HOLD_MS      = 350   # initial press-and-hold before dragging
MOVE_MS      = 60    # duration per interpolation micro-step
DWELL_MS     = 100   # pause at each tile centre
INTERP_STEPS = 6     # waypoints injected between every two tiles

BOARD_WAIT   = 2.2   # seconds to wait after each swipe (tile animation)
WORDS_PATH   = "words.txt"
MIN_WORD_LEN = 3
MAX_WORD_LEN = 8


# ─────────────────────────────────────────────────────────────────────────────
# WDA SESSION
# ─────────────────────────────────────────────────────────────────────────────

_session_id: str | None = None


def _create_session() -> str:
    resp = requests.post(
        f"{WDA_URL}/session",
        json={"capabilities": {"alwaysMatch": {}}},
        headers=WDA_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    if not sid:
        raise RuntimeError(f"No sessionId in WDA response: {data}")
    print(f"  [WDA] New session: {sid}")
    return sid


def get_session() -> str:
    global _session_id

    # Validate existing session
    if _session_id:
        try:
            r = requests.get(f"{WDA_URL}/session/{_session_id}", timeout=5)
            if r.status_code == 200:
                return _session_id
        except Exception:
            pass
        print("  [WDA] Session expired — creating a new one...")
        _session_id = None

    # Try to reuse a session already reported by WDA status
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=8)
        sid = (
            r.json().get("sessionId")
            or r.json().get("value", {}).get("sessionId")
        )
        if sid:
            print(f"  [WDA] Reusing existing session: {sid}")
            _session_id = sid
            return _session_id
    except Exception:
        pass

    _session_id = _create_session()
    return _session_id


# ─────────────────────────────────────────────────────────────────────────────
# SCREENSHOT
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot(retries: int = 3) -> Image.Image:
    global _session_id
    for attempt in range(retries):
        try:
            sid = get_session()
            r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=15)
            if r.status_code == 404:
                _session_id = None
                continue
            r.raise_for_status()
            raw = r.json().get("value", "")
            if isinstance(raw, dict):
                raw = raw.get("value", "")
            return Image.open(BytesIO(base64.b64decode(raw)))
        except Exception as exc:
            print(f"  [screenshot] attempt {attempt + 1} failed: {exc}")
            time.sleep(1)
    raise RuntimeError("Screenshot failed after all retries.")


# ─────────────────────────────────────────────────────────────────────────────
# OCR  — Claude Vision
# ─────────────────────────────────────────────────────────────────────────────

# Compute board bounding box from tile coords (screenshot pixels)
_xs = [cx * COORD_SCALE for cx, _cy in TILE_COORDS.values()]
_ys = [cy * COORD_SCALE for _cx, cy in TILE_COORDS.values()]
BOARD_BOX = (
    int(min(_xs)) - 120,
    int(min(_ys)) - 120,
    int(max(_xs)) + 120,
    int(max(_ys)) + 120,
)


def _board_to_b64(img: Image.Image) -> str:
    """Crop the board region and encode as base64 JPEG."""
    crop = img.crop(BOARD_BOX)
    buf = BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def ocr_board(img: Image.Image) -> list[str]:
    """
    Send the board crop to Claude Vision.
    Returns a list of exactly 16 lowercase letters, or ['?'] * 16 on failure.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. "
            "Export it with:  export ANTHROPIC_API_KEY='sk-ant-...'"
        )

    b64 = _board_to_b64(img)

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 50,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a screenshot of a 4×4 Boggle word-game board. "
                            "Read the 16 letter tiles LEFT TO RIGHT, TOP TO BOTTOM. "
                            "Reply with ONLY the 16 uppercase letters as a single "
                            "string — no spaces, no punctuation. "
                            "Example: ABCDEFGHIJKLMNOP"
                        ),
                    },
                ],
            }
        ],
    }

    try:
        r = requests.post(
            ANTHROPIC_API_URL,
            json=payload,
            headers=ANTHROPIC_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"].strip().upper()
        letters_str = "".join(c for c in raw if c.isalpha())[:16]

        if len(letters_str) != 16:
            print(f"  [OCR] Unexpected response: '{raw}' — skipping frame")
            return ["?"] * 16

        letters = list(letters_str.lower())
        print(
            f"  OCR → {letters_str[:4]} {letters_str[4:8]} "
            f"{letters_str[8:12]} {letters_str[12:]}"
        )
        return letters

    except Exception as exc:
        print(f"  [OCR] Claude Vision error: {exc}")
        return ["?"] * 16


# ─────────────────────────────────────────────────────────────────────────────
# DICTIONARY
# ─────────────────────────────────────────────────────────────────────────────

def load_dictionary(path: str = WORDS_PATH) -> tuple[set[str], set[str]]:
    """
    Load a word-per-line dictionary.
    Returns (words, prefixes) where prefixes enables DFS pruning.

    Download with:
        curl -o words.txt \\
          https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt
    """
    try:
        with open(path) as fh:
            words = {
                w.strip().lower()
                for w in fh
                if MIN_WORD_LEN <= len(w.strip()) <= MAX_WORD_LEN
            }
    except FileNotFoundError:
        print(f"  '{path}' not found. Download it with:")
        print(
            "  curl -o words.txt "
            "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
        )
        raise SystemExit(1)

    prefixes: set[str] = set()
    for word in words:
        for i in range(1, len(word) + 1):
            prefixes.add(word[:i])

    print(f"  Dictionary: {len(words):,} words  /  {len(prefixes):,} prefixes")
    return words, prefixes


# ─────────────────────────────────────────────────────────────────────────────
# SOLVER
# ─────────────────────────────────────────────────────────────────────────────

def _build_adjacency() -> dict[int, list[int]]:
    """Pre-compute the 8-directional neighbours for each of the 16 tiles."""
    adj: dict[int, list[int]] = {}
    for idx in range(16):
        row, col = divmod(idx, 4)
        adj[idx] = [
            (row + dr) * 4 + (col + dc)
            for dr, dc in iterproduct((-1, 0, 1), repeat=2)
            if (dr, dc) != (0, 0)
            and 0 <= row + dr < 4
            and 0 <= col + dc < 4
        ]
    return adj


ADJACENCY = _build_adjacency()


def solve_board(
    letters: list[str],
    words: set[str],
    prefixes: set[str],
) -> dict[str, list[int]]:
    """
    DFS over the 4×4 board to find all valid words.
    Returns {word: [tile_indices]} sorted longest-first.
    """
    found: dict[str, list[int]] = {}

    def dfs(tile: int, word: str, path: list[int], visited: set[int]) -> None:
        if word not in prefixes:
            return
        if len(word) >= MIN_WORD_LEN and word in words:
            # Keep the path only if this is the first find (or longer path)
            if word not in found or len(path) > len(found[word]):
                found[word] = list(path)
        if len(word) == MAX_WORD_LEN:
            return
        for neighbour in ADJACENCY[tile]:
            if neighbour not in visited:
                visited.add(neighbour)
                path.append(neighbour)
                dfs(neighbour, word + letters[neighbour], path, visited)
                path.pop()
                visited.remove(neighbour)

    for start, letter in enumerate(letters):
        if letter != "?":
            dfs(start, letter, [start], {start})

    return dict(sorted(found.items(), key=lambda kv: len(kv[0]), reverse=True))


# ─────────────────────────────────────────────────────────────────────────────
# SWIPE  — W3C Actions API with manual interpolation
#
# Design notes:
#   • No 'button' field on pointerDown/Up — touch pointers have no buttons;
#     including button:0 causes some WDA builds to silently drop the action.
#   • Manual waypoint interpolation: WDA does NOT trace intermediate screen
#     positions between start and end coords.  We walk the finger ourselves
#     so the game's hit-boxes register every tile in the path.
#   • DELETE /actions before each swipe to clear any stuck touch state.
# ─────────────────────────────────────────────────────────────────────────────

def swipe_path(indices: list[int]) -> bool:
    """
    Perform a continuous drag across the given tile indices.
    Returns True on HTTP 200, False otherwise.
    """
    sid = get_session()

    # Clear any leftover touch state from a previous gesture
    try:
        requests.delete(f"{WDA_URL}/session/{sid}/actions", timeout=5)
    except Exception:
        pass

    start_x, start_y = TILE_COORDS[indices[0]]
    actions = [
        {"type": "pointerMove", "duration": 0, "x": start_x, "y": start_y},
        {"type": "pointerDown"},
        {"type": "pause", "duration": HOLD_MS},
    ]

    prev_x, prev_y = start_x, start_y
    for tile_idx in indices[1:]:
        target_x, target_y = TILE_COORDS[tile_idx]

        # Inject INTERP_STEPS intermediate moves so every hit-box is crossed
        for step in range(1, INTERP_STEPS + 1):
            t = step / INTERP_STEPS
            actions.append({
                "type": "pointerMove",
                "duration": MOVE_MS,
                "x": int(prev_x + (target_x - prev_x) * t),
                "y": int(prev_y + (target_y - prev_y) * t),
            })

        actions.append({"type": "pause", "duration": DWELL_MS})
        prev_x, prev_y = target_x, target_y

    actions.append({"type": "pointerUp"})

    payload = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": actions,
            }
        ]
    }

    try:
        r = requests.post(
            f"{WDA_URL}/session/{sid}/actions",
            json=payload,
            headers=WDA_HEADERS,
            timeout=20,
        )
        if r.status_code == 200:
            return True
        print(f"  [swipe] HTTP {r.status_code}: {r.text[:300]}")
        return False
    except Exception as exc:
        print(f"  [swipe] exception: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    if not ANTHROPIC_API_KEY:
        print("\n  ERROR: ANTHROPIC_API_KEY is not set.")
        print("  Run:  export ANTHROPIC_API_KEY='sk-ant-...'")
        raise SystemExit(1)

    words, prefixes = load_dictionary(WORDS_PATH)

    print()
    print("=" * 56)
    print("   Boggle Bot  —  Screenshot → OCR → Solve → Swipe")
    print("=" * 56)
    print("Press Ctrl+C to stop.\n")

    try:
        get_session()
    except Exception as exc:
        print(f"  [WDA] Could not connect: {exc}")
        print("  Make sure WebDriverAgent is running on port 8100.\n")

    played: set[str] = set()
    last_letters: list[str] = []
    results: dict[str, list[int]] = {}

    while True:
        try:
            # ── Screenshot + OCR ──────────────────────────────────────────
            img = take_screenshot()
            letters = ocr_board(img)

            if "?" in letters:
                print("  Unreadable tiles — retrying...")
                time.sleep(0.8)
                continue

            # ── Re-solve only when the board changes ──────────────────────
            if letters != last_letters:
                played.clear()
                last_letters = letters[:]
                t0 = time.perf_counter()
                results = solve_board(letters, words, prefixes)
                elapsed = time.perf_counter() - t0
                top_words = ", ".join(w.upper() for w in list(results)[:6])
                print(
                    f"  Board changed → {len(results)} words found "
                    f"({elapsed:.3f}s)  |  Top: {top_words}"
                )

            # ── Pick the best unplayed word ───────────────────────────────
            remaining = [(w, p) for w, p in results.items() if w not in played]
            if not remaining:
                print("  All words played — waiting for new board...")
                time.sleep(1.5)
                continue

            word, path = remaining[0]   # already sorted longest-first
            played.add(word)

            print(f"  ▶  {word.upper():<12} tiles={path}", end="  ", flush=True)
            success = swipe_path(path)
            print("✓" if success else "✗ FAILED")

            time.sleep(BOARD_WAIT)

        except KeyboardInterrupt:
            print("\n  Bot stopped.")
            break
        except Exception as exc:
            print(f"  Unexpected error: {exc}")
            time.sleep(2)


if __name__ == "__main__":
    run()
