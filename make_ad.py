"""Hi Vit multifruit juice — 9:16 floating-product 3D ad, fully procedural.

Everything (bottle model, materials, props, lights, camera moves, text,
post-processing and encoding) is generated from this one file.

Run with Blender:          blender -b -P make_ad.py -- --stage all
or with the bpy module:    python make_ad.py --stage all

Stages (comma separated, run in order):
  prep      clean/trace the product photos, build label/backfill/logo textures, fetch font
  stills    render look-dev stills (one per variant + final lineup with text)
  preview   render the whole animation at preview quality and encode preview.mp4
  final     render the whole animation at final quality and encode hivit_ad.mp4
  all       prep + final
Options:
  --frames A-B     only render this frame range (preview/final)
  --no-encode      render frames only
  --encode-only    skip rendering, only post-process + encode existing frames
"""
import math
import os
import random
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")

# =============================================================================
# CONFIG — every tunable value lives here
# =============================================================================
CONFIG = {
    "fps": 30,
    "duration": 19.0,              # s (headline needs >=1.8 s hold after the stagger)
    "resolution": (1080, 1920),
    "preview_percent": 50,         # 540x960 previews
    "engine": os.environ.get("HIVIT_ENGINE", "CYCLES"),            # "CYCLES" or "BLENDER_EEVEE"
    "quality": {                   # cycles samples (adaptive + OIDN denoise)
        "still":   {"samples": 64, "threshold": 0.03},
        "preview": {"samples": 12, "threshold": 0.08},
        "final":   {"samples": 24, "threshold": 0.04},
    },
    "motion_blur_shutter": 0.5,    # frames
    "bpm": 120,                    # used when assets/music.mp3 is missing
    "snap_cuts_to_beats": True,
    "ease": 0.55,                  # 0 = linear moves, 1 = full ease-in-out (cubic bezier)

    # --- assets ----------------------------------------------------------------
    "assets": "assets",
    "music": "assets/music.mp3",
    "font_url": "https://raw.githubusercontent.com/google/fonts/main/ofl/nunito/Nunito%5Bwght%5D.ttf",
    "font_file": "build/fonts/Nunito[wght].ttf",
    "font_weight": "Black",
    "label_upscale": 2,
    "label_erode_px": 16,          # drop the photo's own silhouette fringe (original bg spill)            # lanczos upscale + unsharp of the label photos

    # --- bottle geometry (Blender units; bottle height = 2.0, origin at centre) -
    "bottle_height": 2.0,
    "profile_source": "orange",    # photo whose alpha defines the lathe profile
    "n_theta_body": 160,
    "n_theta_cap": 360,
    "cap_ribs": 72,
    "cap_rib_depth": 0.018,        # fraction of cap radius
    "grip_panels": {"angles": [50, 130, 230, 310], "width": 34, "depth": 0.035,
                    "z_from_px": 590, "z_to_px": 790},   # photo rows (orange photo)
    "petaloid": {"feet": 5, "valley_lift": 0.07, "pinch": 0.07, "height": 0.16, "push_up": 0.05},
    "droplets": {"count": 420, "r_min": 0.004, "r_max": 0.016, "flatten": 0.5, "seed": 7},

    # --- materials ---------------------------------------------------------------
    "label_emission": 0.82,        # photo self-illumination (keeps label colours true)
    "label_diffuse": 0.22,         # share of the photo colour that reacts to the lights
    "label_roughness": 0.32,
    "coat_roughness": 0.035,
    "coat_grazing": (0.55, 0.95, 0.3),   # facing range over which the coat fades to 30%
    "edge_glow": 0.45,              # backlit juice glow at the silhouette edges
    "front_blend": (0.08, 0.42),   # -normal.y range where photo fades into backfill
    "condensation_bump": 0.22,
    "cap_color": "#FFC400",
    # silhouette shots: label colour x tint (linear), part of the self-glow kept, stronger rim glow
    "silhouette": {"tint": (0.36, 0.17, 0.06), "emission": 0.6, "glow": 1.8},
    "pet_tint": "#FFF6E8",

    # --- palette (top colour, bottom colour) -------------------------------------
    "palettes": {
        "orange":     {"top": "#FF8A1F", "bottom": "#FFC34D", "hot": "#FFE2A6", "hot_amt": 0.55, "glow": "#FF9A1A"},
        "passion":    {"top": "#C2187A", "bottom": "#FFD23F", "hot": "#FFE58A", "hot_amt": 0.35, "glow": "#FFB000"},
        "seabuck":    {"top": "#F26A1B", "bottom": "#FFB347", "hot": "#FFD9A0", "hot_amt": 0.5, "glow": "#FF8A10"},
        "silhouette": {"top": "#FFA449", "bottom": "#FFE6BC", "hot": "#FFFBF2", "hot_amt": 0.95, "glow": "#FFB040"},
        "brand":      {"top": "#E8590C", "bottom": "#FFD23F", "hot": "#FFE07A", "hot_amt": 0.45, "glow": "#FF9A1A"},
        "brand_lift": {"top": "#FF8A1F", "bottom": "#FFE08A", "hot": "#FFF3C8", "hot_amt": 0.6, "glow": "#FF9A1A"},
    },
    "grad_range": (0.12, 0.92),    # window-y range of the vertical gradient
    "hot_radius": 0.55,

    "variants": {
        "orange":  {"image": "hivit_orange.png",       "palette": "orange",  "props": "orange"},
        "passion": {"image": "hivit_passionfruit.png", "palette": "passion", "props": "passion"},
        "seabuck": {"image": "hivit_seabuckthorn.png", "palette": "seabuck", "props": "seabuck"},
    },
    "logo_crop": {"image": "hivit_orange.png", "box": (248, 730, 652, 1115),   # px in source photo
                  "core": (262, 765, 625, 1080)},  # a shape belongs to the logo if its centre is in here
    "logo_stroke": {"px": 7, "alpha": 0.85, "color": "#4A1A00"},

    # --- lights (W) ---------------------------------------------------------------
    "lights": {
        "key":   {"pos": (-4.5, -5.0, 4.0), "size": (5.0, 5.0), "energy": 260, "color": "#FFF4E6"},
        "strip": {"pos": (2.2, -4.5, 0.6), "size": (0.35, 6.0), "energy": 260, "color": "#FFFFFF"},
        "rimL":  {"pos": (-3.0, 3.2, 0.8), "size": (1.1, 7.0), "energy": 900, "color": "#FFF0D8"},
        "rimR":  {"pos": (3.0, 3.2, 0.8), "size": (1.1, 7.0), "energy": 900, "color": "#FFF0D8"},
        "top":   {"pos": (0.0, 0.5, 5.5), "size": (3.0, 3.0), "energy": 180, "color": "#FFFFFF"},
    },
    "light_presets": {
        "hero":       {"key": 1.0, "strip": 1.0, "rimL": 1.0, "rimR": 1.0, "top": 1.0},
        "silhouette": {"key": 0.0, "strip": 0.0, "rimL": 1.8, "rimR": 1.8, "top": 0.35},
        "lineup":     {"key": 1.1, "strip": 0.8, "rimL": 1.2, "rimR": 1.2, "top": 1.0},
    },

    "world_strength": 0.45,
    "floor_z": -1.0,               # lineup / silhouette floor (bottle centre = floor + 1)
    "floor_reflect": 0.55,
    "focus_pull": 0.26,            # focus on the label surface (bottle radius) not the axis
    "camera_drift": (0.035, 0.022, 0.30),   # amplitude xy, amplitude z, frequency Hz
    "bottle_bob": (0.045, 0.55, 3.0),       # amplitude, frequency Hz, wobble degrees

    "props": {"count_back": 8, "count_front": 1, "front_scale": 0.55, "seed": 3},

    # --- shots (seconds). Bottle/camera tracks are lists of keys spread evenly
    #     over the shot and eased; tuples are constant. Rotations in degrees.
    "shots": [
        {"name": "hook_macro", "start": 0.0, "end": 1.5, "palette": "orange", "lights": "hero",
         "props": None, "floor": False,
         "cam": {"pos": [(0.05, -1.75, 0.10), (0.0, -1.18, 0.05)], "target": [(0.0, 0.0, 0.06), (0.0, 0.0, 0.03)],
                 "lens": [85, 90], "fstop": 1.4},
         "bottles": {"orange": {"pos": (0, 0, 0), "rot": [(0, 0, -9), (0, 0, 4)]}}},
        {"name": "silhouette", "start": 1.5, "end": 2.5, "palette": "silhouette", "lights": "silhouette",
         "props": None, "floor": True,
         "cam": {"pos": [(0.25, -6.0, -0.35), (0.0, -5.4, -0.30)], "target": [(0, 0, 0.05), (0, 0, 0.05)],
                 "lens": [45, 45], "fstop": 6.0},
         "bottles": {"orange": {"pos": (0, 0, 0), "rot": [(0, 0, -14), (0, 0, 10)], "sil": 1.0, "grounded": True}}},
        {"name": "tumble_whip", "start": 2.5, "end": 3.5, "palette": "orange", "lights": "hero",
         "props": None, "floor": False, "ease": 0.0,
         "cam": {"pos": [(0.0, -5.0, 0.2), (0.15, -5.2, 0.3)], "target": [(0, 0, 0.3), (0.2, -1.0, 0.4)],
                 "lens": [32, 30], "fstop": 8.0},
         "bottles": {"orange": {"pos": [(1.8, 16.0, 2.6), (0.6, 4.0, 1.0), (0.1, -2.6, 0.3)],
                                "rot": [(80, 0, 0), (290, 140, 200), (560, 250, 360)], "float": False}}},
        {"name": "orange_hero", "start": 3.5, "end": 5.0, "palette": "orange", "lights": "hero",
         "props": "orange", "floor": False,
         "cam": {"pos": [(0.35, -4.4, 0.35), (-0.25, -3.9, 0.15)], "target": [(0, 0, 0.05), (0, 0, 0.0)],
                 "lens": [50, 52], "fstop": 2.2},
         "bottles": {"orange": {"pos": (0, 0, 0), "rot": [(0, 0, -32), (0, 0, 22)], "whip": 360}}},
        {"name": "orange_lowangle", "start": 5.0, "end": 6.0, "palette": "orange", "lights": "hero",
         "props": "orange", "floor": False,
         "cam": {"pos": [(-1.1, -2.4, -2.5), (0.9, -2.0, -2.2)], "target": [(0, 0, -0.35), (0, 0, -0.2)],
                 "lens": [30, 30], "fstop": 3.0},
         "bottles": {"orange": {"pos": (0, 0, 0), "rot": [(-8, 0, 20), (-4, 0, -15)]}}},
        {"name": "orange_cap", "start": 6.0, "end": 7.0, "palette": "orange", "lights": "hero",
         "props": "orange", "floor": False, "front_props": False,
         "cam": {"pos": [(-0.35, -1.45, 1.25), (0.25, -1.30, 1.18)], "target": [(0, 0, 0.86), (0, 0, 0.88)],
                 "lens": [85, 85], "fstop": 1.2},
         "bottles": {"orange": {"pos": (0, 0, 0), "rot": [(0, 0, 25), (0, 0, -20)]}}},
        {"name": "passion_wide", "start": 7.0, "end": 8.5, "palette": "passion", "lights": "hero",
         "props": "passion", "floor": False,
         "cam": {"pos": [(0.0, -7.6, 0.4), (0.0, -5.6, 0.25)], "target": [(0, 0, 0.0), (0, 0, 0.0)],
                 "lens": [40, 45], "fstop": 2.8},
         "bottles": {"passion": {"pos": (0, 0, 0), "rot": [(0, 20, 38), (0, 20, -12)], "whip": -360}}},
        {"name": "passion_close", "start": 8.5, "end": 10.5, "palette": "passion", "lights": "hero",
         "props": "passion", "floor": False, "front_props": False,
         "cam": {"pos": [(0.4, -3.0, 0.35), (-0.15, -2.7, 0.15)], "target": [(0, 0, 0.12), (0, 0, 0.05)],
                 "lens": [60, 62], "fstop": 1.6},
         "bottles": {"passion": {"pos": (0, 0, 0), "rot": [(0, 20, -8), (0, 20, -40)]}}},
        {"name": "seabuck_orbit", "start": 10.5, "end": 12.5, "palette": "seabuck", "lights": "hero",
         "props": "seabuck", "floor": False, "front_props": False,
         "cam": {"orbit": {"angle": [-42, 38], "dist": [2.7, 2.6], "height": [0.45, 0.2]},
                 "target": [(0, 0, 0.15), (0, 0, 0.05)], "lens": [60, 60], "fstop": 1.6},
         "bottles": {"seabuck": {"pos": (0, 0, 0), "rot": [(0, 0, -20), (0, 0, 18)], "whip": 360}}},
        {"name": "seabuck_pullback", "start": 12.5, "end": 14.0, "palette": "seabuck", "lights": "hero",
         "props": "seabuck", "floor": False,
         "cam": {"pos": [(0.2, -3.0, 0.2), (-0.1, -5.2, 0.35)], "target": [(0, 0, 0.05), (0, 0, 0.1)],
                 "lens": [50, 48], "fstop": 2.4},
         "bottles": {"seabuck": {"pos": (0, 0, 0), "rot": [(0, 0, 12), (0, 0, -16)]}}},
        {"name": "logo_flyin", "start": 14.0, "end": 15.5, "palette": "silhouette", "lights": "silhouette",
         "props": None, "floor": True,
         "cam": {"pos": [(0.0, -6.2, -0.25), (0.0, -5.8, -0.2)], "target": [(0, 0, 0.1), (0, 0, 0.1)],
                 "lens": [45, 46], "fstop": 6.0},
         "bottles": {"orange": {"pos": (0, 0, 0), "rot": [(0, 0, 12), (0, 0, -8)], "sil": 1.0, "grounded": True}},
         "logo": {"pos": [(2.5, -14.0, 4.0), (0.3, -2.8, 0.6), (0.0, -1.55, 0.18), (0.0, -1.5, 0.2)],
                  "rot": [(70, 0, -160), (95, 0, -20), (90, 0, 0), (90, 0, 0)],
                  "width": 1.25, "fade": [0.0, 1.0, 1.0, 1.0], "ease": 0.35}},
        {"name": "final_lineup", "start": 15.5, "end": 19.0, "palette": "brand", "palette_from": "brand_lift",
         "palette_blend": 0.7, "lights": "lineup", "props": None, "floor": True,
         "cam": {"pos": [(0.3, -6.5, 0.6), (-0.1, -5.95, 0.55)], "target": [(0, 0, 0.8), (0, 0, 0.78)],
                 "lens": [48, 49], "fstop": 8.0},
         "bottles": {"orange":  {"pos": (-0.82, 0.18, 0), "rot": [(0, 0, 14), (0, 0, 6)], "grounded": True},
                     "passion": {"pos": (0.0, -0.32, 0), "rot": [(0, 0, -6), (0, 0, 5)], "grounded": True},
                     "seabuck": {"pos": (0.82, 0.18, 0), "rot": [(0, 0, -14), (0, 0, -7)], "grounded": True}}},
    ],

    # --- 2D overlays (px in 1080x1920 space) ------------------------------------
    "safe_area": {"top": 0.12, "bottom": 0.20},
    "still_times": {"1_orange": 4.6, "2_passion": 9.6, "3_seabuck": 11.9, "4_lineup": 18.6},
    "teaser": {"text": "Шинэ савлагаа", "start": 0.25, "end": 1.35, "size": 74, "y": 1380, "fade": 0.25},
    "headline": {"lines": ["Савлагаа", "шинэчлэгдлээ"], "start": 16.0, "stagger": 0.04, "pop": 0.32,
                 "size": 138, "y": 250, "line_gap": -22, "color": "#FFFFFF", "max_width": 980,
                 "shadow": {"offset": (0, 10), "blur": 18, "alpha": 0.45, "color": "#7A2400"}},
    "headline_logo": {"start": 17.0, "fade": 0.4, "width": 175, "y": 640},
    "post": {"bloom": 0.22, "bloom_threshold": 0.78, "bloom_radius": 0.018, "vignette": 0.12,
             "brightness": 1.05, "contrast": 1.08, "saturation": 1.15,
             "sharpen": {"radius": 1.6, "percent": 85, "threshold": 2}},   # radius in 1080p px
    "encode": {"crf": 18, "preset": "slow", "audio_bitrate": "192k", "audio_fade": 1.0},
}

