"""
calibrate.py  —  Run this ONCE before the bot.

It does two things:
  1. Takes a screenshot and draws numbered circles on every TILE_COORDS position
     → saves  calibration_check.png  (open this to see if the dots land on the tiles)

  2. Runs a TAP TEST: taps each tile index 0-15 one at a time, 1 second apart
     → watch your phone — each tile should briefly glow in order

If the dots / glows are in the wrong place, edit the TILE_COORDS in boggle_bot.py
to match your screen.
"""

import requests
import base64
import time
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ── paste your WDA URL and session here ──────────────────────────────────────
WDA_URL   = "http://localhost:8100"
HEADERS   = {"Content-Type": "application/json"}

TILE_COORDS = {
    0:  ( 254, 1151),  1:  ( 511, 1166),  2:  ( 770, 1160),  3:  (1037, 1151),
    4:  ( 235, 1420),  5:  ( 514, 1417),  6:  ( 762, 1401),  7:  (1072, 1429),
    8:  ( 244, 1693),  9:  ( 508, 1686),  10: ( 787, 1686),  11: (1059, 1664),
    12: ( 244, 1956),  13: ( 520, 1950),  14: ( 771, 1940),  15: (1075, 1962),
}
# ─────────────────────────────────────────────────────────────────────────────


def get_session():
    """Get or create a WDA session."""
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=8)
        sid = r.json().get("sessionId") or r.json().get("value", {}).get("sessionId")
        if sid:
            print(f"  Using existing session: {sid}")
            return sid
    except Exception:
        pass
    r = requests.post(
        f"{WDA_URL}/session",
        json={"capabilities": {"alwaysMatch": {}}},
        headers=HEADERS, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
    print(f"  Created new session: {sid}")
    return sid


def take_screenshot(sid):
    r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=15)
    r.raise_for_status()
    raw = r.json().get("value", "")
    if isinstance(raw, dict):
        raw = raw.get("value", "")
    return Image.open(BytesIO(base64.b64decode(raw)))


def tap(sid, x, y):
    """Single tap via W3C actions."""
    payload = {
        "actions": [{
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                {"type": "pointerDown"},
                {"type": "pause", "duration": 120},
                {"type": "pointerUp"},
            ],
        }]
    }
    r = requests.post(f"{WDA_URL}/session/{sid}/actions",
                      json=payload, headers=HEADERS, timeout=10)
    return r.status_code == 200


# ── STEP 1: Overlay dots on screenshot ───────────────────────────────────────
print("\n=== CALIBRATION ===")
sid = get_session()

print("\n[1] Taking screenshot and drawing tile positions...")
img = take_screenshot(sid).convert("RGBA")
overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)

for idx, (cx, cy) in TILE_COORDS.items():
    r = 45
    # Red circle
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                 outline=(255, 40, 40, 230), width=6)
    # Filled dot at centre
    draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)],
                 fill=(255, 40, 40, 255))
    # Index number
    draw.text((cx - 14, cy - 18), str(idx),
              fill=(255, 255, 80, 255))

combined = Image.alpha_composite(img, overlay).convert("RGB")
out_path = "calibration_check.png"
combined.save(out_path)
print(f"   Saved → {out_path}")
print("   ▶  Open this image and check that every red circle sits on a tile.")
print("      If they're off, update TILE_COORDS in boggle_bot.py\n")


# ── STEP 2: Live tap test ─────────────────────────────────────────────────────
print("[2] Tap test — watch your phone, tiles should glow 0→15 in order...")
input("   Press ENTER when you're ready to start the tap test > ")

for idx in range(16):
    cx, cy = TILE_COORDS[idx]
    ok = tap(sid, cx, cy)
    print(f"   Tile {idx:>2}  ({cx:>4}, {cy:>4})  {'✓' if ok else '✗'}")
    time.sleep(1.0)

print("\nDone. If tiles glowed in the wrong order, the coords need adjusting.")
print("Edit TILE_COORDS in boggle_bot.py to fix the positions.\n")
