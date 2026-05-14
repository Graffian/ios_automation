"""
calibrate.py  —  detects coordinate scale and finds correct tile positions.
Run with the Boggle game open on your phone.
"""
import requests
import base64
import time
from PIL import Image, ImageDraw
from io import BytesIO

WDA_URL = "http://localhost:8100"
HEADERS = {"Content-Type": "application/json"}

# Current coords (screenshot pixel space — confirmed correct visually)
TILE_COORDS_SCREEN = {
    0:  ( 254, 1151),  1:  ( 511, 1166),  2:  ( 770, 1160),  3:  (1037, 1151),
    4:  ( 235, 1420),  5:  ( 514, 1417),  6:  ( 762, 1401),  7:  (1072, 1429),
    8:  ( 244, 1693),  9:  ( 508, 1686),  10: ( 787, 1686),  11: (1059, 1664),
    12: ( 244, 1956),  13: ( 520, 1950),  14: ( 771, 1940),  15: (1075, 1962),
}


def get_session():
    try:
        r = requests.get(f"{WDA_URL}/status", timeout=8)
        data = r.json()
        sid = data.get("sessionId") or data.get("value", {}).get("sessionId")
        if sid:
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
    return data.get("sessionId") or data.get("value", {}).get("sessionId")


def get_window_size(sid):
    """Get logical screen size from WDA — this is the touch coordinate space."""
    r = requests.get(f"{WDA_URL}/session/{sid}/window/size", timeout=10)
    data = r.json().get("value", {})
    return int(data.get("width", 0)), int(data.get("height", 0))


def take_screenshot(sid):
    r = requests.get(f"{WDA_URL}/session/{sid}/screenshot", timeout=15)
    r.raise_for_status()
    raw = r.json().get("value", "")
    if isinstance(raw, dict):
        raw = raw.get("value", "")
    img = Image.open(BytesIO(base64.b64decode(raw)))
    return img


def tap(sid, x, y, hold_ms=300):
    """Tap with hold at logical coordinates."""
    payload = {
        "actions": [{
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                {"type": "pointerDown"},
                {"type": "pause", "duration": hold_ms},
                {"type": "pointerUp"},
            ],
        }]
    }
    r = requests.post(f"{WDA_URL}/session/{sid}/actions",
                      json=payload, headers=HEADERS, timeout=10)
    return r.status_code == 200


print("\n=== CALIBRATION ===\n")
sid = get_session()
print(f"Session: {sid}")

# ── Step 1: Detect coordinate spaces ─────────────────────────────────────────
img = take_screenshot(sid)
screenshot_w, screenshot_h = img.size
logical_w, logical_h = get_window_size(sid)

print(f"\nScreenshot size (pixels) : {screenshot_w} x {screenshot_h}")
print(f"Logical window size (pts): {logical_w} x {logical_h}")

if logical_w == 0:
    print("WARNING: Could not get window size — trying common scale factors")
    scale = 3.0
else:
    scale = screenshot_w / logical_w
    print(f"Scale factor             : {scale:.2f}x  (screenshot / logical)")

# ── Compute logical touch coordinates ────────────────────────────────────────
TILE_COORDS_LOGICAL = {
    idx: (int(cx / scale), int(cy / scale))
    for idx, (cx, cy) in TILE_COORDS_SCREEN.items()
}

print("\nLogical tap coordinates (use these in boggle_bot.py):")
print("TILE_COORDS = {")
for idx, (x, y) in TILE_COORDS_LOGICAL.items():
    comma = "," if idx < 15 else ""
    sx, sy = TILE_COORDS_SCREEN[idx]
    print(f"    {idx:>2}: ({x:>4}, {y:>4}){comma}   # screen=({sx},{sy}) ÷ {scale:.1f}")
print("}")

# ── Step 2: Draw overlay on screenshot ───────────────────────────────────────
overlay = img.convert("RGBA")
draw = ImageDraw.Draw(overlay)
for idx, (cx, cy) in TILE_COORDS_SCREEN.items():
    r = 45
    draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], outline=(255,40,40,220), width=6)
    draw.ellipse([(cx-8, cy-8), (cx+8, cy+8)], fill=(255,40,40,255))
    draw.text((cx-12, cy-16), str(idx), fill=(255,255,60,255))

overlay.convert("RGB").save("calibration_check.png")
print("\nSaved calibration_check.png")

# ── Step 3: Tap test with LOGICAL coords ─────────────────────────────────────
print("\n=== TAP TEST ===")
print("Watch your phone — each tile should glow/highlight in order 0→15")
input("Press ENTER to start > ")

for idx in range(16):
    lx, ly = TILE_COORDS_LOGICAL[idx]
    sx, sy = TILE_COORDS_SCREEN[idx]
    ok = tap(sid, lx, ly)
    print(f"  Tile {idx:>2}  logical=({lx:>4},{ly:>4})  screen=({sx:>4},{sy:>4})  {'✓' if ok else '✗'}")
    time.sleep(1.2)

print("\nDone.")
print("If tiles lit up correctly → copy the TILE_COORDS block above into boggle_bot.py")
print("If still wrong → share the output and I'll fix it.")