"""Hi Vit multifruit juice — 18 s vertical 3D product ad, fully procedural.

Everything (image cleanup, bottle modelling, materials, lighting, camera
moves, rendering and the final ffmpeg assembly) is driven from this file.

    blender -b -P make_ad.py -- --mode stills     # 4 look-dev stills (540x960)
    blender -b -P make_ad.py -- --mode preview    # low-quality full animation
    blender -b -P make_ad.py -- --mode final      # full quality + audio -> output/

The script also runs with Blender-as-a-module (pip install bpy==4.5.*):
    python make_ad.py --mode stills

Extra flags: --shots name1,name2 (render only those shots)
             --frames 10,11,200 (render only those frame numbers)
             --no-skip (re-render frames that already exist)
             --no-encode (skip the ffmpeg step)
"""
import math
import os
import random
import shutil
import subprocess
import sys

import bpy  # noqa: I001  (must come before bmesh when running as a module)
import bmesh
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()

# =============================================================================
# CONFIG — every tunable value lives here
# =============================================================================
CONFIG = {
    "fps": 30,
    "duration": 18.0,
    "bpm": 120,                       # beat grid used when there is no music
    "snap_window": 0.25,              # max seconds a cut may move to land on a beat
    "res": {"stills": (540, 960), "preview": (540, 960), "final": (1080, 1920)},
    "samples": {"stills": 48, "preview": 8, "final": 40},
    "adaptive_threshold": {"stills": 0.02, "preview": 0.1, "final": 0.03},
    "motion_blur_shutter": 0.5,
    "view_transform": "Standard",
    "look": "None",
    "exposure": -0.35,

    # ---- bottle geometry (metres) -----------------------------------------
    "bottle_height": 0.21,            # 450 ml PET bottle
    "pet_ior": 1.57,
    "pet_reflect": 1.6,               # multiplier on the fresnel reflection of the PET wall
    "pet_base_reflect": 0.02,
    "pet_tint": "#FBFDFF",
    "juice_inset": 0.0011,
    "fill_line_t": 0.152,             # fraction from top where the juice starts
    "cap_bottom_t": 0.088,            # fraction from top where the cap ends
    "neck_t": 0.12,                   # a row inside the clear neck (finish radius)
    "lathe_steps": 192,
    "cap_steps": 384,
    "cap_ribs": 72,
    "cap_rib_depth": 0.012,
    "petaloid_feet": 5,
    "petaloid_depth": 0.16,
    "grip_panels": 6,
    "grip_depth": 0.03,
    "proj_shrink": 0.975,             # sample the photo slightly inside its silhouette

    "droplets": {"count": 1600, "r_mean": 0.00055, "r_sigma": 0.55, "r_max": 0.0022,
                 "t_range": (0.17, 0.97), "flatten": 0.55, "seed": 7},
    "condensation_bump": 0.10,
    "condensation_scale": 1100.0,

    # ---- colours ------------------------------------------------------------
    "cap_color": "#FFC20E",
    "variants": {
        "orange":       {"image": "assets/hivit_orange.png",       "bg": ("#FF8A1F", "#FFC34D")},
        "passion":      {"image": "assets/hivit_passionfruit.png", "bg": ("#C2187A", "#FFD23F")},
        "seabuckthorn": {"image": "assets/hivit_seabuckthorn.png", "bg": ("#F26A1B", "#FFB347")},
    },
    "backgrounds": {                  # (bottom, top) of the cyclorama gradient
        "silhouette":  ("#FFB25C", "#FFE6BC"),
        "final_start": ("#F98B2F", "#FFE48A"),
        "final":       ("#E8590C", "#FFD23F"),
    },
    "bg_glow": 0.35,                  # soft lighter halo behind the product
    "env_strength": 0.55,             # how much the backdrop shows in reflections
    "label_glow": 0.12,               # emission on the printed label (keeps it juicy)
    "label_rough": 0.42,

    # ---- light rig (relative to the rig origin = product centre) -------------
    "lights": {
        "key":   {"loc": (-0.55, -0.62, 0.32), "size": (0.55, 0.85), "energy": 7.0, "color": "#FFF3E2"},
        "fill":  {"loc": (0.70, -0.45, 0.02),  "size": (0.60, 0.60), "energy": 2.0,  "color": "#FFFFFF"},
        "rim_l": {"loc": (-0.30, 0.30, 0.04),  "size": (0.05, 0.90), "energy": 9.0, "color": "#FFFFFF"},
        "rim_r": {"loc": (0.30, 0.30, 0.04),   "size": (0.05, 0.90), "energy": 9.0, "color": "#FFFFFF"},
        "top":   {"loc": (0.0, 0.06, 0.65),    "size": (0.45, 0.45), "energy": 2.5,  "color": "#FFFFFF"},
        "sweep": {"loc": (0.0, -0.35, 0.0),    "size": (0.03, 1.20), "energy": 0.0,  "color": "#FFFFFF"},
    },
    "floor_gloss": 0.35,
    "floor_roughness": 0.04,

    # ---- floating fruit props (positions are in front-camera space) --------
    "props": {
        "orange": {"kind": "orange_slice", "size": 0.034, "seed": 3, "items": [
            (-0.13, 0.26, 0.09), (0.14, 0.30, -0.03), (-0.09, 0.36, -0.13),
            (0.10, 0.22, 0.14), (0.04, 0.48, -0.21), (-0.21, 0.42, 0.01), (0.22, 0.45, 0.17)]},
        "passion": {"kind": "passion_half", "size": 0.03, "seed": 5, "items": [
            (-0.14, 0.24, 0.12), (0.15, 0.28, -0.06), (-0.10, 0.34, -0.15),
            (0.12, 0.30, 0.17), (-0.24, 0.45, 0.00), (0.26, 0.50, 0.08)]},
        "seabuckthorn": {"kind": "berry_cluster", "size": 0.0068, "seed": 9, "items": [
            (-0.11, 0.20, 0.10), (0.12, 0.26, -0.04), (-0.08, 0.30, -0.13),
            (0.10, 0.24, 0.14), (-0.20, 0.40, 0.03), (0.21, 0.42, 0.10), (0.02, 0.38, -0.22)]},
    },

    # ---- storyboard -----------------------------------------------------------
    # Times are seconds. Key times inside a shot are relative to the shot start.
    # cam keys:   (t, camera_xyz, target_xyz, lens_mm, f_stop)
    # orbit keys: (t, azimuth_deg, distance, height, target_z, lens_mm, f_stop)
    # bottle keys: (t, location_xyz, rotation_deg_xyz)   rotation order Z->Y->X
    # level keys: (t, value) for lights / glow / env
    "shots": [
        {"name": "hook_macro", "start": 0.0, "end": 1.5, "bg": "orange", "drift": 0.0008,
         "bottles": {"orange": [(0.0, (0, 0, 0), (0, 0, -7)), (1.5, (0, 0, 0.002), (0, 0, 5))]},
         "cam": [(0.0, (-0.020, -0.140, 0.006), (-0.012, -0.031, 0.004), 100, 2.8),
                 (1.5, (0.014, -0.112, 0.000), (0.008, -0.031, 0.001), 100, 2.8)]},
        {"name": "silhouette", "start": 1.5, "end": 2.5, "bg": "silhouette", "floor": True,
         "levels": {"key": [(0, 0)], "fill": [(0, 0)], "top": [(0, 0.15)], "rim": [(0, 3.2)],
                    "glow": [(0, 0)], "env": [(0, 0.12)]},
         "bottles": {"orange": [(0.0, (0, 0, 0.105), (0, 0, -9)), (1.0, (0, 0, 0.105), (0, 0, 7))]},
         "cam": [(0.0, (0.0, -0.64, 0.045), (0, 0, 0.10), 60, 8),
                 (1.0, (0.0, -0.57, 0.055), (0, 0, 0.10), 60, 8)]},
        {"name": "tumble", "start": 2.5, "end": 3.5, "bg": "orange", "motion_blur": True,
         "interp": "LINEAR", "props": "orange",
         "bottles": {"orange": [(-0.1, (0.16, 2.0, 0.30), (-60, 20, -40)),
                                (0.5, (0.06, 0.55, 0.12), (260, 70, 140)),
                                (1.0, (-0.02, -0.44, 0.0), (560, 110, 330)),
                                (1.1, (-0.03, -0.55, -0.01), (600, 115, 350))]},
         "cam": [(0.0, (0.0, -0.80, 0.02), (0, 0, 0.03), 35, 11),
                 (1.0, (0.0, -0.78, 0.02), (0, 0, 0.02), 35, 11)]},
        {"name": "orange_hero", "start": 3.5, "end": 5.0, "bg": "orange", "props": "orange", "float": True,
         "bottles": {"orange": [(0.0, (0, 0, 0), (0, 0, -24)), (1.5, (0, 0, 0.004), (0, 0, 16))]},
         "cam": [(0.0, (0.03, -0.64, 0.045), (0, 0, 0.012), 85, 4.0),
                 (1.5, (-0.01, -0.54, 0.03), (0, 0, 0.018), 85, 4.0)]},
        {"name": "orange_base", "start": 5.0, "end": 6.0, "bg": "orange", "props": "orange", "float": True,
         "bottles": {"orange": [(0.0, (0, 0, 0.02), (-16, 0, 12)), (1.0, (0, 0, 0.026), (-14, 0, -10))]},
         "cam": [(0.0, (0.17, -0.25, -0.25), (0, 0, -0.06), 35, 5.6),
                 (1.0, (-0.16, -0.23, -0.22), (0, 0, -0.06), 35, 5.6)]},
        {"name": "orange_cap", "start": 6.0, "end": 7.0, "bg": "orange", "props": "orange", "float": True,
         "bottles": {"orange": [(0.0, (0, 0, 0), (0, 0, -12)), (1.0, (0, 0, 0.003), (0, 0, 16))]},
         "cam": [(0.0, (0.065, -0.17, 0.135), (0, 0, 0.092), 85, 2.8),
                 (1.0, (-0.035, -0.15, 0.128), (0, 0, 0.093), 85, 2.8)]},
        {"name": "passion_wide", "start": 7.0, "end": 8.5, "bg": "passion", "props": "passion", "float": True,
         "bottles": {"passion": [(0.0, (0, 0, 0), (0, 20, 38)), (1.5, (0, 0, 0.004), (0, 20, 14))]},
         "cam": [(0.0, (0.0, -1.20, 0.08), (0, 0, 0.0), 50, 5.6),
                 (1.5, (0.0, -0.95, 0.06), (0, 0, 0.0), 50, 5.6)]},
        {"name": "passion_close", "start": 8.5, "end": 10.5, "bg": "passion", "props": "passion", "float": True,
         "bottles": {"passion": [(0.0, (0, 0, 0), (0, 20, 10)), (2.0, (0, 0, 0.004), (0, 20, -26))]},
         "cam": [(0.0, (0.03, -0.52, 0.03), (0, 0, 0.008), 85, 3.2),
                 (2.0, (-0.02, -0.42, 0.02), (0, 0, 0.012), 85, 3.2)]},
        {"name": "seabuck_orbit", "start": 10.5, "end": 12.5, "bg": "seabuckthorn", "props": "seabuckthorn",
         "float": True,
         "bottles": {"seabuckthorn": [(0.0, (0, 0, 0), (0, 0, 4)), (2.0, (0, 0, 0.004), (0, 0, -6))]},
         "orbit": [(0.0, -42, 0.46, 0.03, 0.012, 85, 3.5), (2.0, 12, 0.42, 0.015, 0.012, 85, 3.5)]},
        {"name": "seabuck_pull", "start": 12.5, "end": 14.0, "bg": "seabuckthorn", "props": "seabuckthorn",
         "float": True,
         "bottles": {"seabuckthorn": [(0.0, (0, 0, 0.004), (0, 0, -6)), (1.5, (0, 0, 0.0), (0, 0, -14))]},
         "orbit": [(0.0, 12, 0.46, 0.02, 0.012, 70, 4.5), (1.5, 18, 0.85, 0.05, 0.0, 70, 5.6)]},
        {"name": "reveal", "start": 14.0, "end": 15.5, "bg": "silhouette", "floor": True,
         "levels": {"key": [(0.35, 0), (1.15, 1)], "fill": [(0.35, 0), (1.15, 1)], "top": [(0.35, 0.15), (1.15, 1)],
                    "rim": [(0.35, 3.2), (1.15, 1.2)], "glow": [(0.35, 0), (1.15, 1)],
                    "env": [(0.35, 0.12), (1.15, 0.55)]},
         "sweep": [(0.15, (-0.45, -0.28, 0.0), 0), (0.3, (-0.30, -0.28, 0.0), 40),
                   (1.1, (0.30, -0.28, 0.0), 40), (1.3, (0.45, -0.28, 0.0), 0)],
         "bottles": {"orange": [(0.0, (0, 0, 0.105), (0, 0, -10)), (1.5, (0, 0, 0.105), (0, 0, 8))]},
         "cam": [(0.0, (0.0, -0.62, 0.06), (0, 0, 0.10), 60, 8),
                 (1.5, (0.0, -0.55, 0.07), (0, 0, 0.10), 60, 8)]},
        {"name": "lineup", "start": 15.5, "end": 18.0, "bg": "final", "bg_from": "final_start", "bg_blend": 1.6,
         "floor": True,
         "sweep": [(0.4, (-0.50, -0.32, 0.03), 0), (0.7, (-0.36, -0.32, 0.03), 12),
                   (2.1, (0.36, -0.32, 0.03), 12), (2.4, (0.50, -0.32, 0.03), 0)],
         "bottles": {"orange":       [(0.0, (-0.074, 0.02, 0.105), (0, 0, 14)), (2.5, (-0.074, 0.02, 0.105), (0, 0, 8))],
                     "passion":      [(0.0, (0.0, -0.03, 0.105), (0, 0, -5)), (2.5, (0.0, -0.03, 0.105), (0, 0, 4))],
                     "seabuckthorn": [(0.0, (0.074, 0.02, 0.105), (0, 0, -14)), (2.5, (0.074, 0.02, 0.105), (0, 0, -8))]},
         "cam": [(0.0, (0.0, -0.74, 0.13), (0, 0, 0.092), 50, 6.3),
                 (2.5, (0.0, -0.56, 0.115), (0, 0, 0.096), 50, 6.3)]},
    ],
    "stills": [("orange", 4.6), ("passion", 9.8), ("seabuckthorn", 11.6), ("lineup", 17.4)],
}