# =============================================================================
# helpers
# =============================================================================


def P(rel):
    return rel if os.path.isabs(rel) else os.path.join(HERE, rel)


def hex2rgb(h, linear=True):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    if linear:
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return tuple(c)


def lerp(a, b, t):
    if isinstance(a, (tuple, list)):
        return tuple(x + (y - x) * t for x, y in zip(a, b))
    return a + (b - a) * t


def ease_io(u, k):
    """Blend between linear and cubic ease-in-out (bezier-like) by k."""
    u = min(max(u, 0.0), 1.0)
    s = u * u * (3 - 2 * u)
    return u + (s - u) * k


def track(spec, u, k):
    """Evaluate a key list evenly spread over u in [0,1] (eased); constants pass through."""
    if not isinstance(spec, list):
        return spec
    if len(spec) == 1:
        return spec[0]
    e = ease_io(u, k) if 0.0 <= u <= 1.0 else u   # extrapolate linearly past the shot end
    x = e * (len(spec) - 1)
    i = int(min(max(math.floor(x), 0), len(spec) - 2))
    return lerp(spec[i], spec[i + 1], x - i)


def smoothstep(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def log(*a):
    print("[make_ad]", *a, flush=True)


# =============================================================================
# PREP — photo tracing and texture generation (pure numpy/Pillow)
# =============================================================================


def largest_mask(alpha):
    from scipy import ndimage
    m = alpha > 128
    lab, n = ndimage.label(m)
    if n > 1:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        m = lab == (1 + int(np.argmax(sizes)))
    return ndimage.binary_fill_holes(m)


def analyse_photo(path):
    """Silhouette rows, axis, cap bottom, fill line of a front product photo."""
    from scipy import ndimage
    im = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    m = largest_mask(im[..., 3])
    rows = np.nonzero(m.any(1))[0]
    y_top, y_bot = int(rows[0]), int(rows[-1])
    L = np.full(im.shape[0], np.nan)
    R = np.full(im.shape[0], np.nan)
    for y in rows:
        xs = np.nonzero(m[y])[0]
        L[y], R[y] = xs[0], xs[-1] + 1
    hw = (R - L) / 2.0
    cx = float(np.nanmedian((L + R)[y_top + 50:y_bot - 50] / 2.0))
    rgb = im[..., :3]

    def centre_med(y):
        w = max(4, int(hw[y] * 0.35))
        return np.median(rgb[y, int(cx) - w:int(cx) + w], axis=0)

    cap_bot = y_top + 60
    for y in range(y_top + 60, y_top + int(0.2 * (y_bot - y_top))):
        r, g, b = centre_med(y)
        if b > 55:  # the yellow cap has ~0 blue; clear PET below it does not
            cap_bot = y
            break
    def juice(y):
        r, g, b = centre_med(y)
        return r > 200 and b < 45 and g < 200

    fill = cap_bot + 80
    for y in range(cap_bot + 60, y_top + int(0.35 * (y_bot - y_top))):
        if all(juice(y + k) for k in range(0, 14, 2)):   # sustained juice, not a reflection streak
            fill = y
            break
    hw_s = ndimage.gaussian_filter1d(ndimage.median_filter(np.nan_to_num(hw), 7), 1.5)
    return {"y_top": y_top, "y_bot": y_bot, "cx": cx, "hw": hw_s, "cap_bot": cap_bot,
            "fill": fill, "w": im.shape[1], "h": im.shape[0], "mask": m}


def make_backfill(path, info, out):
    """Per-row median colour of the label -> vertical strip used on the bottle back."""
    from scipy import ndimage
    im = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    h = im.shape[0]
    strip = np.zeros((h, 3), np.float32)
    cx = info["cx"]
    for y in range(h):
        w = info["hw"][y]
        if w < 5:
            continue
        a, b = int(cx - 0.55 * w), int(cx + 0.55 * w)
        px = im[y, a:b]
        px = px[px[:, 3] > 200][:, :3]
        if len(px) == 0:
            continue
        lum = px.mean(1)
        keep = px[(lum > 70) & (lum < 245)]
        strip[y] = np.median(keep if len(keep) > 8 else px, axis=0)
    valid = strip.sum(1) > 0
    idx = np.arange(h)
    for c in range(3):
        strip[:, c] = np.interp(idx, idx[valid], strip[valid, c])
    strip = ndimage.gaussian_filter1d(strip, 14, axis=0)
    img = np.repeat(strip[:, None, :], 8, axis=1).clip(0, 255).astype(np.uint8)
    Image.fromarray(img, "RGB").save(out)


def make_logo(cfg):
    """Cut the Hi Vit wordmark out of the label photo (black + white knock-out versions)."""
    from scipy import ndimage
    src = Image.open(P(os.path.join(cfg["assets"], cfg["logo_crop"]["image"]))).convert("RGB")
    bx = cfg["logo_crop"]["box"]
    crop = np.asarray(src.crop(bx)).astype(np.float32)
    pad = 2 * cfg["logo_stroke"]["px"]
    crop = np.pad(crop, ((pad, pad), (pad, pad), (0, 0)), mode="edge")
    r, g, b = crop[..., 0], crop[..., 1], crop[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    a_dark = np.clip((150 - lum) / 70.0, 0, 1)
    redness = np.clip((r - g - 90) / 60.0, 0, 1) * (b < 120)
    a = np.maximum(a_dark, redness)
    m = a > 0.5
    lab, n = ndimage.label(ndimage.binary_dilation(m, iterations=2))
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    keep = np.zeros_like(m)
    cx0, cy0, cx1, cy1 = cfg["logo_crop"]["core"]
    cents = ndimage.center_of_mass(m, lab, range(1, n + 1))
    for i, (s, (cy, cx)) in enumerate(zip(sizes, cents)):
        sx, sy = cx - pad + bx[0], cy - pad + bx[1]          # centre in source-photo pixels
        if s > 0.003 * m.size and cx0 <= sx <= cx1 and cy0 <= sy <= cy1:
            keep |= lab == i + 1
    a = a * ndimage.binary_dilation(keep, iterations=2)
    # knock-out version: dark letters -> white, keep the red smile
    red_w = np.clip(redness * 1.4, 0, 1)
    white = np.stack([255 * (1 - red_w) + r * red_w, 255 * (1 - red_w) + g * red_w,
                      255 * (1 - red_w) + b * red_w], -1)
    black = np.stack([r, g, b], -1)
    black = black * (1 - a_dark[..., None]) + np.array([12, 8, 6]) * a_dark[..., None]
    # soft dark stroke keeps the white knock-out readable over light backgrounds
    st = cfg["logo_stroke"]
    stroke = ndimage.gaussian_filter(ndimage.binary_dilation(a > 0.4, iterations=st["px"]).astype(np.float32),
                                     st["px"] / 3) * st["alpha"]
    sc = np.array(hex2rgb(st["color"], False)) * 255
    a_w = a + stroke * (1 - a)
    white = (white * a[..., None] + sc * (stroke * (1 - a))[..., None]) / np.maximum(a_w, 1e-4)[..., None]
    for name, rgb, al in (("logo_white.png", white, a_w), ("logo_black.png", black, a)):
        out = np.dstack([rgb, al * 255]).clip(0, 255).astype(np.uint8)
        im = Image.fromarray(out, "RGBA")
        bb = im.getbbox()
        im.crop(bb).save(os.path.join(BUILD, name))


def ensure_font(cfg):
    path = P(cfg["font_file"])
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subprocess.run(["curl", "-sSfL", "-o", path, cfg["font_url"]], check=True)
    return path


def verify_font(cfg):
    from fontTools.ttLib import TTFont
    cmap = TTFont(P(cfg["font_file"])).getBestCmap()
    text = "".join(cfg["headline"]["lines"]) + cfg["teaser"]["text"]
    missing = sorted({c for c in text if not c.isspace() and ord(c) not in cmap})
    if missing:
        raise SystemExit(f"font is missing glyphs: {missing}")
    log("font covers all glyphs:", "".join(sorted(set(text))))


def stage_prep(cfg):
    os.makedirs(BUILD, exist_ok=True)
    ensure_font(cfg)
    verify_font(cfg)
    infos = {}
    for key, v in cfg["variants"].items():
        src = P(os.path.join(cfg["assets"], v["image"]))
        info = analyse_photo(src)
        infos[key] = info
        # clean copy: alpha from the largest connected silhouette, colour un-touched
        im = Image.open(src).convert("RGBA")
        arr = np.asarray(im).copy()
        arr[..., 3] = np.where(info["mask"], arr[..., 3], 0)
        clean = Image.fromarray(arr, "RGBA")
        clean.save(os.path.join(BUILD, f"clean_{key}.png"))
        k = cfg["label_upscale"]
        from scipy import ndimage
        er = ndimage.binary_erosion(info["mask"], iterations=cfg["label_erode_px"])
        soft = ndimage.gaussian_filter(er.astype(np.float32), cfg["label_erode_px"] / 3)
        arr[..., 3] = (np.minimum(arr[..., 3] / 255.0, soft) * 255).astype(np.uint8)
        clean = Image.fromarray(arr, "RGBA")
        up = clean.resize((clean.width * k, clean.height * k), Image.LANCZOS)
        rgb = up.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
        up = Image.merge("RGBA", (*rgb.split(), up.split()[3]))
        up.save(os.path.join(BUILD, f"label_{key}.png"))
        make_backfill(os.path.join(BUILD, f"clean_{key}.png"), info, os.path.join(BUILD, f"backfill_{key}.png"))
        log(f"{key}: rows {info['y_top']}-{info['y_bot']} axis {info['cx']:.1f} "
            f"cap_bottom {info['cap_bot']} fill {info['fill']}")
    np.savez(os.path.join(BUILD, "photo_info.npz"),
             **{f"{k}_{f}": np.asarray(v[f]) for k, v in infos.items()
                for f in ("y_top", "y_bot", "cx", "hw", "cap_bot", "fill", "w", "h")})
    make_logo(cfg)
    log("prep done")


def load_infos(cfg):
    path = os.path.join(BUILD, "photo_info.npz")
    if not os.path.exists(path):
        stage_prep(cfg)
    d = np.load(path)
    out = {}
    for k in cfg["variants"]:
        out[k] = {f: d[f"{k}_{f}"] for f in ("y_top", "y_bot", "cx", "hw", "cap_bot", "fill", "w", "h")}
        for f in ("y_top", "y_bot", "cap_bot", "fill", "w", "h"):
            out[k][f] = int(out[k][f])
        out[k]["cx"] = float(out[k]["cx"])
    return out


# =============================================================================
# BEATS — snap shot boundaries to the music
# =============================================================================


def beat_times(cfg):
    music = P(cfg["music"])
    if os.path.exists(music):
        try:
            import librosa
            y, sr = librosa.load(music, sr=None, mono=True, duration=cfg["duration"] + 2)
            tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
            bt = librosa.frames_to_time(beats, sr=sr)
            log(f"music tempo {float(np.atleast_1d(tempo)[0]):.1f} BPM, {len(bt)} beats")
            return list(bt)
        except Exception as e:  # noqa: BLE001
            log("beat detection failed, using BPM grid:", e)
    step = 60.0 / cfg["bpm"]
    return [i * step for i in range(int(cfg["duration"] / step) + 2)]


def snap_shots(cfg):
    if not cfg["snap_cuts_to_beats"]:
        return
    beats = beat_times(cfg)
    shots = cfg["shots"]
    for a, b in zip(shots, shots[1:]):
        t = a["end"]
        nb = min(beats, key=lambda x: abs(x - t))
        if abs(nb - t) <= 0.26 and nb - a["start"] > 0.4 and b["end"] - nb > 0.4:
            a["end"] = b["start"] = round(nb * cfg["fps"]) / cfg["fps"]


# =============================================================================
# SCENE — Blender part
# =============================================================================

bpy = None


def import_bpy():
    global bpy
    import bpy as _bpy
    bpy = _bpy


def sock_id(coll, ident):
    return next(s for s in coll if s.identifier == ident)


class NB:
    """Tiny node-building helper."""

    def __init__(self, tree):
        self.t = tree
        self.n = tree.nodes
        self.l = tree.links

    def node(self, kind, **props):
        nd = self.n.new(kind)
        for k, v in props.items():
            if k == "inputs":
                for name, val in v.items():
                    nd.inputs[name].default_value = val
            else:
                setattr(nd, k, v)
        return nd

    def link(self, a, b):
        self.l.new(a, b)

    def math(self, op, a, b=None, c=None, clamp=False):
        nd = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                nd.inputs[i].default_value = v
            else:
                self.link(v, nd.inputs[i])
        return nd.outputs[0]

    def mix(self, fac, a, b, dtype="RGBA"):
        nd = self.node("ShaderNodeMix", data_type=dtype)
        sfx = {"RGBA": "Color", "FLOAT": "Float", "VECTOR": "Vector"}[dtype]
        for sock, v in (("Factor_Float", fac), (f"A_{sfx}", a), (f"B_{sfx}", b)):
            s = sock_id(nd.inputs, sock)
            if hasattr(v, "is_output") or hasattr(v, "links"):
                self.link(v, s)
            else:
                s.default_value = v if not (dtype == "RGBA" and len(v) == 3) else (*v, 1.0)
        return sock_id(nd.outputs, f"Result_{sfx}")

    def maprange(self, v, a, b, c=0.0, d=1.0, smooth=True):
        nd = self.node("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP" if smooth else "LINEAR")
        self.link(v, nd.inputs["Value"])
        nd.inputs["From Min"].default_value = a
        nd.inputs["From Max"].default_value = b
        nd.inputs["To Min"].default_value = c
        nd.inputs["To Max"].default_value = d
        return nd.outputs[0]

    def value(self, name, v):
        nd = self.node("ShaderNodeValue", name=name, label=name)
        nd.outputs[0].default_value = v
        return nd

    def rgb(self, name, c):
        nd = self.node("ShaderNodeRGB", name=name, label=name)
        nd.outputs[0].default_value = (*c, 1.0)
        return nd


def new_material(name):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:  # noqa: BLE001
        pass
    m.node_tree.nodes.clear()
    return m, NB(m.node_tree)


def principled(nb, **inputs):
    bs = nb.node("ShaderNodeBsdfPrincipled")
    for k, v in inputs.items():
        if hasattr(v, "is_output"):
            nb.link(v, bs.inputs[k])
        else:
            bs.inputs[k].default_value = v
    return bs


def output(nb, shader):
    out = nb.node("ShaderNodeOutputMaterial")
    nb.link(shader, out.inputs["Surface"])
    return out


def mesh_object(name, verts, faces, smooth=True, coll=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.update()
    if smooth:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    ob = bpy.data.objects.new(name, me)
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def lathe(rings, n_theta, close_top=None, close_bottom=None):
    """rings: list of arrays (n_theta,3) top->bottom. close_*: centre point or None."""
    verts = [v for ring in rings for v in ring]
    faces = []
    nr = len(rings)
    for i in range(nr - 1):
        for j in range(n_theta):
            a = i * n_theta + j
            b = i * n_theta + (j + 1) % n_theta
            faces.append((a, b, b + n_theta, a + n_theta))
    if close_top is not None:
        c = len(verts)
        verts.append(close_top)
        for j in range(n_theta):
            faces.append((c, (j + 1) % n_theta, j))
    if close_bottom is not None:
        c = len(verts)
        verts.append(close_bottom)
        base = (nr - 1) * n_theta
        for j in range(n_theta):
            faces.append((base + j, base + (j + 1) % n_theta, c))
    return verts, faces


class Geo:
    """Bottle dimensions derived from the profile photo."""

    def __init__(self, cfg, info):
        self.H = cfg["bottle_height"]
        self.info = info
        self.s = self.H / (info["y_bot"] - info["y_top"])

    def z(self, y):
        return (self.info["y_bot"] - y) * self.s - self.H / 2

    def r(self, y):
        return float(self.info["hw"][int(round(min(max(y, self.info["y_top"]), self.info["y_bot"])))]) * self.s


def build_bottle_meshes(cfg, geo):
    info = geo.info
    nt = cfg["n_theta_body"]
    th = np.linspace(0, 2 * np.pi, nt, endpoint=False)
    sin, cos = np.sin(th), -np.cos(th)          # theta=0 faces the camera (-Y)
    gp = cfg["grip_panels"]
    pet = cfg["petaloid"]

    def angdist(a, b):
        d = (a - b + 180) % 360 - 180
        return abs(d)

    panel = np.zeros(nt)
    thd = np.degrees(th)
    for ang in gp["angles"]:
        d = np.array([angdist(t, ang) for t in thd])
        panel = np.maximum(panel, 1 - np.clip((d - gp["width"] / 2 + 6) / 6.0, 0, 1))
    valley = 0.5 - 0.5 * np.cos(pet["feet"] * th)

    # ---------------- body (cap bottom -> base)
    y0 = info["cap_bot"] - 10
    y1 = info["y_bot"] - 2
    ys = np.concatenate([np.arange(y0, y1, 3.0), [y1]])
    z_bot = geo.z(y1)
    rings = []
    for y in ys:
        z = geo.z(y)
        r = geo.r(y)
        pz = smoothstep(gp["z_from_px"] - 30, gp["z_from_px"] + 10, y) * (1 - smoothstep(gp["z_to_px"] - 10, gp["z_to_px"] + 30, y))
        rr = r * (1 - gp["depth"] * panel * pz)
        w = max(0.0, (pet["height"] - (z - z_bot)) / pet["height"]) ** 2
        rr = rr * (1 - pet["pinch"] * w * valley)
        rings.append(np.stack([rr * sin, rr * cos, np.full(nt, z)], -1))
    # petaloid underside
    rb = rings[-1][:, :2].copy()
    n_under = 14
    for k in range(1, n_under):
        f = 1 - k / n_under
        sx = math.sin(math.pi * f)
        zz = z_bot + valley * pet["valley_lift"] * sx * 1.0 + pet["push_up"] * (1 - f) ** 2
        rings.append(np.stack([rb[:, 0] * f, rb[:, 1] * f, zz], -1))
    verts, faces = lathe(rings, nt, close_bottom=(0, 0, z_bot + pet["push_up"]))
    body = (verts, faces)

    # ---------------- cap (ribbed)
    nc = cfg["n_theta_cap"]
    thc = np.linspace(0, 2 * np.pi, nc, endpoint=False)
    rib = np.cos(cfg["cap_ribs"] * thc)
    rib = np.clip(rib * 2.5, -1, 1) * 0.5 + 0.5        # flattened (square-ish) ribs
    yt, yb = info["y_top"], info["cap_bot"] + 2
    ys = np.linspace(yt + 1, yb, 70)
    crings = []
    r_top = geo.r(yt + 14)
    # rounded top: fan of rings from the centre to the edge
    for k in range(6):
        f = (k + 1) / 7
        crings.append(None if False else np.stack([r_top * f * np.sin(thc), -r_top * f * np.cos(thc),
                                                   np.full(nc, geo.z(yt) - 0.002 * f * f)], -1))
    for y in ys:
        r = geo.r(y) * 0.985
        band = smoothstep(yt + 22, yt + 34, y) * (1 - smoothstep(yb - 26, yb - 14, y))
        rr = r * (1 - cfg["cap_rib_depth"] * band * (1 - rib))
        crings.append(np.stack([rr * np.sin(thc), -rr * np.cos(thc), np.full(nc, geo.z(y))], -1))
    r_in = geo.r(yb) * 0.8
    crings.append(np.stack([r_in * np.sin(thc), -r_in * np.cos(thc), np.full(nc, geo.z(yb))], -1))
    cap = lathe(crings, nc, close_top=(0, 0, geo.z(yt)), close_bottom=(0, 0, geo.z(yb)))

    # ---------------- juice surface disk at the fill line
    zf = geo.z(info["fill"] + 3)
    rf = geo.r(info["fill"] + 3) * 0.96
    disk = lathe([np.stack([rf * sin, rf * cos, np.full(nt, zf)], -1)], nt, close_top=(0, 0, zf))

    # ---------------- condensation droplets (flattened half spheres on the body)
    d = cfg["droplets"]
    rng = random.Random(d["seed"])
    ico_v, ico_f = icosphere(1)
    dv, df = [], []
    yz0, yz1 = info["fill"] + 20, info["y_bot"] - 40
    for i in range(d["count"]):
        y = rng.uniform(yz0, yz1)
        t = rng.uniform(0, 2 * math.pi)
        rad = d["r_min"] + (d["r_max"] - d["r_min"]) * rng.random() ** 2.5
        r = geo.r(y)
        n = np.array([math.sin(t), -math.cos(t), 0.0])
        # account for the slope of the profile in the normal
        dr = (geo.r(y - 4) - geo.r(y + 4)) / (8 * geo.s)
        n = n + np.array([0, 0, dr])
        n /= np.linalg.norm(n)
        c = np.array([r * math.sin(t), -r * math.cos(t), geo.z(y)]) - n * rad * 0.1
        # local frame
        up = np.array([0, 0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0, 0])
        tx = np.cross(up, n)
        tx /= np.linalg.norm(tx)
        ty = np.cross(n, tx)
        stretch = 1.0 + 0.4 * rng.random()        # slightly elongated downwards
        base = len(dv)
        for v in ico_v:
            p = c + rad * (v[0] * tx + v[1] * ty * stretch + v[2] * n * d["flatten"])
            dv.append(p)
        df += [tuple(base + i for i in f) for f in ico_f]
    drops = (dv, df)
    return body, cap, disk, drops


def icosphere(subdiv):
    t = (1 + 5 ** 0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
         (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    v = [np.array(x, float) / np.linalg.norm(x) for x in v]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
         (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
         (8, 6, 7), (9, 8, 1)]
    for _ in range(subdiv):
        cache = {}
        nf = []

        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = v[a] + v[b]
                v.append(m / np.linalg.norm(m))
                cache[k] = len(v) - 1
            return cache[k]
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return v, f


# ----------------------------------------------------------------- materials


def mat_label(cfg, key, info, geo):
    """Photo projected from the front + backfill strip, under a clear PET coat."""
    m, nb = new_material(f"Label_{key}")
    pal = cfg["palettes"][cfg["variants"][key]["palette"]]
    tc = nb.node("ShaderNodeTexCoord")
    sep = nb.node("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Object"], sep.inputs[0])
    # projection constants for this photo (fit by height, same profile scale)
    s_v = geo.H / (info["y_bot"] - info["y_top"])
    W, Hh = info["w"], info["h"]
    u = nb.math("MULTIPLY_ADD", sep.outputs["X"], 1.0 / (s_v * W))
    u.node.inputs[2].default_value = info["cx"] / W
    v = nb.math("MULTIPLY_ADD", sep.outputs["Z"], 1.0 / (s_v * Hh))
    v.node.inputs[2].default_value = (Hh - info["y_bot"]) / Hh + geo.H / (2 * s_v * Hh)
    uv = nb.node("ShaderNodeCombineXYZ")
    nb.link(u, uv.inputs[0])
    nb.link(v, uv.inputs[1])
    img = nb.node("ShaderNodeTexImage", interpolation="Cubic", extension="CLIP")
    img.image = bpy.data.images.load(os.path.join(BUILD, f"label_{key}.png"), check_existing=True)
    img.image.alpha_mode = "STRAIGHT"
    nb.link(uv.outputs[0], img.inputs[0])
    uvb = nb.node("ShaderNodeCombineXYZ", inputs={"X": 0.5})
    nb.link(v, uvb.inputs[1])
    back = nb.node("ShaderNodeTexImage", interpolation="Linear", extension="EXTEND")
    back.image = bpy.data.images.load(os.path.join(BUILD, f"backfill_{key}.png"), check_existing=True)
    nb.link(uvb.outputs[0], back.inputs[0])
    # front weight from the object-space normal
    nsep = nb.node("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Normal"], nsep.inputs[0])
    front = nb.math("MULTIPLY", nsep.outputs["Y"], -1.0)
    a, b = cfg["front_blend"]
    wf = nb.maprange(front, a, b)
    w = nb.math("MULTIPLY", wf, img.outputs["Alpha"])
    base = nb.mix(w, back.outputs["Color"], img.outputs["Color"])
    sil = nb.value("SIL", 0.0).outputs[0]
    dark = sil_tint(nb, cfg, sil, base)
    # condensation micro bump on the coat
    vor = nb.node("ShaderNodeTexVoronoi", feature="F1", inputs={"Scale": 160.0, "Randomness": 1.0})
    nb.link(tc.outputs["Object"], vor.inputs["Vector"])
    hgt = nb.maprange(vor.outputs["Distance"], 0.0, 0.42, 1.0, 0.0)
    bump = nb.node("ShaderNodeBump", inputs={"Strength": cfg["condensation_bump"], "Distance": 0.002})
    nb.link(hgt, bump.inputs["Height"])
    emis = nb.math("MULTIPLY", nb.math("MULTIPLY_ADD", sil, cfg["silhouette"]["emission"] - 1.0, 1.0),
                   cfg["label_emission"])
    # taper the coat at grazing angles: soft glowing rims instead of a clipped white outline
    lwc = nb.node("ShaderNodeLayerWeight", inputs={"Blend": 0.5})
    a0, a1, keep = cfg["coat_grazing"]
    coat_w = nb.maprange(lwc.outputs["Facing"], a0, a1, 1.0, keep)
    diffuse = nb.node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    sock_id(diffuse.inputs, "Factor_Float").default_value = 1.0
    nb.link(dark, sock_id(diffuse.inputs, "A_Color"))
    sock_id(diffuse.inputs, "B_Color").default_value = (*[cfg["label_diffuse"]] * 3, 1.0)
    bs = principled(nb, **{"Base Color": sock_id(diffuse.outputs, "Result_Color"), "Roughness": cfg["label_roughness"],
                           "Specular IOR Level": 0.35, "Coat Weight": coat_w,
                           "Coat Roughness": cfg["coat_roughness"], "Coat IOR": 1.52,
                           "Coat Normal": bump.outputs["Normal"],
                           "Emission Color": dark, "Emission Strength": emis})
    # juice glow at grazing angles (backlit liquid look)
    lw = nb.node("ShaderNodeLayerWeight", inputs={"Blend": 0.35})
    edge = nb.math("POWER", lw.outputs["Facing"], 2.2)
    glow_amt = nb.math("MULTIPLY", edge, nb.math("MULTIPLY_ADD", sil, (cfg["silhouette"]["glow"] - 1.0) * cfg["edge_glow"],
                                                 cfg["edge_glow"]))
    gcol = nb.rgb("GLOW", hex2rgb(pal["glow"]))
    em = nb.node("ShaderNodeEmission")
    nb.link(gcol.outputs[0], em.inputs["Color"])
    nb.link(glow_amt, em.inputs["Strength"])
    add = nb.node("ShaderNodeAddShader")
    nb.link(bs.outputs[0], add.inputs[0])
    nb.link(em.outputs[0], add.inputs[1])
    # clear PET above the fill line (thin-wall glass approximation)
    clear = thin_pet(nb, cfg)
    zf = geo.z(info["fill"])
    above = nb.maprange(sep.outputs["Z"], zf - 0.006, zf + 0.006)
    mixs = nb.node("ShaderNodeMixShader")
    nb.link(above, mixs.inputs[0])
    nb.link(add.outputs[0], mixs.inputs[1])
    nb.link(clear, mixs.inputs[2])
    output(nb, mixs.outputs[0])
    return m


def sil_tint(nb, cfg, sil, color):
    """Silhouette shots: multiply towards a deep backlit-amber tint (not black)."""
    nd = nb.node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    nb.link(sil, sock_id(nd.inputs, "Factor_Float"))
    a = sock_id(nd.inputs, "A_Color")
    if hasattr(color, "is_output"):
        nb.link(color, a)
    else:
        a.default_value = (*color, 1.0)
    sock_id(nd.inputs, "B_Color").default_value = (*cfg["silhouette"]["tint"], 1.0)
    return sock_id(nd.outputs, "Result_Color")


def thin_pet(nb, cfg):
    """Clear PET wall: thin-walled transmission (no solid-glass refraction)."""
    tint = hex2rgb(cfg["pet_tint"])
    bs = principled(nb, **{"Base Color": (*tint, 1.0), "Roughness": 0.06, "IOR": 1.5,
                           "Transmission Weight": 1.0, "Thin Wall": True})
    return bs.outputs[0]


def mat_simple(name, **kw):
    m, nb = new_material(name)
    bs = principled(nb, **kw)
    output(nb, bs.outputs[0])
    return m


def mat_droplet():
    # thin-walled: crisp highlights without dark total-internal-reflection specks
    return mat_simple("Droplet", **{"Base Color": (1, 1, 1, 1), "Roughness": 0.0, "IOR": 1.33,
                                    "Transmission Weight": 1.0, "Thin Wall": True, "Specular IOR Level": 1.0})


def mat_cap(cfg):
    m, nb = new_material("Cap")
    sil = nb.value("SIL", 0.0).outputs[0]
    col = sil_tint(nb, cfg, sil, hex2rgb(cfg["cap_color"]))
    noise = nb.node("ShaderNodeTexNoise", inputs={"Scale": 420.0, "Detail": 2.0})
    bump = nb.node("ShaderNodeBump", inputs={"Strength": 0.04})
    nb.link(noise.outputs["Fac"], bump.inputs["Height"])
    bs = principled(nb, **{"Base Color": col, "Roughness": 0.22, "Coat Weight": 0.7, "Coat Roughness": 0.06,
                           "Normal": bump.outputs["Normal"],
                           "Emission Color": col, "Emission Strength": nb.math("MULTIPLY_ADD", sil, 0.25, 0.12)})
    output(nb, bs.outputs[0])
    return m


def mat_juice_top(cfg, key):
    pal = cfg["palettes"][cfg["variants"][key]["palette"]]
    m, nb = new_material(f"JuiceTop_{key}")
    sil = nb.value("SIL", 0.0).outputs[0]
    col = sil_tint(nb, cfg, sil, hex2rgb(pal["glow"]))
    bs = principled(nb, **{"Base Color": col, "Roughness": 0.05, "Emission Color": col, "Emission Strength": 0.6})
    output(nb, bs.outputs[0])
    return m


def bg_group(cfg):
    """Screen-space vertical gradient + radial hot spot (shared by backdrop and floor)."""
    ng = bpy.data.node_groups.new("BG_GRAD", "ShaderNodeTree")
    for name, kind in (("Top", "NodeSocketColor"), ("Bottom", "NodeSocketColor"),
                       ("Hot", "NodeSocketColor"), ("HotAmt", "NodeSocketFloat")):
        ng.interface.new_socket(name, in_out="INPUT", socket_type=kind)
    ng.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    nb = NB(ng)
    gi = nb.node("NodeGroupInput")
    go = nb.node("NodeGroupOutput")
    tc = nb.node("ShaderNodeTexCoord")
    sep = nb.node("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Window"], sep.inputs[0])
    g0, g1 = cfg["grad_range"]
    f = nb.maprange(sep.outputs["Y"], g0, g1)
    grad = nb.mix(f, gi.outputs["Bottom"], gi.outputs["Top"])
    aspect = cfg["resolution"][0] / cfg["resolution"][1]
    dx = nb.math("MULTIPLY", nb.math("SUBTRACT", sep.outputs["X"], 0.5), aspect)
    dy = nb.math("SUBTRACT", sep.outputs["Y"], 0.5)
    d = nb.math("SQRT", nb.math("ADD", nb.math("MULTIPLY", dx, dx), nb.math("MULTIPLY", dy, dy)))
    hot = nb.maprange(d, 0.0, cfg["hot_radius"], 1.0, 0.0)
    amt = nb.math("MULTIPLY", hot, gi.outputs["HotAmt"])
    col = nb.mix(amt, grad, gi.outputs["Hot"])
    nb.link(col, go.inputs["Color"])
    return ng


def build_backdrop_and_floor(cfg, cam):
    ng = bg_group(cfg)
    bgs = []
    # backdrop: camera-parented plane, only visible to camera/transmission rays
    m, nb = new_material("Backdrop")
    g = nb.node("ShaderNodeGroup")
    g.node_tree = ng
    em = nb.node("ShaderNodeEmission")
    nb.link(g.outputs[0], em.inputs["Color"])
    output(nb, em.outputs[0])
    bgs.append(g)
    D = 120.0
    half = D * 18.0 / 12.0 * 1.2
    bd = mesh_object("Backdrop", [(-half, -half, -D), (half, -half, -D), (half, half, -D), (-half, half, -D)],
                     [(0, 1, 2, 3)], smooth=False)
    bd.data.materials.append(m)
    bd.parent = cam
    for attr in ("visible_diffuse", "visible_glossy", "visible_shadow", "visible_volume_scatter"):
        setattr(bd, attr, False)
    # floor: same gradient (seamless), contact AO and a glossy mirror reflection
    m, nb = new_material("Floor")
    g = nb.node("ShaderNodeGroup")
    g.node_tree = ng
    bgs.append(g)
    ao = nb.node("ShaderNodeAmbientOcclusion", samples=4, inputs={"Distance": 0.8})
    aof = nb.maprange(ao.outputs["AO"], 0.0, 1.0, 0.5, 1.0, smooth=False)
    col = nb.node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    sock_id(col.inputs, "Factor_Float").default_value = 1.0
    nb.link(g.outputs[0], sock_id(col.inputs, "A_Color"))
    nb.link(aof, sock_id(col.inputs, "B_Color"))
    em = nb.node("ShaderNodeEmission")
    nb.link(sock_id(col.outputs, "Result_Color"), em.inputs["Color"])
    gl = nb.node("ShaderNodeBsdfGlossy", inputs={"Roughness": 0.06})
    # reflection fades with distance from the products
    tc = nb.node("ShaderNodeTexCoord")
    dist = nb.node("ShaderNodeVectorMath", operation="LENGTH")
    nb.link(tc.outputs["Object"], dist.inputs[0])
    fade = nb.maprange(dist.outputs["Value"], 1.5, 6.0, cfg["floor_reflect"], 0.0)
    mx = nb.node("ShaderNodeMixShader")
    nb.link(fade, mx.inputs[0])
    nb.link(em.outputs[0], mx.inputs[1])
    nb.link(gl.outputs[0], mx.inputs[2])
    output(nb, mx.outputs[0])
    S = 60
    fl = mesh_object("Floor", [(-S, -S, 0), (S, -S, 0), (S, S, 0), (-S, S, 0)], [(0, 1, 2, 3)], smooth=False)
    fl.location.z = cfg["floor_z"]
    fl.data.materials.append(m)
    fl.visible_diffuse = False
    return bgs, fl


def build_world(cfg):
    w = bpy.data.worlds.new("World")
    try:
        w.use_nodes = True
    except Exception:  # noqa: BLE001
        pass
    nt = w.node_tree
    nt.nodes.clear()
    nb = NB(nt)
    tc = nb.node("ShaderNodeTexCoord")
    sep = nb.node("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Generated"], sep.inputs[0])
    top = nb.rgb("ENV_TOP", (1, 0.8, 0.5))
    bot = nb.rgb("ENV_BOT", (1, 0.5, 0.1))
    f = nb.maprange(sep.outputs["Z"], -0.25, 0.6)
    c1 = nb.mix(f, bot.outputs[0], top.outputs[0])
    up = nb.maprange(sep.outputs["Z"], 0.55, 1.0, 0.0, 1.0)
    c2 = nb.mix(up, c1, (1.3, 1.25, 1.2))
    bgn = nb.node("ShaderNodeBackground", inputs={"Strength": cfg["world_strength"]})
    nb.link(c2, bgn.inputs["Color"])
    out = nb.node("ShaderNodeOutputWorld")
    nb.link(bgn.outputs[0], out.inputs["Surface"])
    bpy.context.scene.world = w
    return w, top, bot


# ----------------------------------------------------------------- props


def mat_orange_flesh():
    m, nb = new_material("OrangeFlesh")
    tc = nb.node("ShaderNodeTexCoord")
    sep = nb.node("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Object"], sep.inputs[0])
    x, y = sep.outputs["X"], sep.outputs["Y"]
    r = nb.math("SQRT", nb.math("ADD", nb.math("MULTIPLY", x, x), nb.math("MULTIPLY", y, y)))
    ang = nb.math("ARCTAN2", y, x)
    seg = nb.math("FRACT", nb.math("MULTIPLY", ang, 10 / (2 * math.pi)))
    memb = nb.maprange(nb.math("ABSOLUTE", nb.math("SUBTRACT", seg, 0.5)), 0.44, 0.49)
    centre = nb.maprange(r, 0.08, 0.04)
    pith = nb.maprange(r, 0.80, 0.84)
    peel = nb.maprange(r, 0.92, 0.95)
    noise = nb.node("ShaderNodeTexNoise", inputs={"Scale": 30.0, "Detail": 4.0})
    c = nb.mix(noise.outputs["Fac"], (1.0, 0.33, 0.01), (1.0, 0.5, 0.05))
    c = nb.mix(nb.math("MAXIMUM", memb, centre), c, (1.0, 0.75, 0.45))
    c = nb.mix(pith, c, (1.0, 0.85, 0.6))
    c = nb.mix(peel, c, (0.95, 0.25, 0.0))
    bs = principled(nb, **{"Base Color": c, "Roughness": 0.18, "Coat Weight": 0.6,
                           "Emission Color": c, "Emission Strength": 0.25})
    output(nb, bs.outputs[0])
    return m


