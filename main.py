"""
boggle_bot.py — Screenshot → Claude OCR → Solve → Swipe
=========================================================
Requires:
    pip install requests pillow python-dotenv
    .env file in the same folder:
        ANTHROPIC_API_KEY=sk-ant-...
    words.txt (see load_dictionary for download command)
"""

import base64
import os
import time
from io import BytesIO
from itertools import product as iterproduct

import requests
from dotenv import load_dotenv
from PIL import Image

# Load .env FIRST — before any os.environ reads
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

WDA_URL     = "http://localhost:8100"
WDA_HEADERS = {"Content-Type": "application/json"}

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-sonnet-4-20250514"
# API key is read inside ocr_board() at call time (not at import time)
# so load_dotenv() above has already populated os.environ by then.

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

COORD_SCALE  = 3.0
HOLD_MS      = 350
MOVE_MS      = 60
DWELL_MS     = 100
INTERP_STEPS = 6
BOARD_WAIT   = 2.2
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

    if _session_id:
        try:
            r = requests.get(f"{WDA_URL}/session/{_session_id}", timeout=5)
            if r.status_code == 200:
                return _session_id
        except Exception:
            pass
        print("  [WDA] Session expired — creating a new one...")
        _session_id = None

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

_xs = [cx * COORD_SCALE for cx, _cy in TILE_COORDS.values()]
_ys = [cy * COORD_SCALE for _cx, cy in TILE_COORDS.values()]
BOARD_BOX = (
    int(min(_xs)) - 120,
    int(min(_ys)) - 120,
    int(max(_xs)) + 120,
    int(max(_ys)) + 120,
)


def _board_to_b64(img: Image.Image) -> str:
    crop = img.crop(BOARD_BOX)
    buf = BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def ocr_board(img: Image.Image) -> list[str]:
    """
    Send board crop to Claude Vision.
    Returns list of 16 lowercase letters, or ['?']*16 on failure.
    """
    # Read key HERE at call time — load_dotenv() has already run by now
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found.\n"
            "Make sure your .env file exists in the same folder and contains:\n"
            "  ANTHROPIC_API_KEY=sk-ant-..."
        )

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

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
                            "data": _board_to_b64(img),
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a screenshot of a 4x4 Boggle word-game board. "
                            "Read the 16 letter tiles LEFT TO RIGHT, TOP TO BOTTOM. "
                            "Reply with ONLY the 16 uppercase letters as a single "
                            "string, no spaces, no punctuation. "
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
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"].strip().upper()
        letters_str = "".join(c for c in raw if c.isalpha())[:16]

        if len(letters_str) != 16:
            print(f"  [OCR] Unexpected response: '{raw}' — skipping frame")
            return ["?"] * 16

        print(
            f"  OCR -> {letters_str[:4]} {letters_str[4:8]} "
            f"{letters_str[8:12]} {letters_str[12:]}"
        )
        return list(letters_str.lower())

    except Exception as exc:
        print(f"  [OCR] Claude Vision error: {exc}")
        return ["?"] * 16


# ─────────────────────────────────────────────────────────────────────────────
# DICTIONARY
# ─────────────────────────────────────────────────────────────────────────────

def load_dictionary(path: str = WORDS_PATH) -> tuple[set[str], set[str]]:
    """
    Load word list and build prefix set for DFS pruning.

    Download words.txt:
        curl -o words.txt https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt
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
    """DFS solver. Returns {word: path} sorted longest-first."""
    found: dict[str, list[int]] = {}

    def dfs(tile: int, word: str, path: list[int], visited: set[int]) -> None:
        if word not in prefixes:
            return
        if len(word) >= MIN_WORD_LEN and word in words:
            if word not in found or len(path) > len(found[word]):
                found[word] = list(path)
        if len(word) == MAX_WORD_LEN:
            return
        for nb in ADJACENCY[tile]:
            if nb not in visited:
                visited.add(nb)
                path.append(nb)
                dfs(nb, word + letters[nb], path, visited)
                path.pop()
                visited.remove(nb)

    for start, letter in enumerate(letters):
        if letter != "?":
            dfs(start, letter, [start], {start})

    return dict(sorted(found.items(), key=lambda kv: len(kv[0]), reverse=True))


# ─────────────────────────────────────────────────────────────────────────────
# SWIPE  — W3C Actions API with manual interpolation
# ─────────────────────────────────────────────────────────────────────────────

def swipe_path(indices: list[int]) -> bool:
    sid = get_session()

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
        tx, ty = TILE_COORDS[tile_idx]
        for step in range(1, INTERP_STEPS + 1):
            t = step / INTERP_STEPS
            actions.append({
                "type": "pointerMove",
                "duration": MOVE_MS,
                "x": int(prev_x + (tx - prev_x) * t),
                "y": int(prev_y + (ty - prev_y) * t),
            })
        actions.append({"type": "pause", "duration": DWELL_MS})
        prev_x, prev_y = tx, ty

    actions.append({"type": "pointerUp"})

    payload = {
        "actions": [{
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": actions,
        }]
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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n  ERROR: ANTHROPIC_API_KEY not found in environment or .env file.")
        print("  Make sure .env exists in this folder and contains:")
        print("    ANTHROPIC_API_KEY=sk-ant-...")
        raise SystemExit(1)

    words, prefixes = load_dictionary(WORDS_PATH)

    print()
    print("=" * 56)
    print("   Boggle Bot  --  Screenshot -> OCR -> Solve -> Swipe")
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
            img     = take_screenshot()
            letters = ocr_board(img)

            if "?" in letters:
                print("  Unreadable tiles — retrying...")
                time.sleep(0.8)
                continue

            if letters != last_letters:
                played.clear()
                last_letters = letters[:]
                t0      = time.perf_counter()
                results = solve_board(letters, words, prefixes)
                elapsed = time.perf_counter() - t0
                top     = ", ".join(w.upper() for w in list(results)[:6])
                print(f"  Board changed -> {len(results)} words ({elapsed:.3f}s)  |  Top: {top}")

            remaining = [(w, p) for w, p in results.items() if w not in played]
            if not remaining:
                print("  All words played — waiting for new board...")
                time.sleep(1.5)
                continue

            word, path = remaining[0]
            played.add(word)

            print(f"  >  {word.upper():<12} tiles={path}", end="  ", flush=True)
            print("OK" if swipe_path(path) else "FAILED")

            time.sleep(BOARD_WAIT)

        except KeyboardInterrupt:
            print("\n  Bot stopped.")
            break
        except Exception as exc:
            print(f"  Unexpected error: {exc}")
            time.sleep(2)


if __name__ == "__main__":
    run()