# =============================================================================
# helpers
# =============================================================================
def P(*parts):
    return os.path.join(ROOT, *parts)


def hex_lin(h, alpha=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return (*lin, alpha)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    opts = {"mode": "stills", "shots": None, "frames": None, "skip": True, "encode": True,
            "only": None, "samples": None, "scale": 100}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--mode":
            opts["mode"] = argv[i + 1]; i += 1
        elif a == "--shots":
            opts["shots"] = argv[i + 1].split(","); i += 1
        elif a == "--frames":
            opts["frames"] = [int(x) for x in argv[i + 1].split(",")]; i += 1
        elif a == "--only":
            opts["only"] = argv[i + 1].split(","); i += 1
        elif a == "--samples":
            opts["samples"] = int(argv[i + 1]); i += 1
        elif a == "--scale":
            opts["scale"] = int(argv[i + 1]); i += 1
        elif a == "--no-skip":
            opts["skip"] = False
        elif a == "--no-encode":
            opts["encode"] = False
        i += 1
    return opts


# ---- image I/O through Blender (no PIL needed inside Blender) ---------------
def load_rgba(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[::-1].copy()        # top-down rows


def save_rgba(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(arr[::-1]).astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def largest_component(mask):
    """Keep the blob connected to the image centre (drops stray specks)."""
    k = 4
    h, w = mask.shape
    small = mask[: h // k * k, : w // k * k].reshape(h // k, k, w // k, k).mean((1, 3)) > 0.5
    seed = np.zeros_like(small)
    ys, xs = np.nonzero(small)
    cy, cx = int(np.median(ys)), int(np.median(xs))
    seed[cy, cx] = True
    while True:
        g = seed.copy()
        g[1:] |= seed[:-1]; g[:-1] |= seed[1:]; g[:, 1:] |= seed[:, :-1]; g[:, :-1] |= seed[:, 1:]
        g &= small
        if (g == seed).all():
            break
        seed = g
    for _ in range(2):                              # dilate to cover edge pixels
        g = seed.copy()
        g[1:] |= seed[:-1]; g[:-1] |= seed[1:]; g[:, 1:] |= seed[:, :-1]; g[:, :-1] |= seed[:, 1:]
        seed = g
    big = np.zeros_like(mask)
    big[: h // k * k, : w // k * k] = np.repeat(np.repeat(seed, k, 0), k, 1)
    return mask & big


# =============================================================================
# 1. asset preparation: clean alpha, trace profile, back-side gradient
# =============================================================================
def prepare_variant(name):
    cfg = CONFIG["variants"][name]
    os.makedirs(P("build"), exist_ok=True)
    rgba = load_rgba(P(cfg["image"]))
    alpha = rgba[..., 3]
    mask = largest_component(alpha > 0.5)
    clean = rgba.copy()
    clean[..., 3] = np.where(mask, alpha, 0.0)
    clean_path = P("build", f"{name}_clean.png")
    save_rgba(clean, clean_path)

    rows = np.nonzero(mask.any(1))[0]
    y_top, y_bot = rows.min(), rows.max()
    hpx = y_bot - y_top
    left = np.full(mask.shape[0], np.nan)
    right = np.full(mask.shape[0], np.nan)
    for y in rows:
        xs = np.nonzero(mask[y])[0]
        left[y], right[y] = xs.min(), xs.max()
    body = slice(y_top + int(0.2 * hpx), y_top + int(0.9 * hpx))
    cx = float(np.nanmedian((left[body] + right[body]) / 2))
    radius_px = (right - left + 1) / 2

    # back-side colour: median of the side columns of every row, blurred
    back = np.zeros((mask.shape[0], 3), np.float32)
    for y in rows:
        wdt = right[y] - left[y]
        cols = np.r_[int(left[y] + 0.12 * wdt):int(left[y] + 0.26 * wdt),
                     int(right[y] - 0.26 * wdt):int(right[y] - 0.12 * wdt)]
        if len(cols):
            back[y] = np.median(rgba[y, cols, :3], axis=0)
    back[:y_top] = back[y_top]
    back[y_bot:] = back[y_bot]
    kern = np.ones(61) / 61
    for c in range(3):
        back[:, c] = np.convolve(np.pad(back[:, c], 30, mode="edge"), kern, "valid")
    back_img = np.ones((mask.shape[0], 4, 4), np.float32)
    back_img[..., :3] = back[:, None, :]
    back_path = P("build", f"{name}_back.png")
    save_rgba(back_img, back_path)

    s = CONFIG["bottle_height"] / hpx                       # metres per pixel

    def r_at(t):
        """radius (m) at fraction t from the top, smoothed over a few rows"""
        y = y_top + t * hpx
        y0, y1 = int(max(y_top, y - 3)), int(min(y_bot, y + 3)) + 1
        return float(np.nanmean(radius_px[y0:y1])) * s

    return {"name": name, "clean": clean_path, "back": back_path, "s": s,
            "W": mask.shape[1], "H": mask.shape[0], "cx": cx, "y_top": y_top, "hpx": hpx, "r_at": r_at}


# =============================================================================
# 2. geometry
# =============================================================================
def z_of(t):
    hb = CONFIG["bottle_height"]
    return hb / 2 - t * hb


def lathe(name, prof, steps):
    """Revolve an (r, z) profile with a Screw modifier and return the mesh."""
    me = bpy.data.meshes.new(name + "_profile")
    me.from_pydata([(r, 0.0, z) for r, z in prof], [(i, i + 1) for i in range(len(prof) - 1)], [])
    ob = bpy.data.objects.new(name + "_profile", me)
    bpy.context.scene.collection.objects.link(ob)
    m = ob.modifiers.new("Lathe", "SCREW")
    m.axis = "Z"
    m.angle = 2 * math.pi
    m.steps = steps
    m.render_steps = steps
    m.use_merge_vertices = True
    m.merge_threshold = 1e-6
    m.use_normal_calculate = True
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    mesh.name = name
    bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(me)
    return mesh


def orient_outward(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    score = 0.0
    for f in bm.faces:
        c = f.calc_center_median()
        score += f.normal.x * c.x + f.normal.y * c.y
    if score < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()


def deform_bottle(mesh, inset=0.0):
    """Petaloid feet and shoulder grip panels (applied to shell and juice alike)."""
    n = len(mesh.vertices)
    co = np.empty(n * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    hb = CONFIG["bottle_height"]
    x, y, z = co[:, 0], co[:, 1], co[:, 2]
    th = np.arctan2(y, x)
    r = np.hypot(x, y)
    t = (hb / 2 - z) / hb
    # petaloid base: valleys between the feet are pushed in and up
    feet = CONFIG["petaloid_feet"]
    v = (0.5 + 0.5 * np.cos(feet * th)) ** 1.5
    f = smoothstep(0.90, 1.0, t)
    r = r * (1 - CONFIG["petaloid_depth"] * (1 - v) * f)
    z = z + 0.0045 * (1 - v) * f ** 2
    # shallow vertical grip panels on the shoulder
    pan = smoothstep(0.55, 0.8, np.cos(CONFIG["grip_panels"] * th))
    band = smoothstep(0.17, 0.20, t) * (1 - smoothstep(0.26, 0.29, t))
    r = r * (1 - CONFIG["grip_depth"] * pan * band)
    co[:, 0] = r * np.cos(th)
    co[:, 1] = r * np.sin(th)
    co[:, 2] = z
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()


def bottle_profiles(info):
    r_at = info["r_at"]
    hb = CONFIG["bottle_height"]
    t_cap, t_fill = CONFIG["cap_bottom_t"], CONFIG["fill_line_t"]
    r_neck = r_at(CONFIG["neck_t"])
    ts = np.linspace(t_cap - 0.004, 0.985, 300)
    shell = [(r_neck * 0.98, z_of(0.03))] + [(r_at(t), z_of(t)) for t in ts]
    r_end = r_at(0.985)
    zb = -hb / 2
    for k in np.linspace(0, 1, 8)[1:]:          # rounded heel down to the floor
        a = k * math.pi / 2
        shell.append((r_end - 0.006 * (1 - math.cos(a)) * 1.0, z_of(0.985) - (z_of(0.985) - zb) * math.sin(a)))
    for k in np.linspace(0, 1, 10)[1:]:         # push-up dome
        shell.append(((shell[-1][0]) * (1 - k) + 1e-5 * k, zb + 0.009 * math.sin(k * math.pi / 2)))
    shell[-1] = (0.0, zb + 0.009)

    ins = CONFIG["juice_inset"]
    juice = [(0.0, z_of(t_fill))]
    for t in np.linspace(t_fill, 0.985, 260):
        juice.append((max(r_at(t) - ins, 0.0), z_of(t)))
    for rr, zz in shell[len(ts) + 1:]:
        juice.append((max(rr - ins * 1.2, 0.0), zz + ins))
    juice[-1] = (0.0, juice[-1][1])
    juice.insert(1, (juice[1][0] * 0.96, z_of(t_fill) + 0.0006))    # small meniscus

    cap = [(0.0, z_of(0.0))]
    rc = r_at(0.045)
    for k in np.linspace(0, 1, 7):              # rounded top edge
        a = k * math.pi / 2
        cap.append((rc * 0.93 + rc * 0.07 * math.sin(a), z_of(0.0) - 0.0022 * (1 - math.cos(a))))
    for t in np.linspace(0.012, t_cap, 60):
        cap.append((r_at(t), z_of(t)))
    cap.append((r_neck * 1.02, z_of(t_cap)))
    cap.append((r_neck * 1.02, z_of(0.02)))
    return shell, juice, cap


def knurl_cap(mesh):
    n = len(mesh.vertices)
    co = np.empty(n * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    hb = CONFIG["bottle_height"]
    t = (hb / 2 - co[:, 2]) / hb
    th = np.arctan2(co[:, 1], co[:, 0])
    rib = np.tanh(3.0 * np.sin(CONFIG["cap_ribs"] * th))
    band = smoothstep(0.010, 0.016, t) * (1 - smoothstep(0.058, 0.062, t))
    k = 1 + CONFIG["cap_rib_depth"] * rib * band
    outer = np.hypot(co[:, 0], co[:, 1]) > 0.0
    co[outer, 0] *= k[outer]
    co[outer, 1] *= k[outer]
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()


def make_droplets(name, shell_mesh, seed):
    cfg = CONFIG["droplets"]
    rng = np.random.default_rng(seed)
    shell_mesh.calc_loop_triangles()
    nt = len(shell_mesh.loop_triangles)
    vco = np.empty(len(shell_mesh.vertices) * 3, np.float32)
    shell_mesh.vertices.foreach_get("co", vco)
    vco = vco.reshape(-1, 3)
    tri = np.empty(nt * 3, np.int32)
    shell_mesh.loop_triangles.foreach_get("vertices", tri)
    tri = tri.reshape(-1, 3)
    a, b, c = vco[tri[:, 0]], vco[tri[:, 1]], vco[tri[:, 2]]
    cr = np.cross(b - a, c - a)
    area = np.linalg.norm(cr, axis=1) / 2
    nrm = cr / np.maximum(np.linalg.norm(cr, axis=1, keepdims=True), 1e-12)
    cen = (a + b + c) / 3
    hb = CONFIG["bottle_height"]
    t = (hb / 2 - cen[:, 2]) / hb
    radial = np.hypot(cen[:, 0], cen[:, 1])
    ok = (t > cfg["t_range"][0]) & (t < cfg["t_range"][1]) & (nrm[:, 0] * cen[:, 0] + nrm[:, 1] * cen[:, 1] > 0) \
        & (radial > 0.015)
    w = area * ok
    pick = rng.choice(nt, size=cfg["count"], p=w / w.sum())
    u, v = rng.random(cfg["count"]), rng.random(cfg["count"])
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    pts = a[pick] + u[:, None] * (b[pick] - a[pick]) + v[:, None] * (c[pick] - a[pick])
    nn = nrm[pick]
    rad = np.clip(rng.lognormal(math.log(cfg["r_mean"]), cfg["r_sigma"], cfg["count"]), 0.00015, cfg["r_max"])

    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    tv = np.array([vv.co[:] for vv in bm.verts], np.float32)
    tf = [[vv.index for vv in f.verts] for f in bm.faces]
    bm.free()
    verts, faces = [], []
    up = np.array([0, 0, 1.0])
    for i in range(cfg["count"]):
        zax = nn[i]
        xax = np.cross(up, zax)
        if np.linalg.norm(xax) < 1e-6:
            xax = np.array([1.0, 0, 0])
        xax /= np.linalg.norm(xax)
        yax = np.cross(zax, xax)
        # drops sag a little under gravity: elongate along -yax (down the bottle)
        sx, sy, sz = rad[i], rad[i] * 1.12, rad[i] * cfg["flatten"]
        local = tv * np.array([sx, sy, sz])
        world = pts[i] + local[:, 0:1] * xax + local[:, 1:2] * yax + local[:, 2:3] * zax + zax * sz * 0.2
        base = len(verts)
        verts.extend(world.tolist())
        faces.extend([[base + j for j in f] for f in tf])
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.shade_smooth()
    return me


# =============================================================================
# 3. materials
# =============================================================================
class NB:
    """Tiny node-builder to keep the material code readable."""

    def __init__(self, mat):
        mat.use_nodes = True
        self.nt = mat.node_tree
        self.nt.nodes.clear()

    def n(self, kind, **props):
        node = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(node, k, v)
        return node

    def link(self, a, b):
        self.nt.links.new(a, b)

    def math(self, op, a, b=None, clamp=False):
        m = self.n("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                self.link(v, m.inputs[i])
        return m.outputs[0]

    def mix_rgb(self, fac, a, b, blend="MIX"):
        m = self.n("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        cols = [s for s in m.inputs if s.type == "RGBA"]
        for sock, v in ((m.inputs[0], fac), (cols[0], a), (cols[1], b)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            elif isinstance(v, tuple):
                sock.default_value = v
            else:
                self.link(v, sock)
        return [o for o in m.outputs if o.type == "RGBA"][0]

    def maprange(self, v, a, b, smooth=True):
        m = self.n("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP" if smooth else "LINEAR")
        self.link(v, m.inputs[0])
        m.inputs[1].default_value = a
        m.inputs[2].default_value = b
        return m.outputs[0]

    def value(self, v, name):
        node = self.n("ShaderNodeValue", name=name, label=name)
        node.outputs[0].default_value = v
        return node

    def rgb(self, c, name):
        node = self.n("ShaderNodeRGB", name=name, label=name)
        node.outputs[0].default_value = c
        return node

    def out(self, shader):
        o = self.n("ShaderNodeOutputMaterial")
        self.link(shader, o.inputs["Surface"])


def principled(nb, **inputs):
    p = nb.n("ShaderNodeBsdfPrincipled")
    for k, v in inputs.items():
        if hasattr(v, "is_output"):
            nb.link(v, p.inputs[k])
        else:
            p.inputs[k].default_value = v
    return p


def shadow_transparent(nb, shader):
    """Clear plastics/water must not block light reaching the juice inside."""
    lp = nb.n("ShaderNodeLightPath")
    fac = nb.math("MAXIMUM", lp.outputs["Is Shadow Ray"], lp.outputs["Is Diffuse Ray"])
    tr = nb.n("ShaderNodeBsdfTransparent")
    mx = nb.n("ShaderNodeMixShader")
    nb.link(fac, mx.inputs[0])
    nb.link(shader, mx.inputs[1])
    nb.link(tr.outputs[0], mx.inputs[2])
    return mx.outputs[0]


def mat_pet():
    mat = bpy.data.materials.new("PET")
    nb = NB(mat)
    tc = nb.n("ShaderNodeTexCoord")
    vor = nb.n("ShaderNodeTexVoronoi", feature="SMOOTH_F1")
    vor.inputs["Scale"].default_value = CONFIG["condensation_scale"]
    vor.inputs["Smoothness"].default_value = 0.6
    nb.link(tc.outputs["Object"], vor.inputs["Vector"])
    patch = nb.n("ShaderNodeTexNoise")
    patch.inputs["Scale"].default_value = 18.0
    nb.link(tc.outputs["Object"], patch.inputs["Vector"])
    h = nb.math("MULTIPLY", vor.outputs["Distance"], nb.maprange(patch.outputs["Fac"], 0.4, 0.62))
    bump = nb.n("ShaderNodeBump")
    bump.inputs["Strength"].default_value = CONFIG["condensation_bump"]
    bump.inputs["Distance"].default_value = 0.0002
    nb.link(h, bump.inputs["Height"])
    rough = nb.math("ADD", nb.math("MULTIPLY", nb.maprange(patch.outputs["Fac"], 0.4, 0.62), 0.05), 0.015)
    # thin-wall PET: a fresnel-weighted gloss coat over a clear pass-through
    # (physically right for a 0.4 mm wall and far cheaper/noise-free than refraction)
    gl = nb.n("ShaderNodeBsdfGlossy")
    nb.link(rough, gl.inputs["Roughness"])
    nb.link(bump.outputs[0], gl.inputs["Normal"])
    fr = nb.n("ShaderNodeFresnel")
    fr.inputs["IOR"].default_value = CONFIG["pet_ior"]
    nb.link(bump.outputs[0], fr.inputs["Normal"])
    fac = nb.math("ADD", nb.math("MULTIPLY", fr.outputs[0], CONFIG["pet_reflect"]), CONFIG["pet_base_reflect"],
                  clamp=True)
    tr = nb.n("ShaderNodeBsdfTransparent")
    tr.inputs["Color"].default_value = hex_lin(CONFIG["pet_tint"])
    mx = nb.n("ShaderNodeMixShader")
    nb.link(fac, mx.inputs[0])
    nb.link(tr.outputs[0], mx.inputs[1])
    nb.link(gl.outputs[0], mx.inputs[2])
    nb.out(shadow_transparent(nb, mx.outputs[0]))
    return mat


def mat_water():
    mat = bpy.data.materials.new("Droplet")
    nb = NB(mat)
    p = principled(nb, **{"Base Color": (1, 1, 1, 1), "Roughness": 0.0, "IOR": 1.33,
                          "Transmission Weight": 1.0, "Specular IOR Level": 0.8})
    nb.out(shadow_transparent(nb, p.outputs[0]))
    return mat


def mat_cap():
    mat = bpy.data.materials.new("Cap")
    nb = NB(mat)
    col = hex_lin(CONFIG["cap_color"])
    p = principled(nb, **{"Base Color": col, "Roughness": 0.24, "Coat Weight": 0.55, "Coat Roughness": 0.06,
                          "Subsurface Weight": 0.25, "Subsurface Scale": 0.002,
                          "Subsurface Radius": (1.0, 0.6, 0.15)})
    nb.out(p.outputs[0])
    return mat


def mat_juice(info):
    """Photo projected from the front, matching side gradient on the back."""
    mat = bpy.data.materials.new(f"Juice_{info['name']}")
    nb = NB(mat)
    tc = nb.n("ShaderNodeTexCoord")
    sep = nb.n("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Object"], sep.inputs[0])
    comb = nb.n("ShaderNodeCombineXYZ")
    nb.link(sep.outputs["X"], comb.inputs["X"])
    nb.link(sep.outputs["Z"], comb.inputs["Y"])
    s, W, H = info["s"], info["W"], info["H"]
    shrink = CONFIG["proj_shrink"]
    v0 = 1 - (info["y_top"] + info["hpx"] / 2) / H
    mp = nb.n("ShaderNodeMapping", vector_type="POINT")
    mp.inputs["Location"].default_value = (info["cx"] / W, v0, 0)
    mp.inputs["Scale"].default_value = (shrink / (s * W), 1 / (s * H), 1)
    nb.link(comb.outputs[0], mp.inputs["Vector"])

    photo = nb.n("ShaderNodeTexImage", interpolation="Cubic", extension="EXTEND")
    photo.image = bpy.data.images.load(info["clean"], check_existing=True)
    nb.link(mp.outputs[0], photo.inputs[0])
    back = nb.n("ShaderNodeTexImage", interpolation="Linear", extension="EXTEND")
    back.image = bpy.data.images.load(info["back"], check_existing=True)
    nb.link(mp.outputs[0], back.inputs[0])

    nsep = nb.n("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Normal"], nsep.inputs[0])
    front = nb.maprange(nb.math("MULTIPLY", nsep.outputs["Y"], -1.0), 0.0, 0.42)
    base = nb.mix_rgb(front, back.outputs["Color"], photo.outputs["Color"])

    glow = nb.value(CONFIG["label_glow"], "glow")
    p = principled(nb, **{"Base Color": base, "Roughness": CONFIG["label_rough"], "Specular IOR Level": 0.25,
                          "Emission Color": base, "Emission Strength": glow.outputs[0]})
    nb.out(p.outputs[0])
    return mat


def mat_backdrop():
    mat = bpy.data.materials.new("Backdrop")
    nb = NB(mat)
    bot = nb.rgb((1, 0.5, 0.1, 1), "bg_bottom")
    top = nb.rgb((1, 0.8, 0.3, 1), "bg_top")
    env = nb.value(CONFIG["env_strength"], "env")
    screen, dgrad = _bg_colours(nb, bot, top)
    lp = nb.n("ShaderNodeLightPath")
    direct = nb.math("MAXIMUM", lp.outputs["Is Camera Ray"], lp.outputs["Is Transmission Ray"])
    refl = nb.mix_rgb(env.outputs[0], (0, 0, 0, 1), dgrad)
    col = nb.mix_rgb(direct, refl, screen)
    em = nb.n("ShaderNodeEmission")
    nb.link(col, em.inputs["Color"])
    nb.out(em.outputs[0])
    return mat


def _bg_colours(nb, bot, top):
    tc = nb.n("ShaderNodeTexCoord")
    win = nb.n("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Window"], win.inputs[0])
    vert = nb.maprange(win.outputs["Y"], -0.05, 1.05)
    grad = nb.mix_rgb(vert, bot.outputs[0], top.outputs[0])
    dx = nb.math("MULTIPLY", nb.math("SUBTRACT", win.outputs["X"], 0.5), 0.5625)
    dy = nb.math("SUBTRACT", win.outputs["Y"], 0.56)
    d = nb.math("SQRT", nb.math("ADD", nb.math("MULTIPLY", dx, dx), nb.math("MULTIPLY", dy, dy)))
    halo = nb.math("MULTIPLY", nb.math("SUBTRACT", 1.0, nb.maprange(d, 0.0, 0.6)), CONFIG["bg_glow"])
    lighter = nb.mix_rgb(0.5, grad, (1, 1, 1, 1))
    screen = nb.mix_rgb(halo, grad, lighter)
    nrm = nb.n("ShaderNodeVectorMath", operation="NORMALIZE")
    nb.link(tc.outputs["Object"], nrm.inputs[0])
    ob = nb.n("ShaderNodeSeparateXYZ")
    nb.link(nrm.outputs[0], ob.inputs[0])
    dgrad = nb.mix_rgb(nb.maprange(ob.outputs["Z"], -0.3, 0.7), bot.outputs[0], top.outputs[0])
    return screen, dgrad


def mat_floor():
    mat = bpy.data.materials.new("Floor")
    nb = NB(mat)
    bot = nb.rgb((1, 0.5, 0.1, 1), "bg_bottom")
    top = nb.rgb((1, 0.8, 0.3, 1), "bg_top")
    nb.value(CONFIG["env_strength"], "env")
    screen, _ = _bg_colours(nb, bot, top)
    ao = nb.n("ShaderNodeAmbientOcclusion", samples=8)
    ao.inputs["Distance"].default_value = 0.05
    shade = nb.math("ADD", nb.math("MULTIPLY", ao.outputs["AO"], 0.6), 0.4)
    # darken the gradient with an AO term for soft contact shadows
    mul = nb.n("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
    cols = [s for s in mul.inputs if s.type == "RGBA"]
    mul.inputs[0].default_value = 1.0
    nb.link(screen, cols[0])
    gray = nb.n("ShaderNodeCombineColor")
    for i in range(3):
        nb.link(shade, gray.inputs[i])
    nb.link(gray.outputs[0], cols[1])
    em = nb.n("ShaderNodeEmission")
    nb.link([o for o in mul.outputs if o.type == "RGBA"][0], em.inputs["Color"])
    gl = nb.n("ShaderNodeBsdfGlossy")
    gl.inputs["Roughness"].default_value = CONFIG["floor_roughness"]
    lw = nb.n("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.35
    g = nb.math("ADD", nb.math("MULTIPLY", lw.outputs["Fresnel"], 0.5), CONFIG["floor_gloss"] * 0.6)
    gcol = nb.n("ShaderNodeCombineColor")
    for i in range(3):
        nb.link(g, gcol.inputs[i])
    nb.link(gcol.outputs[0], gl.inputs["Color"])
    add = nb.n("ShaderNodeAddShader")
    nb.link(em.outputs[0], add.inputs[0])
    nb.link(gl.outputs[0], add.inputs[1])
    nb.out(add.outputs[0])
    return mat


def mat_orange_slice():
    mat = bpy.data.materials.new("OrangeSlice")
    nb = NB(mat)
    tc = nb.n("ShaderNodeTexCoord")
    sep = nb.n("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Object"], sep.inputs[0])
    d = nb.math("SQRT", nb.math("ADD", nb.math("MULTIPLY", sep.outputs["X"], sep.outputs["X"]),
                                nb.math("MULTIPLY", sep.outputs["Y"], sep.outputs["Y"])))
    ang = nb.math("ARCTAN2", sep.outputs["Y"], sep.outputs["X"])
    memb = nb.math("ABSOLUTE", nb.math("SINE", nb.math("MULTIPLY", ang, 5.0)))
    m = nb.math("SUBTRACT", 1.0, nb.maprange(memb, 0.02, 0.09))
    inner = nb.math("LESS_THAN", d, 0.84)
    centre = nb.math("SUBTRACT", 1.0, nb.maprange(d, 0.06, 0.12))
    pith_mask = nb.math("MAXIMUM", nb.math("MULTIPLY", m, inner), centre)
    ramp = nb.n("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.elements[0].position = 0.0
    cr.elements[0].color = hex_lin("#FF9A1A")
    cr.elements[1].position = 0.84
    cr.elements[1].color = hex_lin("#FF8A00")
    for pos, c in ((0.86, "#FFE9C2"), (0.92, "#FFDDA0"), (0.94, "#F57C00"), (1.0, "#E86A00")):
        e = cr.elements.new(pos)
        e.color = hex_lin(c)
    nb.link(d, ramp.inputs[0])
    noise = nb.n("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 14.0
    nb.link(tc.outputs["Object"], noise.inputs["Vector"])
    pulp = nb.mix_rgb(nb.math("MULTIPLY", noise.outputs["Fac"], 0.35), ramp.outputs[0], (1.0, 0.75, 0.25, 1))
    col = nb.mix_rgb(pith_mask, pulp, hex_lin("#FFF0D2"))
    p = principled(nb, **{"Base Color": col, "Roughness": 0.25, "Coat Weight": 0.5,
                          "Subsurface Weight": 0.4, "Subsurface Scale": 0.004,
                          "Subsurface Radius": (1.0, 0.5, 0.15)})
    nb.out(p.outputs[0])
    return mat


def mat_passion_skin():
    mat = bpy.data.materials.new("PassionSkin")
    nb = NB(mat)
    tc = nb.n("ShaderNodeTexCoord")
    noise = nb.n("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    noise.inputs["Detail"].default_value = 6.0
    nb.link(tc.outputs["Object"], noise.inputs["Vector"])
    col = nb.mix_rgb(nb.maprange(noise.outputs["Fac"], 0.35, 0.65), hex_lin("#3E0C36"), hex_lin("#8A2468"))
    p = principled(nb, **{"Base Color": col, "Roughness": 0.4, "Coat Weight": 0.3})
    nb.out(p.outputs[0])
    return mat


def mat_passion_pulp():
    mat = bpy.data.materials.new("PassionPulp")
    nb = NB(mat)
    tc = nb.n("ShaderNodeTexCoord")
    sep = nb.n("ShaderNodeSeparateXYZ")
    nb.link(tc.outputs["Object"], sep.inputs[0])
    d = nb.math("SQRT", nb.math("ADD", nb.math("MULTIPLY", sep.outputs["X"], sep.outputs["X"]),
                                nb.math("MULTIPLY", sep.outputs["Y"], sep.outputs["Y"])))
    vor = nb.n("ShaderNodeTexVoronoi")
    vor.inputs["Scale"].default_value = 9.0
    nb.link(tc.outputs["Object"], vor.inputs["Vector"])
    seed = nb.math("SUBTRACT", 1.0, nb.maprange(vor.outputs["Distance"], 0.12, 0.2))
    pulp = nb.mix_rgb(seed, hex_lin("#FFB01F"), hex_lin("#2B1606"))
    col = nb.mix_rgb(nb.maprange(d, 0.84, 0.9), pulp, hex_lin("#F3E3BE"))
    p = principled(nb, **{"Base Color": col, "Roughness": 0.15, "Coat Weight": 0.8,
                          "Subsurface Weight": 0.3, "Subsurface Scale": 0.003})
    nb.out(p.outputs[0])
    return mat


def mat_berry():
    mat = bpy.data.materials.new("Berry")
    nb = NB(mat)
    p = principled(nb, **{"Base Color": hex_lin("#FF8A12"), "Roughness": 0.18, "Coat Weight": 0.6,
                          "Subsurface Weight": 0.6, "Subsurface Scale": 0.003,
                          "Subsurface Radius": (1.0, 0.45, 0.1)})
    nb.out(p.outputs[0])
    return mat


# =============================================================================
# 4. scene construction
# =============================================================================
def new_obj(name, data, parent=None, coll=None):
    ob = bpy.data.objects.new(name, data)
    (coll or bpy.context.scene.collection).objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def build_bottle(info, mats):
    name = info["name"]
    root = new_obj(f"Bottle_{name}", None)
    root.empty_display_size = 0.05
    root.rotation_mode = "ZYX"                       # spin about the bottle axis first, then tilt
    shell_p, juice_p, cap_p = bottle_profiles(info)

    shell = lathe(f"{name}_shell", shell_p, CONFIG["lathe_steps"])
    orient_outward(shell)
    deform_bottle(shell)
    new_obj(f"{name}_PET", shell, root)
    shell.materials.append(mats["pet"])

    juice = lathe(f"{name}_juice", juice_p, CONFIG["lathe_steps"])
    orient_outward(juice)
    deform_bottle(juice)
    juice.materials.append(mat_juice(info))
    new_obj(f"{name}_Juice", juice, root)

    cap = lathe(f"{name}_cap", cap_p, CONFIG["cap_steps"])
    orient_outward(cap)
    knurl_cap(cap)
    cap.materials.append(mats["cap"])
    new_obj(f"{name}_Cap", cap, root)

    drops = make_droplets(f"{name}_drops", shell, CONFIG["droplets"]["seed"] + len(name))
    drops.materials.append(mats["water"])
    new_obj(f"{name}_Droplets", drops, root)
    return root


def mesh_orange_slice():
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=64, radius1=1.0, radius2=1.0, depth=0.22)
    me = bpy.data.meshes.new("orange_slice")
    bm.to_mesh(me)
    bm.free()
    return me


def mesh_passion_half():
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-5,
                           plane_co=(0, 0, 0), plane_no=(0, 0, 1), clear_outer=True)
    edges = [e for e in bm.edges if e.is_boundary]
    res = bmesh.ops.edgeloop_fill(bm, edges=edges)
    for f in bm.faces:
        f.smooth = True
        f.material_index = 0
    for f in res["faces"]:
        f.material_index = 1
        f.smooth = False
    me = bpy.data.meshes.new("passion_half")
    bm.to_mesh(me)
    bm.free()
    return me


def mesh_berry_cluster(seed):
    rng = random.Random(seed)
    bm = bmesh.new()
    for i in range(rng.randint(4, 7)):
        m = bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0)
        sc = rng.uniform(0.85, 1.1)
        off = (rng.uniform(-1.6, 1.6), rng.uniform(-1.6, 1.6), rng.uniform(-1.2, 1.2))
        for v in m["verts"]:
            v.co.x = v.co.x * sc + off[0]
            v.co.y = v.co.y * sc + off[1]
            v.co.z = v.co.z * sc * 1.18 + off[2]
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new("berries")
    bm.to_mesh(me)
    bm.free()
    return me


def build_props(variant, mats):
    cfg = CONFIG["props"][variant]
    rng = random.Random(cfg["seed"])
    objs = []
    for i, loc in enumerate(cfg["items"]):
        if cfg["kind"] == "orange_slice":
            me = mesh_orange_slice()
            me.materials.append(mats["orange_slice"])
        elif cfg["kind"] == "passion_half":
            me = mesh_passion_half()
            me.materials.append(mats["passion_skin"])
            me.materials.append(mats["passion_pulp"])
        else:
            me = mesh_berry_cluster(cfg["seed"] * 31 + i)
            me.materials.append(mats["berry"])
        ob = new_obj(f"prop_{variant}_{i}", me)
        ob.scale = (cfg["size"] * rng.uniform(0.85, 1.15),) * 3
        ob["base_loc"] = loc
        ob["base_rot"] = (rng.uniform(-1.2, 1.2) + 1.2, rng.uniform(-0.8, 0.8), rng.uniform(0, 6.28))
        ob["spin"] = (rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(-0.6, 0.6))
        objs.append(ob)
    return objs


def build_scene(variants):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = "OPENIMAGEDENOISE"
    sc.cycles.max_bounces = 8
    sc.cycles.diffuse_bounces = 1
    sc.cycles.glossy_bounces = 3
    sc.cycles.transmission_bounces = 6
    sc.cycles.transparent_max_bounces = 10
    sc.cycles.volume_bounces = 0
    sc.cycles.caustics_reflective = False
    sc.cycles.caustics_refractive = False
    sc.cycles.sample_clamp_indirect = 4.0
    sc.cycles.blur_glossy = 1.0
    sc.render.use_persistent_data = True
    sc.render.fps = CONFIG["fps"]
    sc.view_settings.view_transform = CONFIG["view_transform"]
    try:
        sc.view_settings.look = CONFIG["look"]
    except TypeError:
        pass
    sc.view_settings.exposure = CONFIG["exposure"]
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.image_settings.compression = 15
    sc.render.motion_blur_shutter = CONFIG["motion_blur_shutter"]

    mats = {"pet": mat_pet(), "water": mat_water(), "cap": mat_cap(),
            "backdrop": mat_backdrop(), "floor": mat_floor(),
            "orange_slice": mat_orange_slice(), "passion_skin": mat_passion_skin(),
            "passion_pulp": mat_passion_pulp(), "berry": mat_berry()}

    # cyclorama: a huge inward-facing sphere + floor plane sharing one gradient
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=30.0)
    me = bpy.data.meshes.new("cyc")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mats["backdrop"])
    cyc = new_obj("Cyclorama", me)
    cyc.visible_shadow = False
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=25.0)
    me = bpy.data.meshes.new("floor")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mats["floor"])
    floor = new_obj("Floor", me)

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.0
    sc.world = world

    # light rig
    rig = new_obj("LightRig", None)
    tgt = new_obj("LightTarget", None, rig)
    lights = {}
    for key, lc in CONFIG["lights"].items():
        ld = bpy.data.lights.new(key, "AREA")
        ld.shape = "RECTANGLE"
        ld.size, ld.size_y = lc["size"]
        ld.energy = lc["energy"]
        ld.color = hex_lin(lc["color"])[:3]
        ob = new_obj(f"L_{key}", ld, rig)
        ob.location = lc["loc"]
        con = ob.constraints.new("TRACK_TO")
        con.target = tgt
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
        lights[key] = ob

    cam_d = bpy.data.cameras.new("Camera")
    cam_d.sensor_fit = "VERTICAL"
    cam_d.sensor_height = 36.0
    cam_d.clip_start = 0.005
    cam_d.clip_end = 100
    cam = new_obj("Camera", cam_d)
    aim = new_obj("CamTarget", None)
    con = cam.constraints.new("TRACK_TO")
    con.target = aim
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    cam_d.dof.use_dof = True
    cam_d.dof.focus_object = aim
    sc.camera = cam

    infos = {v: prepare_variant(v) for v in variants}
    bottles = {v: build_bottle(infos[v], mats) for v in variants}
    props = {v: build_props(v, mats) for v in variants if v in CONFIG["props"]}
    return {"scene": sc, "mats": mats, "cyc": cyc, "floor": floor, "rig": rig, "lights": lights,
            "cam": cam, "aim": aim, "bottles": bottles, "props": props}


# =============================================================================
# 5. animation
# =============================================================================
def fcurves(idblock):
    ad = idblock.animation_data
    if not ad or not ad.action:
        return []
    act = ad.action
    try:
        return list(act.fcurves)
    except AttributeError:
        out = []
        for layer in act.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out.extend(cb.fcurves)
        return out


def set_interp(idblock, interp):
    for fc in fcurves(idblock):
        for kp in fc.keyframe_points:
            kp.interpolation = interp


def add_noise(ob, path, index, strength, scale, phase):
    for fc in fcurves(ob):
        if fc.data_path == path and fc.array_index == index:
            m = fc.modifiers.new("NOISE")
            m.blend_type = "ADD"
            m.strength = strength
            m.scale = scale
            m.phase = phase
            return


def frame_of(t):
    return int(round(t * CONFIG["fps"]))


def walk(ob):
    yield ob
    for c in ob.children:
        yield from walk(c)


def set_visible(ob, vis):
    for o in walk(ob):
        o.hide_render = not vis
        o.hide_viewport = not vis


def set_socket(mat, node_name, value, frame=None):
    """Set (and optionally keyframe) the value of an RGB/Value node."""
    nodes = mat.node_tree.nodes
    if node_name not in nodes:
        return
    sock = nodes[node_name].outputs[0]
    sock.default_value = value
    if frame is not None:
        sock.keyframe_insert("default_value", frame=frame)


def snap_times():
    """Cut times on the beat: librosa beats from music.mp3 if present, else the BPM grid."""
    music = P("assets", "music.mp3")
    beats = None
    if os.path.exists(music):
        try:
            import librosa
            y, sr = librosa.load(music, sr=None, mono=True)
            _, bf = librosa.beat.beat_track(y=y, sr=sr)
            beats = librosa.frames_to_time(bf, sr=sr)
            print(f"[beats] {len(beats)} beats detected in music.mp3")
        except Exception as e:                                  # noqa: BLE001
            print(f"[beats] librosa unavailable ({e}); using {CONFIG['bpm']} BPM grid")
    if beats is None or len(beats) < 4:
        beats = np.arange(0, CONFIG["duration"] + 1e-6, 60.0 / CONFIG["bpm"])
    cuts = sorted({s["start"] for s in CONFIG["shots"]} | {CONFIG["duration"]})
    snapped = {}
    for c in cuts:
        if c in (0.0, CONFIG["duration"]):
            snapped[c] = c
            continue
        b = beats[np.argmin(np.abs(beats - c))]
        snapped[c] = float(b) if abs(b - c) <= CONFIG["snap_window"] else c
    shots = []
    for s in CONFIG["shots"]:
        s2 = dict(s)
        s2["start"], s2["end"] = snapped[s["start"]], snapped.get(s["end"], s["end"])
        s2["k"] = (s2["end"] - s2["start"]) / (s["end"] - s["start"])   # time-scale for keys
        shots.append(s2)
    return shots


def reset(S):
    for ob in bpy.data.objects:
        ob.animation_data_clear()
    for m in bpy.data.materials:
        if m.node_tree:
            m.node_tree.animation_data_clear()
    for ld in bpy.data.lights:
        ld.animation_data_clear()
    for b in S["bottles"].values():
        set_visible(b, False)
    for objs in S["props"].values():
        for o in objs:
            set_visible(o, False)


def setup_shot(S, shot):
    reset(S)
    sc = S["scene"]
    f0 = frame_of(shot["start"])
    k = shot.get("k", 1.0)

    def F(t):
        return f0 + t * k * CONFIG["fps"]

    floor_on = shot.get("floor", False)
    S["floor"].hide_render = not floor_on
    S["rig"].location = (0, 0, 0.105 if floor_on else 0.0)
    sc.render.use_motion_blur = shot.get("motion_blur", False)

    # background colours
    bg = shot["bg"]
    pair = CONFIG["variants"][bg]["bg"] if bg in CONFIG["variants"] else CONFIG["backgrounds"][bg]
    rim_col = [0.5 * a + 0.5 for a in hex_lin(pair[1])[:3]]
    for m in (S["mats"]["backdrop"], S["mats"]["floor"]):
        if "bg_from" in shot:
            src = CONFIG["backgrounds"][shot["bg_from"]]
            set_socket(m, "bg_bottom", hex_lin(src[0]), F(0))
            set_socket(m, "bg_top", hex_lin(src[1]), F(0))
            set_socket(m, "bg_bottom", hex_lin(pair[0]), F(shot["bg_blend"]))
            set_socket(m, "bg_top", hex_lin(pair[1]), F(shot["bg_blend"]))
        else:
            set_socket(m, "bg_bottom", hex_lin(pair[0]))
            set_socket(m, "bg_top", hex_lin(pair[1]))

    # light / glow / env levels
    lv = shot.get("levels", {})
    for key, ob in S["lights"].items():
        base = CONFIG["lights"][key]["energy"]
        group = "rim" if key.startswith("rim") else key
        if key in ("rim_l", "rim_r"):
            ob.data.color = rim_col
        if key == "sweep":
            continue
        if group in lv:
            for t, val in lv[group]:
                ob.data.energy = base * val
                ob.data.keyframe_insert("energy", frame=F(t))
        else:
            ob.data.energy = base
    for m in S["mats"].values():
        if m.name.startswith("Juice"):
            for t, val in lv.get("glow", [(0, 1.0)]):
                set_socket(m, "glow", CONFIG["label_glow"] * val, F(t) if "glow" in lv else None)
    for m in (S["mats"]["backdrop"], S["mats"]["floor"]):
        for t, val in lv.get("env", [(0, 1.0)]):
            set_socket(m, "env", CONFIG["env_strength"] * val, F(t) if "env" in lv else None)

    sw = S["lights"]["sweep"]
    sw.data.energy = 0.0
    for t, loc, e in shot.get("sweep", []):
        sw.location = loc
        sw.data.energy = e
        sw.keyframe_insert("location", frame=F(t))
        sw.data.keyframe_insert("energy", frame=F(t))

    # bottles
    for name, keys in shot["bottles"].items():
        b = S["bottles"][name]
        set_visible(b, True)
        for t, loc, rot in keys:
            b.location = loc
            b.rotation_euler = [math.radians(a) for a in rot]
            b.keyframe_insert("location", frame=F(t))
            b.keyframe_insert("rotation_euler", frame=F(t))
        if shot.get("interp"):
            set_interp(b, shot["interp"])
        if shot.get("float"):
            add_noise(b, "location", 2, 0.010, 22, 3.0)
            add_noise(b, "location", 0, 0.004, 30, 7.0)
            add_noise(b, "rotation_euler", 0, 0.05, 28, 11.0)
            add_noise(b, "rotation_euler", 1, 0.05, 32, 17.0)

    # floating props
    pv = shot.get("props")
    if pv:
        dur = (shot["end"] - shot["start"])
        for i, o in enumerate(S["props"][pv]):
            set_visible(o, True)
            bl, br, sp = o["base_loc"], o["base_rot"], o["spin"]
            for t in (0.0, dur / k):
                tt = (shot["start"] + t * k)
                o.location = (bl[0] + 0.004 * tt, bl[1], bl[2] + 0.006 * math.sin(tt * 1.3 + i))
                o.rotation_euler = [br[j] + sp[j] * tt for j in range(3)]
                o.keyframe_insert("location", frame=F(t))
                o.keyframe_insert("rotation_euler", frame=F(t))
            set_interp(o, "LINEAR")

    # camera
    cam, aim = S["cam"], S["aim"]
    if "cam" in shot:
        for t, pos, tgt, lens, fs in shot["cam"]:
            cam.location, aim.location = pos, tgt
            cam.data.lens, cam.data.dof.aperture_fstop = lens, fs
            cam.keyframe_insert("location", frame=F(t))
            aim.keyframe_insert("location", frame=F(t))
            cam.data.keyframe_insert("lens", frame=F(t))
            cam.data.keyframe_insert("dof.aperture_fstop", frame=F(t))
    else:
        keys = shot["orbit"]
        t0, t1 = keys[0][0], keys[-1][0]
        n = max(2, int((t1 - t0) * CONFIG["fps"] / 2))
        for i in range(n + 1):
            u = i / n
            e = u * u * (3 - 2 * u)
            vals = [a + (b - a) * e for a, b in zip(keys[0][1:], keys[-1][1:])]
            az, dist, h, tz, lens, fs = vals
            a = math.radians(az)
            cam.location = (dist * math.sin(a), -dist * math.cos(a), h)
            aim.location = (0, 0, tz)
            cam.data.lens, cam.data.dof.aperture_fstop = lens, fs
            t = t0 + (t1 - t0) * u
            cam.keyframe_insert("location", frame=F(t))
            aim.keyframe_insert("location", frame=F(t))
            cam.data.keyframe_insert("lens", frame=F(t))
            cam.data.keyframe_insert("dof.aperture_fstop", frame=F(t))
        set_interp(cam, "LINEAR")
        set_interp(aim, "LINEAR")
    if shot.get("interp") == "LINEAR":
        set_interp(cam, "LINEAR")
    drift = shot.get("drift", 0.004)
    for i in range(3):
        add_noise(cam, "location", i, drift, 35, 5.0 + 13 * i)
    return range(frame_of(shot["start"]), frame_of(shot["end"]))


# =============================================================================
# 6. render + encode
# =============================================================================
def configure_quality(sc, mode):
    sc.render.resolution_x, sc.render.resolution_y = CONFIG["res"][mode]
    sc.render.resolution_percentage = 100
    sc.cycles.samples = CONFIG["samples"][mode]
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.adaptive_threshold = CONFIG["adaptive_threshold"][mode]


def render_frame(sc, f, path):
    sc.frame_set(f)
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def encode(frames_dir, out_path, with_audio):
    fps = CONFIG["fps"]
    music = P("assets", "music.mp3")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
           "-i", os.path.join(frames_dir, "f_%04d.png")]
    if with_audio and os.path.exists(music):
        fade = CONFIG["duration"] - 0.5
        cmd += ["-i", music, "-map", "0:v", "-map", "1:a",
                "-af", f"afade=t=out:st={fade}:d=0.5", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-t", str(CONFIG["duration"]), "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    print("[ffmpeg]", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    opts = parse_args()
    mode = opts["mode"]
    S = build_scene(list(CONFIG["variants"].keys()))
    sc = S["scene"]
    configure_quality(sc, mode)
    sc.render.resolution_percentage = opts["scale"]
    if opts["samples"]:
        sc.cycles.samples = opts["samples"]
    shots = snap_times()
    os.makedirs(P("output"), exist_ok=True)

    if mode == "stills":
        out = P("output", "previews")
        os.makedirs(out, exist_ok=True)
        for name, t in CONFIG["stills"]:
            if opts["only"] and name not in opts["only"]:
                continue
            shot = next(s for s in shots if s["start"] <= t < s["end"])
            setup_shot(S, shot)
            render_frame(sc, frame_of(t), os.path.join(out, f"still_{name}.png"))
        return

    frames_dir = P("build", f"frames_{mode}")
    os.makedirs(frames_dir, exist_ok=True)
    for shot in shots:
        if opts["shots"] and shot["name"] not in opts["shots"]:
            continue
        rng = setup_shot(S, shot)
        for f in rng:
            if opts["frames"] and f not in opts["frames"]:
                continue
            path = os.path.join(frames_dir, f"f_{f:04d}.png")
            if opts["skip"] and os.path.exists(path):
                continue
            print(f"[render] {shot['name']} frame {f}", flush=True)
            render_frame(sc, f, path)

    total = frame_of(CONFIG["duration"])
    have = all(os.path.exists(os.path.join(frames_dir, f"f_{f:04d}.png")) for f in range(total))
    if opts["encode"] and have:
        name = "hivit_ad_final.mp4" if mode == "final" else f"hivit_ad_{mode}.mp4"
        encode(frames_dir, P("output", name), with_audio=(mode == "final"))
        if not os.path.exists(P("assets", "music.mp3")):
            print("[audio] assets/music.mp3 not found - video rendered without audio")
    elif opts["encode"]:
        print("[encode] skipped: not all frames rendered yet")


if __name__ == "__main__":
    main()