def mat_pulp():
    m, nb = new_material("PassionPulp")
    vor = nb.node("ShaderNodeTexVoronoi", feature="F1", inputs={"Scale": 14.0})
    seeds = nb.maprange(vor.outputs["Distance"], 0.16, 0.24, 1.0, 0.0)
    tc = nb.node("ShaderNodeTexCoord")
    sep = nb.node("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Object"], sep.inputs[0])
    x, y = sep.outputs["X"], sep.outputs["Y"]
    r = nb.math("SQRT", nb.math("ADD", nb.math("MULTIPLY", x, x), nb.math("MULTIPLY", y, y)))
    rim = nb.maprange(r, 0.82, 0.88)
    c = nb.mix(seeds, (1.0, 0.55, 0.02), (0.03, 0.02, 0.01))
    c = nb.mix(rim, c, (0.95, 0.85, 0.6))
    bs = principled(nb, **{"Base Color": c, "Roughness": 0.08, "Coat Weight": 1.0,
                           "Emission Color": c, "Emission Strength": 0.18})
    output(nb, bs.outputs[0])
    return m


def build_props(cfg):
    sets = {"orange": [], "passion": [], "seabuck": []}
    peel = mat_simple("OrangePeel", **{"Base Color": (1.0, 0.28, 0.0, 1), "Roughness": 0.35, "Coat Weight": 0.4,
                                       "Emission Color": (1.0, 0.28, 0.0, 1), "Emission Strength": 0.12})
    flesh = mat_orange_flesh()
    skin = mat_simple("PassionSkin", **{"Base Color": (0.16, 0.012, 0.09, 1), "Roughness": 0.3, "Coat Weight": 0.8})
    pulp = mat_pulp()
    berry = mat_simple("Berry", **{"Base Color": (1.0, 0.36, 0.0, 1), "Roughness": 0.12, "Coat Weight": 1.0,
                                   "Emission Color": (1.0, 0.36, 0.0, 1), "Emission Strength": 0.3})
    leaf = mat_simple("Leaf", **{"Base Color": (0.06, 0.28, 0.03, 1), "Roughness": 0.4, "Coat Weight": 0.3})
    iv, ifc = icosphere(3)
    iv = [np.asarray(v) for v in iv]

    def sphere(name, r, mat, scale=(1, 1, 1)):
        ob = mesh_object(name, [v * r * np.array(scale) for v in iv], ifc)
        ob.data.materials.append(mat)
        return ob

    def disc(name, r, h, n=64):
        th = np.linspace(0, 2 * np.pi, n, endpoint=False)
        ring_t = np.stack([r * np.cos(th), r * np.sin(th), np.full(n, h / 2)], -1)
        ring_b = np.stack([r * np.cos(th), r * np.sin(th), np.full(n, -h / 2)], -1)
        v, f = lathe([ring_t, ring_b], n, close_top=(0, 0, h / 2), close_bottom=(0, 0, -h / 2))
        ob = mesh_object(name, v, f)
        ob.data.materials.append(flesh)
        ob.data.materials.append(peel)
        for p in ob.data.polygons:   # side faces -> peel
            if len(p.vertices) == 4:
                p.material_index = 1
        return ob

    def half(name, r):
        vs = [v * r for v in iv if v[2] <= 0.02]
        idx = {i: k for k, i in enumerate(i for i, v in enumerate(iv) if v[2] <= 0.02)}
        fs = [tuple(idx[i] for i in f) for f in ifc if all(i in idx for i in f)]
        rim = [k for i, k in idx.items() if abs(iv[i][2]) < 0.03]
        c = len(vs)
        vs.append(np.array([0, 0, 0.0]))
        rim_sorted = sorted(rim, key=lambda k: math.atan2(vs[k][1], vs[k][0]))
        cap_faces = [(c, rim_sorted[i], rim_sorted[(i + 1) % len(rim_sorted)]) for i in range(len(rim_sorted))]
        ob = mesh_object(name, vs, fs + cap_faces)
        ob.data.materials.append(skin)
        ob.data.materials.append(pulp)
        for p in ob.data.polygons[len(fs):]:
            p.material_index = 1
        return ob

    total = cfg["props"]["count_back"] + cfg["props"]["count_front"]
    for i in range(total):
        if i % 3 == 2:
            sets["orange"].append(sphere(f"Orange_{i}", 0.34, peel))
        else:
            sets["orange"].append(disc(f"OrangeSlice_{i}", 0.36, 0.09))
        if i % 3 == 2:
            sets["passion"].append(sphere(f"Passion_{i}", 0.3, skin, (1, 1, 1.08)))
        else:
            sets["passion"].append(half(f"PassionHalf_{i}", 0.3))
        # sea buckthorn: small cluster of berries + a leaf
        cl = []
        rr = random.Random(100 + i)
        for k in range(rr.randint(3, 5)):
            b = sphere(f"Berry_{i}_{k}", 0.075 + 0.02 * rr.random(), berry, (1, 1, 1.15))
            b.location = (rr.uniform(-0.12, 0.12), rr.uniform(-0.12, 0.12), rr.uniform(-0.12, 0.12))
            cl.append(b)
        lf = sphere(f"Leaf_{i}", 0.25, leaf, (0.16, 1.0, 0.025))
        lf.location = (0.05, 0.15, 0.05)
        lf.rotation_euler = (0.4, 0.2, 0.8)
        cl.append(lf)
        root = bpy.data.objects.new(f"BerryCluster_{i}", None)
        bpy.context.scene.collection.objects.link(root)
        for b in cl:
            b.parent = root
        sets["seabuck"].append(root)
    return sets


def build_logo_plane(cfg):
    img = bpy.data.images.load(os.path.join(BUILD, "logo_white.png"))
    img.alpha_mode = "STRAIGHT"
    w, h = img.size
    a = h / w
    ob = mesh_object("LogoPlane", [(-0.5, 0, -a / 2), (0.5, 0, -a / 2), (0.5, 0, a / 2), (-0.5, 0, a / 2)],
                     [(0, 1, 2, 3)], smooth=False)
    me = ob.data
    uvl = me.uv_layers.new()
    for i, uv in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
        uvl.data[i].uv = uv
    m, nb = new_material("Logo")
    tex = nb.node("ShaderNodeTexImage", interpolation="Cubic")
    tex.image = img
    fade = nb.value("FADE", 1.0).outputs[0]
    em = nb.node("ShaderNodeEmission", inputs={"Strength": 1.0})
    nb.link(tex.outputs["Color"], em.inputs["Color"])
    tr = nb.node("ShaderNodeBsdfTransparent")
    mx = nb.node("ShaderNodeMixShader")
    nb.link(nb.math("MULTIPLY", tex.outputs["Alpha"], fade), mx.inputs[0])
    nb.link(tr.outputs[0], mx.inputs[1])
    nb.link(em.outputs[0], mx.inputs[2])
    output(nb, mx.outputs[0])
    me.materials.append(m)
    ob.visible_diffuse = False
    ob.visible_shadow = False
    # local frame: plane lies in XZ, rotate X by 90 deg in shot data means "facing up"; we
    # want rot (90,0,0) to face the camera, so pre-rotate the mesh by -90 around X
    for v in me.vertices:
        x, y, z = v.co
        v.co = (x, z, -y)
    return ob, m


# ----------------------------------------------------------------- scene assembly


class Rig:
    pass


def build_scene(cfg, infos):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    sc = bpy.context.scene
    rig = Rig()
    geo = Geo(cfg, infos[cfg["profile_source"]])
    rig.geo = geo
    body, cap, disk, drops = build_bottle_meshes(cfg, geo)
    m_drop = mat_droplet()
    m_cap = mat_cap(cfg)
    rig.bottles = {}
    rig.sil_nodes = {}
    for key in cfg["variants"]:
        root = bpy.data.objects.new(f"Bottle_{key}", None)
        sc.collection.objects.link(root)
        b = mesh_object(f"Body_{key}", *body)
        mlab = mat_label(cfg, key, infos[key], geo)
        b.data.materials.append(mlab)
        c = mesh_object(f"Cap_{key}", *cap)
        mc = m_cap.copy()
        c.data.materials.append(mc)
        d = mesh_object(f"Drops_{key}", *drops)
        d.data.materials.append(m_drop)
        j = mesh_object(f"JuiceTop_{key}", *disk, smooth=False)
        mj = mat_juice_top(cfg, key)
        j.data.materials.append(mj)
        for ob in (b, c, d, j):
            ob.parent = root
        rig.bottles[key] = {"root": root, "children": [b, c, d, j]}
        rig.sil_nodes[key] = [mm.node_tree.nodes["SIL"] for mm in (mlab, mc, mj)]
        log(f"bottle {key}: body {len(body[0])} v, drops {len(drops[0])} v")

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.sensor_fit = "VERTICAL"
    cam_data.sensor_height = 36.0
    cam_data.clip_start = 0.05
    cam_data.clip_end = 400
    cam = bpy.data.objects.new("Camera", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    rig.cam = cam
    rig.bg_nodes, rig.floor = build_backdrop_and_floor(cfg, cam)
    rig.world, rig.env_top, rig.env_bot = build_world(cfg)

    rig.lights = {}
    for name, L in cfg["lights"].items():
        ld = bpy.data.lights.new(name, "AREA")
        ld.shape = "RECTANGLE"
        ld.size, ld.size_y = L["size"]
        ld.energy = L["energy"]
        ld.color = hex2rgb(L["color"], linear=False)
        lo = bpy.data.objects.new(name, ld)
        sc.collection.objects.link(lo)
        lo.location = L["pos"]
        d = -np.asarray(L["pos"], float)
        from mathutils import Vector
        lo.rotation_euler = Vector(d).to_track_quat("-Z", "Y").to_euler()
        lo.visible_camera = False
        rig.lights[name] = lo

    rig.props = build_props(cfg)
    rig.logo, rig.logo_mat = build_logo_plane(cfg)
    return rig


# ----------------------------------------------------------------- state evaluation


def shot_at(cfg, frame):
    t = frame / cfg["fps"]
    for s in cfg["shots"]:
        if s["start"] - 1e-6 <= t < s["end"] - 1e-6:
            return s
    return cfg["shots"][-1]


def camera_pose(shot, u, k):
    c = shot["cam"]
    tgt = np.asarray(track(c["target"], u, k), float)
    if "orbit" in c:
        o = c["orbit"]
        ang = math.radians(track(o["angle"], u, k))
        dist = track(o["dist"], u, k)
        hgt = track(o["height"], u, k)
        pos = tgt + np.array([dist * math.sin(ang), -dist * math.cos(ang), hgt])
    else:
        pos = np.asarray(track(c["pos"], u, k), float)
    return pos, tgt, track(c["lens"], u, k)


def prop_layout(cfg, shot, key, cam_pos, cam_tgt, lens):
    """World anchors for the props of a shot, laid out in the shot's opening frustum."""
    import zlib
    rng = random.Random(zlib.crc32(f'{shot["name"]}|{key}|{cfg["props"]["seed"]}'.encode()))  # stable across runs
    fwd = cam_tgt - cam_pos
    focus = np.linalg.norm(fwd)
    fwd /= focus
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    tan_v = 18.0 / lens
    tan_h = tan_v * cfg["resolution"][0] / cfg["resolution"][1]
    out = []
    nb, nf = cfg["props"]["count_back"], cfg["props"]["count_front"]
    for i in range(nb + nf):
        if i >= nb and not shot.get("front_props", True):
            out.append((cam_pos - fwd * 50, np.zeros(3), np.zeros(3), np.zeros(3), 0.001))  # parked
            continue
        front = i >= nb
        depth = focus * (rng.uniform(0.45, 0.6) if front else rng.uniform(1.6, 3.2))
        while True:
            sx, sy = rng.uniform(-1.1, 1.1), rng.uniform(-0.95, 0.95)
            if front and abs(sx) > 0.9 and abs(sy) > 0.45:   # foreground: only peeking at a corner
                break
            if not front and (abs(sx) > 0.42 or abs(sy) > 0.8):
                break
        p = cam_pos + fwd * depth + right * sx * depth * tan_h + up * sy * depth * tan_v
        vel = np.array([rng.uniform(-0.12, 0.12), rng.uniform(-0.05, 0.05), rng.uniform(0.04, 0.16)])
        rot0 = np.array([rng.uniform(0, 6.3) for _ in range(3)])
        spin = np.array([rng.uniform(-0.6, 0.6) for _ in range(3)])
        if key in ("orange", "passion"):
            # slices / halves: show the cut face to camera, tilted +-45 deg, slow wobble
            from mathutils import Euler, Vector
            q = Vector(-fwd).to_track_quat("Z", "Y")
            q.rotate(Euler((rng.uniform(-0.8, 0.8), rng.uniform(-0.8, 0.8), 0.0)))
            e = q.to_euler()
            rot0 = np.array([e.x, e.y, e.z])
            spin = spin * 0.35
        scl = rng.uniform(0.85, 1.25) * (cfg["props"]["front_scale"] if front else 1.0)
        out.append((p, vel, rot0, spin, scl))
    return out


_layout_cache = {}


def eval_state(cfg, frame, rig):
    shot = shot_at(cfg, int(math.floor(frame + 1e-6)))
    t = frame / cfg["fps"]
    dur = shot["end"] - shot["start"]
    u = (t - shot["start"]) / dur
    k = shot.get("ease", cfg["ease"])
    st = {"shot": shot["name"]}
    pos, tgt, lens = camera_pose(shot, u, k)
    ax, az, fq = cfg["camera_drift"]
    drift = np.array([ax * math.sin(2 * math.pi * fq * t), ax * 0.6 * math.sin(2 * math.pi * fq * 0.7 * t + 1.3),
                      az * math.sin(2 * math.pi * fq * 1.3 * t + 0.4)])
    pos = pos + drift
    st["cam"] = (pos, tgt, lens, shot["cam"].get("fstop", 4.0))
    st["focus_pull"] = shot["cam"].get("focus_pull", cfg["focus_pull"])
    # palette (optional blend at the head of the shot)
    pal = cfg["palettes"][shot["palette"]]
    if "palette_from" in shot:
        pf = cfg["palettes"][shot["palette_from"]]
        b = smoothstep(0.0, shot.get("palette_blend", 0.5), t - shot["start"])
        pal = {kk: (lerp(hex2rgb(pf[kk]), hex2rgb(pal[kk]), b) if kk != "hot_amt" else lerp(pf[kk], pal[kk], b))
               for kk in ("top", "bottom", "hot", "hot_amt")}
    else:
        pal = {kk: (hex2rgb(pal[kk]) if kk != "hot_amt" else pal[kk]) for kk in ("top", "bottom", "hot", "hot_amt")}
    st["pal"] = pal
    st["lights"] = cfg["light_presets"][shot["lights"]]
    st["floor"] = shot["floor"]
    # bottles
    amp, fqb, wob = cfg["bottle_bob"]
    st["bottles"] = {}
    for i, key in enumerate(cfg["variants"]):
        bs = shot["bottles"].get(key)
        if bs is None:
            continue
        p = np.asarray(track(bs["pos"], u, k), float).copy()
        r = np.asarray(track(bs["rot"], u, k), float).copy()
        ph = i * 2.1
        if bs.get("grounded"):
            p[2] = cfg["floor_z"] + rig.geo.H / 2 - (rig.geo.z(rig.geo.info["y_bot"]) + rig.geo.H / 2) + 0.004
            r[2] += 2.5 * math.sin(2 * math.pi * 0.35 * t + ph)
        elif bs.get("float", True):
            p[2] += amp * math.sin(2 * math.pi * fqb * t + ph)
            p[0] += amp * 0.4 * math.sin(2 * math.pi * fqb * 0.6 * t + ph + 1)
            r[0] += wob * math.sin(2 * math.pi * fqb * 0.8 * t + ph)
            r[1] += wob * math.sin(2 * math.pi * fqb * 0.65 * t + ph + 2)
        if bs.get("whip"):
            # fast spin at the head of the shot, hidden by motion blur
            w = 1 - (1 - min(max((t - shot["start"]) / 0.4, 0), 1)) ** 3
            r[2] += bs["whip"] * (w - 1)
        st["bottles"][key] = (p, r, bs.get("sil", 0.0))
    # props
    st["props"] = None
    if shot.get("props"):
        ck = (shot["name"], shot["props"])
        if ck not in _layout_cache:
            p0, t0, l0 = camera_pose(shot, 0.0, k)
            _layout_cache[ck] = prop_layout(cfg, shot, shot["props"], p0, t0, l0)
        lt = t - shot["start"]
        st["props"] = (shot["props"], [(p + vel * lt + np.array([0, 0, 0.05 * math.sin(1.7 * lt + i)]),
                                        rot0 + spin * lt, sc)
                                       for i, (p, vel, rot0, spin, sc) in enumerate(_layout_cache[ck])])
    # logo
    st["logo"] = None
    if shot.get("logo"):
        lg = shot["logo"]
        kk = lg.get("ease", k)
        st["logo"] = (np.asarray(track(lg["pos"], u, kk), float), np.asarray(track(lg["rot"], u, kk), float),
                      lg["width"], track(lg["fade"], u, kk))
    return st


def apply_state(cfg, rig, st, key_frame=None):
    from mathutils import Vector
    keys = []

    def K(owner, path):
        if key_frame is not None:
            keys.append((owner, path))

    pos, tgt, lens, fstop = st["cam"]
    cam = rig.cam
    cam.location = Vector(pos)
    cam.rotation_euler = (Vector(tgt) - Vector(pos)).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = lens
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = max(0.1, float(np.linalg.norm(np.asarray(tgt) - np.asarray(pos))) - st["focus_pull"])
    cam.data.dof.aperture_fstop = fstop
    K(cam, "location"), K(cam, "rotation_euler"), K(cam.data, "lens")
    K(cam.data.dof, "focus_distance"), K(cam.data.dof, "aperture_fstop")

    pal = st["pal"]
    for g in rig.bg_nodes:
        g.inputs["Top"].default_value = (*pal["top"], 1)
        g.inputs["Bottom"].default_value = (*pal["bottom"], 1)
        g.inputs["Hot"].default_value = (*pal["hot"], 1)
        g.inputs["HotAmt"].default_value = pal["hot_amt"]
        for s in ("Top", "Bottom", "Hot", "HotAmt"):
            K(g.inputs[s], "default_value")
    rig.env_top.outputs[0].default_value = (*lerp(pal["top"], (1, 1, 1), 0.35), 1)
    rig.env_bot.outputs[0].default_value = (*pal["bottom"], 1)
    K(rig.env_top.outputs[0], "default_value"), K(rig.env_bot.outputs[0], "default_value")

    for name, lo in rig.lights.items():
        lo.data.energy = cfg["lights"][name]["energy"] * st["lights"].get(name, 1.0)
        K(lo.data, "energy")

    rig.floor.hide_render = not st["floor"]
    K(rig.floor, "hide_render")

    for key, b in rig.bottles.items():
        root = b["root"]
        vis = key in st["bottles"]
        if vis:
            p, r, sil = st["bottles"][key]
            root.location = Vector(p)
            root.rotation_euler = [math.radians(x) for x in r]
        else:
            root.location = (0, 60, -60)
            sil = 0.0
        for ch in b["children"]:
            ch.hide_render = not vis
            K(ch, "hide_render")
        K(root, "location"), K(root, "rotation_euler")
        for nd in rig.sil_nodes[key]:
            nd.outputs[0].default_value = sil
            K(nd.outputs[0], "default_value")

    active = st["props"][0] if st["props"] else None
    for set_name, objs in rig.props.items():
        for i, ob in enumerate(objs):
            vis = set_name == active
            if vis:
                p, rot, sc = st["props"][1][i]
                ob.location = Vector(p)
                ob.rotation_euler = tuple(rot)
                ob.scale = (sc, sc, sc)
            else:
                ob.location = (0, 80, -80)
            obs = [ob] + list(ob.children)
            for o in obs:
                o.hide_render = not vis
                K(o, "hide_render")
            K(ob, "location"), K(ob, "rotation_euler"), K(ob, "scale")

    lg = rig.logo
    fade_node = rig.logo_mat.node_tree.nodes["FADE"]
    if st["logo"]:
        p, r, wdt, fade = st["logo"]
        lg.location = Vector(p)
        lg.rotation_euler = [math.radians(x) for x in r]
        lg.scale = (wdt, wdt, wdt)
        lg.hide_render = False
        fade_node.outputs[0].default_value = fade
    else:
        lg.hide_render = True
        lg.location = (0, 80, -80)
        fade_node.outputs[0].default_value = 0.0
    K(lg, "location"), K(lg, "rotation_euler"), K(lg, "scale"), K(lg, "hide_render")
    K(fade_node.outputs[0], "default_value")

    if key_frame is not None:
        for owner, path in keys:
            owner.keyframe_insert(path, frame=key_frame)


def bake(cfg, rig, f0, f1):
    """Key every frame and half frame so motion blur (shutter opens on the frame)
    never interpolates across a cut: frame f+0.5 is still evaluated in the shot of
    frame f (eval_state floors the frame), extrapolating its moves slightly."""
    prefs = bpy.context.preferences.edit
    prefs.keyframe_new_interpolation_type = "LINEAR"
    t0 = time.time()
    for f in range(f0, f1 + 1):
        for sub in (0.0, 0.5):
            apply_state(cfg, rig, eval_state(cfg, f + sub, rig), key_frame=f + sub)
    log(f"baked frames {f0}-{f1} in {time.time() - t0:.1f}s")


# ----------------------------------------------------------------- render setup


def setup_render(cfg, quality, percent):
    sc = bpy.context.scene
    sc.render.engine = cfg["engine"]
    sc.render.fps = cfg["fps"]
    sc.render.resolution_x, sc.render.resolution_y = cfg["resolution"]
    sc.render.resolution_percentage = percent
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = cfg["motion_blur_shutter"]
    for owner in (sc.render, getattr(sc, "cycles", None)):
        if owner is not None and hasattr(owner, "motion_blur_position"):
            owner.motion_blur_position = "START"
    q = cfg["quality"][quality]
    if cfg["engine"] == "CYCLES":
        cy = sc.cycles
        cy.device = "CPU"
        cy.samples = q["samples"]
        cy.use_adaptive_sampling = True
        cy.adaptive_threshold = q["threshold"]
        cy.use_denoising = True
        cy.denoiser = "OPENIMAGEDENOISE"
        cy.max_bounces = 6
        cy.diffuse_bounces = 1
        cy.glossy_bounces = 3
        cy.transmission_bounces = 6
        cy.transparent_max_bounces = 8
        cy.volume_bounces = 0
        cy.light_sampling_threshold = 0.02
        cy.caustics_reflective = False
        cy.caustics_refractive = False
        cy.blur_glossy = 1.0
        sc.render.use_persistent_data = True
    else:
        ee = sc.eevee
        ee.taa_render_samples = max(16, q["samples"])
        if hasattr(ee, "use_raytracing"):
            ee.use_raytracing = True


# =============================================================================
# POST — bloom, vignette, typography
# =============================================================================

_font_cache = {}


def get_font(cfg, size):
    key = size
    if key not in _font_cache:
        f = ImageFont.truetype(P(cfg["font_file"]), size)
        try:
            f.set_variation_by_name(cfg["font_weight"])
        except Exception:  # noqa: BLE001
            pass
        _font_cache[key] = f
    return _font_cache[key]


def ease_out_back(x, s=1.9):
    x = min(max(x, 0.0), 1.0) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def draw_headline(cfg, canvas, t, scale):
    hl = cfg["headline"]
    if t < hl["start"]:
        return
    W = canvas.width
    size = int(hl["size"] * scale)
    font = get_font(cfg, size)
    # shrink to max width
    widest = max(font.getlength(line) for line in hl["lines"])
    if widest > hl["max_width"] * scale:
        size = int(size * hl["max_width"] * scale / widest)
        font = get_font(cfg, size)
    asc, desc = font.getmetrics()
    line_h = asc + desc + hl["line_gap"] * scale
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    idx = 0
    y = hl["y"] * scale
    for line in hl["lines"]:
        lw = font.getlength(line)
        x = (W - lw) / 2
        for ch_i, ch in enumerate(line):
            adv = font.getlength(line[:ch_i + 1]) - font.getlength(line[:ch_i])
            if not ch.isspace():
                lt = t - hl["start"] - idx * hl["stagger"]
                p = lt / hl["pop"]
                if p > 0:
                    s = max(ease_out_back(p), 0.001)
                    a = min(1.0, p * 3)
                    glyph = Image.new("RGBA", (int(adv + size), int(line_h + size)), (0, 0, 0, 0))
                    ImageDraw.Draw(glyph).text((size / 2, size / 2), ch, font=font,
                                               fill=(*[int(c * 255) for c in hex2rgb(hl["color"], False)], int(255 * a)))
                    gw, gh = glyph.size
                    nw, nh = max(1, int(gw * s)), max(1, int(gh * s))
                    g2 = glyph.resize((nw, nh), Image.BICUBIC)
                    cxp = x + font.getlength(line[:ch_i]) + adv / 2
                    cyp = y + line_h * 0.55 + (1 - min(p, 1)) * 0.25 * size
                    layer.alpha_composite(g2, (int(cxp - nw / 2), int(cyp - nh / 2 - (line_h * 0.05))))
                idx += 1
        y += line_h
    sh = hl["shadow"]
    alpha = layer.split()[3]
    shadow = Image.new("RGBA", canvas.size, (*[int(c * 255) for c in hex2rgb(sh["color"], False)], 0))
    shadow.putalpha(alpha.point(lambda v: int(v * sh["alpha"])))
    shadow = shadow.filter(ImageFilter.GaussianBlur(sh["blur"] * scale))
    off = (int(sh["offset"][0] * scale), int(sh["offset"][1] * scale))
    canvas.alpha_composite(shadow, off)
    canvas.alpha_composite(layer)


def draw_teaser(cfg, canvas, t, scale):
    tz = cfg["teaser"]
    if not (tz["start"] <= t <= tz["end"]):
        return
    a = min(smoothstep(tz["start"], tz["start"] + tz["fade"], t), 1 - smoothstep(tz["end"] - tz["fade"], tz["end"], t))
    font = get_font(cfg, int(tz["size"] * scale))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = font.getlength(tz["text"])
    yy = tz["y"] * scale + (1 - a) * 18 * scale
    d.text(((canvas.width - w) / 2, yy), tz["text"], font=font, fill=(255, 255, 255, int(255 * a)))
    sh = Image.new("RGBA", canvas.size, (90, 30, 0, 0))
    sh.putalpha(layer.split()[3].point(lambda v: int(v * 0.4)))
    sh = sh.filter(ImageFilter.GaussianBlur(10 * scale))
    canvas.alpha_composite(sh, (0, int(6 * scale)))
    canvas.alpha_composite(layer)


_logo_img = None


def draw_headline_logo(cfg, canvas, t, scale):
    global _logo_img
    hl = cfg["headline_logo"]
    if t < hl["start"]:
        return
    if _logo_img is None:
        _logo_img = Image.open(os.path.join(BUILD, "logo_white.png")).convert("RGBA")
    a = smoothstep(hl["start"], hl["start"] + hl["fade"], t)
    w = int(hl["width"] * scale)
    h = int(_logo_img.height * w / _logo_img.width)
    lg = _logo_img.resize((w, h), Image.LANCZOS)
    lg.putalpha(lg.split()[3].point(lambda v: int(v * a)))
    sh = Image.new("RGBA", lg.size, (90, 30, 0, 0))
    sh.putalpha(lg.split()[3].point(lambda v: int(v * 0.35)))
    sh = sh.filter(ImageFilter.GaussianBlur(8 * scale))
    x = (canvas.width - w) // 2
    y = int(hl["y"] * scale + (1 - a) * 14 * scale)
    canvas.alpha_composite(sh, (x, y + int(5 * scale)))
    canvas.alpha_composite(lg, (x, y))


def post_frame(cfg, img, t):
    """Bloom + vignette on the render, then the 2D typography."""
    pc = cfg["post"]
    scale = img.width / cfg["resolution"][0]
    arr = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    if pc["bloom"] > 0:
        small = img.convert("RGB").resize((img.width // 4, img.height // 4), Image.BILINEAR)
        s = np.asarray(small).astype(np.float32) / 255.0
        lum = s.max(-1, keepdims=True)
        s = s * np.clip((lum - pc["bloom_threshold"]) / (1 - pc["bloom_threshold"]), 0, 1)
        bl = Image.fromarray((s * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(pc["bloom_radius"] * img.width / 4))
        bl = np.asarray(bl.resize(img.size, Image.BILINEAR)).astype(np.float32) / 255.0
        arr = 1 - (1 - arr) * (1 - bl * pc["bloom"])
    if pc["vignette"] > 0:
        h, w = arr.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 * 0.6 + ((yy - h / 2) / (h / 2)) ** 2)
        arr *= (1 - pc["vignette"] * np.clip(d - 0.55, 0, 1) ** 1.5)[..., None]
    out = Image.fromarray((arr.clip(0, 1) * 255 + 0.5).astype(np.uint8))
    from PIL import ImageEnhance
    out = ImageEnhance.Brightness(out).enhance(pc["brightness"])
    out = ImageEnhance.Contrast(out).enhance(pc["contrast"])
    out = ImageEnhance.Color(out).enhance(pc["saturation"])
    sh = pc["sharpen"]
    out = out.filter(ImageFilter.UnsharpMask(radius=max(0.6, sh["radius"] * scale), percent=sh["percent"],
                                             threshold=sh["threshold"]))
    out = out.convert("RGBA")
    draw_teaser(cfg, out, t, scale)
    draw_headline(cfg, out, t, scale)
    draw_headline_logo(cfg, out, t, scale)
    return out.convert("RGB")


# =============================================================================
# STAGES
# =============================================================================


def prepare_blender(cfg):
    import_bpy()
    infos = load_infos(cfg)
    snap_shots(cfg)
    rig = build_scene(cfg, infos)
    return rig


def stage_stills(cfg):
    rig = prepare_blender(cfg)
    setup_render(cfg, "still", cfg["preview_percent"])
    sc = bpy.context.scene
    sc.render.use_motion_blur = False
    outdir = os.path.join(BUILD, "stills")
    os.makedirs(outdir, exist_ok=True)
    picks = cfg["still_times"]
    for name, t in picks.items():
        f = t * cfg["fps"]
        apply_state(cfg, rig, eval_state(cfg, f, rig))
        sc.frame_set(int(f))
        path = os.path.join(outdir, f"{name}_raw.png")
        sc.render.filepath = path
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        log(f"still {name} ({t}s) rendered in {time.time() - t0:.1f}s")
        post_frame(cfg, Image.open(path), t).save(os.path.join(outdir, f"{name}.png"))
    ims = [Image.open(os.path.join(outdir, f"{n}.png")) for n in picks]
    sheet = Image.new("RGB", (sum(i.width for i in ims) + 20 * (len(ims) - 1), ims[0].height), "white")
    x = 0
    for im in ims:
        sheet.paste(im, (x, 0))
        x += im.width + 20
    sheet.save(os.path.join(outdir, "contact_sheet.jpg"), quality=90)
    log("stills in", outdir)


def stage_render(cfg, quality, frames=None, encode=True, render=True):
    n_frames = int(round(cfg["duration"] * cfg["fps"]))
    f0, f1 = frames or (0, n_frames - 1)
    pct = cfg["preview_percent"] if quality == "preview" else 100
    outdir = os.path.join(BUILD, f"frames_{quality}")
    os.makedirs(outdir, exist_ok=True)
    if render:
        rig = prepare_blender(cfg)
        setup_render(cfg, quality, pct)
        sc = bpy.context.scene
        sc.frame_start, sc.frame_end = f0, f1
        bake(cfg, rig, max(0, f0 - 1), min(n_frames - 1, f1 + 1))
        sc.render.filepath = os.path.join(outdir, "f_")
        sc.render.use_overwrite = True
        t0 = time.time()
        bpy.ops.render.render(animation=True)
        log(f"rendered {f1 - f0 + 1} frames in {time.time() - t0:.0f}s")
    else:
        snap_shots(cfg)
    if encode:
        out = os.path.join(HERE, "preview.mp4" if quality == "preview" else "hivit_ad.mp4")
        encode_video(cfg, outdir, out, n_frames, pct)


def encode_video(cfg, frame_dir, out, n_frames, pct):
    W = cfg["resolution"][0] * pct // 100
    H = cfg["resolution"][1] * pct // 100
    W -= W % 2
    H -= H % 2
    music = P(cfg["music"])
    has_audio = os.path.exists(music)
    enc = cfg["encode"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(cfg["fps"]), "-i", "-"]
    if has_audio:
        fo = cfg["duration"] - enc["audio_fade"]
        cmd += ["-i", music, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", enc["audio_bitrate"],
                "-af", f"afade=t=out:st={fo}:d={enc['audio_fade']}", "-shortest"]
    else:
        log("assets/music.mp3 not found -> encoding WITHOUT audio")
    cmd += ["-c:v", "libx264", "-preset", enc["preset"], "-crf", str(enc["crf"]), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    last = None
    for f in range(n_frames):
        path = os.path.join(frame_dir, f"f_{f:04d}.png")
        if os.path.exists(path):
            last = Image.open(path)
        if last is None:
            last = Image.new("RGB", (W, H), "black")
        img = post_frame(cfg, last.resize((W, H)) if last.size != (W, H) else last, f / cfg["fps"])
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()
    log("wrote", out)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    stages = ["all"]
    frames = None
    encode, render = True, True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--stage":
            stages = argv[i + 1].split(",")
            i += 1
        elif a == "--frames":
            a0, a1 = argv[i + 1].split("-")
            frames = (int(a0), int(a1))
            i += 1
        elif a == "--times":   # e.g. --times hook=0.8,sil=2.0
            CONFIG["still_times"] = {kv.split("=")[0]: float(kv.split("=")[1]) for kv in argv[i + 1].split(",")}
            i += 1
        elif a == "--no-encode":
            encode = False
        elif a == "--encode-only":
            render = False
        i += 1
    if "all" in stages:
        stages = ["prep", "final"]
    os.chdir(HERE)
    for s in stages:
        log("stage", s)
        if s == "prep":
            stage_prep(CONFIG)
        elif s == "stills":
            stage_stills(CONFIG)
        elif s in ("preview", "final"):
            stage_render(CONFIG, s, frames, encode, render)
        else:
            raise SystemExit(f"unknown stage {s}")


if __name__ == "__main__":
    main()
