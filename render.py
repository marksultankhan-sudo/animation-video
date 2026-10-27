"""20 saniyelik animasyonlu açıklama videosu: "Güneş Enerjisi Nasıl Çalışır?"

Kareler Pillow ile çizilir, ffmpeg'e ham olarak aktarılır ve numpy ile
sentezlenen arka plan müziğiyle birleştirilir.

Kullanım:  python3 render.py   ->  explainer.mp4
"""
import math
import subprocess
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
S = 2                      # süper örnekleme (kenar yumuşatma için)
FPS = 30
DURATION = 20.0
FRAMES = int(FPS * DURATION)
XFADE = 0.4                # sahneler arası geçiş süresi (sn)
OUT = "explainer.mp4"
AUDIO = "music.wav"

FONT_BOLD = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"
FONT_SEMI = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
FONT_MED = "/usr/share/fonts/opentype/inter/Inter-Medium.otf"

_font_cache = {}


def font(path, size):
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, int(size * S))
    return _font_cache[key]


# ---------------------------------------------------------------- yardımcılar
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    return clamp((t - start) / dur)


def ease_out(x):
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def back_out(x):
    if x <= 0:
        return 0.0
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def lerp(a, b, x):
    return a + (b - a) * x


def mix(c1, c2, x):
    return tuple(int(lerp(a, b, x)) for a, b in zip(c1, c2))


def P(*v):
    return [p * S for p in v]


def rgba(c, a=1.0):
    return (c[0], c[1], c[2], int(255 * clamp(a)))


_grad_cache = {}


def gradient(top, bottom):
    key = (top, bottom)
    if key not in _grad_cache:
        y = np.linspace(0, 1, H * S)[:, None, None]
        arr = np.array(top)[None, None, :] * (1 - y) + np.array(bottom)[None, None, :] * y
        arr = np.repeat(arr, W * S, axis=1).astype(np.uint8)
        _grad_cache[key] = Image.fromarray(arr, "RGB")
    return _grad_cache[key].copy()


def circle(d, x, y, r, fill, outline=None, width=0):
    d.ellipse(P(x - r, y - r, x + r, y + r), fill=fill, outline=outline, width=int(width * S))


def glow(d, x, y, r, color, strength=1.0, steps=10):
    for i in range(steps, 0, -1):
        rr = r * (1 + i * 0.25)
        circle(d, x, y, rr, rgba(color, 0.05 * strength))


def text(d, x, y, s, f, color, alpha=1.0, anchor="mm"):
    alpha = clamp(alpha)
    if alpha <= 0:
        return
    if alpha >= 1:
        d.text(P(x, y), s, font=f, fill=color, anchor=anchor)
        return
    # Pillow metin çiziminde dolgu alfasını yok sayar; maske ile karıştır
    box = [int(v) for v in d.textbbox(P(x, y), s, font=f, anchor=anchor)]
    mask = Image.new("L", (box[2] - box[0] + 2, box[3] - box[1] + 2))
    ImageDraw.Draw(mask).text((x * S - box[0], y * S - box[1]), s, font=f, fill=int(255 * alpha), anchor=anchor)
    d._image.paste(Image.new("RGB", mask.size, color), (box[0], box[1]), mask)


def line(d, pts, color, width, alpha=1.0):
    d.line([c * S for p in pts for c in p], fill=rgba(color, alpha), width=int(width * S), joint="curve")


def sun(d, x, y, r, t, alpha=1.0):
    glow(d, x, y, r, (255, 200, 60), strength=alpha)
    for i in range(12):
        a = t * 0.6 + i * math.pi / 6
        r1, r2 = r * 1.25, r * (1.55 + 0.08 * math.sin(t * 4 + i))
        line(d, [(x + math.cos(a) * r1, y + math.sin(a) * r1),
                 (x + math.cos(a) * r2, y + math.sin(a) * r2)], (255, 205, 70), r * 0.12, alpha)
    circle(d, x, y, r, rgba((255, 196, 45), alpha))
    circle(d, x - r * 0.15, y - r * 0.15, r * 0.7, rgba((255, 222, 110), alpha))


def caption(d, t, start, step, msg):
    """Alttan kayan, numaralı açıklama kartı."""
    p = ease_out(prog(t, start, 0.6))
    if p <= 0:
        return
    f = font(FONT_SEMI, 30)
    tw = d.textlength(msg, font=f) / S
    bw = tw + 120
    x0 = (W - bw) / 2
    y0 = lerp(H + 20, H - 110, p)
    d.rounded_rectangle(P(x0, y0, x0 + bw, y0 + 76), radius=38 * S, fill=rgba((15, 23, 42), 0.85 * p))
    circle(d, x0 + 40, y0 + 38, 22, rgba((255, 196, 45), p))
    text(d, x0 + 40, y0 + 39, str(step), font(FONT_BOLD, 26), (15, 23, 42), p)
    text(d, x0 + 78, y0 + 38, msg, f, (255, 255, 255), p, anchor="lm")


def panel(d, cx, cy, w, h, lit=0.0, alpha=1.0):
    """Eğik güneş paneli (paralelkenar) + ayak."""
    skew = w * 0.25
    line(d, [(cx, cy + h / 2), (cx, cy + h / 2 + 90)], (90, 100, 120), 14, alpha)
    corners = [(cx - w / 2 + skew, cy - h / 2), (cx + w / 2 + skew, cy - h / 2),
               (cx + w / 2 - skew, cy + h / 2), (cx - w / 2 - skew, cy + h / 2)]
    d.polygon([c * S for p in corners for c in p], fill=rgba((200, 210, 225), alpha))
    cols, rows = 6, 4
    m = 8
    for r in range(rows):
        for c in range(cols):
            def pt(u, v):
                x = cx - w / 2 + u * w + skew * (1 - 2 * v)
                return (x, cy - h / 2 + v * h)
            u0, u1 = c / cols, (c + 1) / cols
            v0, v1 = r / rows, (r + 1) / rows
            du, dv = m / w, m / h * 0.6
            q = [pt(u0 + du / 2, v0 + dv / 2), pt(u1 - du / 2, v0 + dv / 2),
                 pt(u1 - du / 2, v1 - dv / 2), pt(u0 + du / 2, v1 - dv / 2)]
            flick = 0.5 + 0.5 * math.sin(r * 1.7 + c * 2.3)
            base = mix((30, 64, 140), (70, 150, 255), clamp(lit * (0.6 + 0.4 * flick)))
            d.polygon([c2 * S for p in q for c2 in p], fill=rgba(base, alpha))


# -------------------------------------------------------------------- sahneler
def scene_title(t):
    img = gradient((10, 18, 48), (52, 40, 110))
    d = ImageDraw.Draw(img, "RGBA")
    # yıldızlar
    rng = np.random.default_rng(3)
    for sx, sy, ph in zip(rng.uniform(0, W, 60), rng.uniform(0, H * 0.7, 60), rng.uniform(0, 6, 60)):
        circle(d, sx, sy, 1.6, rgba((255, 255, 255), 0.4 + 0.4 * math.sin(t * 3 + ph)))
    rise = ease_out(prog(t, 0, 1.6))
    sun(d, W / 2, lerp(H + 120, 250, rise), 80, t)
    # ufuk tepeleri
    d.ellipse(P(-200, 560, 700, 1000), fill=(28, 36, 80))
    d.ellipse(P(500, 580, 1500, 1050), fill=(22, 30, 68))
    p1 = back_out(prog(t, 0.7, 0.7))
    p2 = ease_out(prog(t, 1.1, 0.7))
    text(d, W / 2, 440 + (1 - p1) * 40, "Güneş Enerjisi", font(FONT_BOLD, 78), (255, 255, 255), clamp(p1))
    text(d, W / 2, 515 + (1 - p2) * 30, "nasıl çalışır?", font(FONT_MED, 40), (255, 210, 110), p2)
    return img


def scene_panel(t):
    img = gradient((90, 170, 245), (190, 228, 255))
    d = ImageDraw.Draw(img, "RGBA")
    sun(d, 180, 150, 70, t + 3)
    d.rectangle(P(0, 520, W, H), fill=(110, 190, 110))
    d.ellipse(P(-300, 470, 900, 760), fill=(125, 205, 120))
    pin = ease_out(prog(t, 0.2, 0.8))
    pcx, pcy = lerp(1400, 860, pin), 360
    hits = 0
    for i in range(14):
        st = 0.9 + i * 0.25
        k = (t - st) % 3.5 / 0.9 if t > st else -1
        if 0 <= k <= 1:
            tx = pcx - 120 + (i * 53) % 240
            ty = pcy - 50 + (i * 37) % 100
            x, y = lerp(220, tx, k), lerp(180, ty, k)
            glow(d, x, y, 7, (255, 230, 90), 1.6, 5)
            circle(d, x, y, 7, (255, 240, 120))
            if k > 0.8:
                hits += 1
    lit = clamp(prog(t, 1.5, 2.0) * 0.7 + hits * 0.15)
    panel(d, pcx, pcy, 340, 170, lit)
    caption(d, t, 0.8, 1, "Güneş ışığı (fotonlar) panele çarpar")
    return img


def scene_cell(t):
    img = gradient((14, 22, 46), (24, 38, 74))
    d = ImageDraw.Draw(img, "RGBA")
    x0, x1 = 200, 760
    yn0, yn1, yp1 = 210, 320, 440
    appear = ease_out(prog(t, 0, 0.6))
    d.rectangle(P(x0, yn0, x1, yn1), fill=rgba((60, 120, 230), appear))
    d.rectangle(P(x0, yn1, x1, yp1), fill=rgba((150, 70, 160), appear))
    line(d, [(x0, yn1), (x1, yn1)], (255, 255, 255), 2, 0.5 * appear)
    text(d, x0 + 20, yn0 + 24, "N tipi silisyum", font(FONT_SEMI, 22), (255, 255, 255), appear, "lm")
    text(d, x0 + 20, yp1 - 24, "P tipi silisyum", font(FONT_SEMI, 22), (255, 255, 255), appear, "lm")
    # devre kablosu: üst katmandan çıkıp ampulden geçip alt katmana döner
    bx, by = 1010, 330
    path = [(x1 - 40, yn0), (x1 - 40, 120), (bx, 120), (bx, by - 60),
            (bx, by + 60), (bx, 560), (x1 - 40, 560), (x1 - 40, yp1)]
    wire = ease_out(prog(t, 0.4, 0.8))
    line(d, path, (200, 210, 230), 6, wire)
    # ampul
    on = clamp(prog(t, 2.0, 1.5))
    if on > 0:
        glow(d, bx, by - 10, 50, (255, 220, 90), on * 2.5, 12)
    circle(d, bx, by - 10, 46, mix((80, 90, 110), (255, 228, 110), on))
    d.rectangle(P(bx - 22, by + 34, bx + 22, by + 64), fill=(150, 160, 175))
    text(d, bx + 90, by - 10, "Elektrik!", font(FONT_BOLD, 30), (255, 220, 100), on, "lm")
    # fotonlar yukarıdan gelir
    for i in range(8):
        st = 0.6 + i * 0.35
        k = (t - st) / 0.7
        if 0 <= k <= 1:
            x = x0 + 60 + (i * 97) % (x1 - x0 - 160)
            y = lerp(20, yn1, k)
            glow(d, x, y, 7, (255, 230, 90), 1.6, 5)
            circle(d, x, y, 7, (255, 240, 120))
    # elektronlar devrede dolaşır
    seglens = [math.dist(a, b) for a, b in zip(path, path[1:])]
    total = sum(seglens)

    def along(s):
        s %= total
        for (a, b), L in zip(zip(path, path[1:]), seglens):
            if s <= L:
                return lerp(a[0], b[0], s / L), lerp(a[1], b[1], s / L)
            s -= L
        return path[-1]

    flow = clamp(prog(t, 1.4, 0.8))
    for i in range(16):
        s = (t - 1.4) * 260 + i * total / 16
        if s < 0 or flow <= 0:
            continue
        ex, ey = along(s)
        circle(d, ex, ey, 11, rgba((80, 230, 255), flow))
        text(d, ex, ey - 1, "−", font(FONT_BOLD, 18), (10, 30, 50), flow)
    caption(d, t, 0.6, 2, "Fotonlar elektronları harekete geçirir")
    return img


def scene_grid(t):
    img = gradient((236, 244, 255), (205, 222, 245))
    d = ImageDraw.Draw(img, "RGBA")
    ys = 300
    xs = [200, 520, 840, 1100]
    # bağlantı çizgileri ve akan noktalar
    for i in range(3):
        p = ease_out(prog(t, 0.4 + i * 0.6, 0.5))
        a, b = xs[i] + 90, xs[i + 1] - 90
        if p > 0:
            line(d, [(a, ys), (lerp(a, b, p), ys)], (120, 135, 160), 6)
        if p >= 1:
            for j in range(4):
                k = ((t * 0.9 + j / 4) % 1)
                circle(d, lerp(a, b, k), ys, 7, (255, 170, 30))
    # 1) panel
    p = back_out(prog(t, 0, 0.6))
    if p > 0:
        panel(d, xs[0], ys - 10, 150 * p, 90 * p, 0.9)
        text(d, xs[0], ys + 140, "Panel", font(FONT_SEMI, 26), (30, 40, 70), clamp(p))
    # DC etiketi
    text(d, (xs[0] + xs[1]) / 2, ys - 40, "DC", font(FONT_BOLD, 28), (220, 120, 20), prog(t, 0.6, 0.4))
    # 2) invertör
    p = back_out(prog(t, 0.8, 0.6))
    if p > 0:
        s = 80 * p
        d.rounded_rectangle(P(xs[1] - s, ys - s, xs[1] + s, ys + s), radius=int(18 * S), fill=(40, 55, 90))
        pts = [(xs[1] - 45 * p + k * 90 * p / 40, ys + 22 * p * math.sin(k / 40 * 2 * math.pi + t * 6))
               for k in range(41)]
        line(d, pts, (120, 220, 255), 6)
        text(d, xs[1], ys + 140, "İnvertör", font(FONT_SEMI, 26), (30, 40, 70), clamp(p))
    text(d, (xs[1] + xs[2]) / 2, ys - 40, "AC", font(FONT_BOLD, 28), (30, 140, 220), prog(t, 1.2, 0.4))
    # 3) ev
    p = back_out(prog(t, 1.4, 0.6))
    if p > 0:
        hx, hy, s = xs[2], ys + 20, 90 * p
        d.polygon(P(hx - s * 1.1, hy - s * 0.3, hx, hy - s * 1.2, hx + s * 1.1, hy - s * 0.3), fill=(200, 70, 60))
        d.rectangle(P(hx - s * 0.8, hy - s * 0.3, hx + s * 0.8, hy + s * 0.75), fill=(250, 240, 225))
        lit = clamp(prog(t, 2.2, 0.6))
        wc = mix((90, 100, 120), (255, 215, 80), lit)
        for wx in (-0.45, 0.45):
            if lit > 0:
                glow(d, hx + s * wx, hy + s * 0.1, s * 0.18, (255, 210, 80), lit, 6)
            d.rectangle(P(hx + s * (wx - 0.18), hy - s * 0.08, hx + s * (wx + 0.18), hy + s * 0.28), fill=wc)
        text(d, hx, ys + 140, "Ev", font(FONT_SEMI, 26), (30, 40, 70), clamp(p))
    # 4) şebeke (fazla enerji)
    p = back_out(prog(t, 2.0, 0.6))
    if p > 0:
        gx = xs[3]
        line(d, [(gx - 40 * p, ys + 80), (gx, ys - 80 * p), (gx + 40 * p, ys + 80)], (90, 100, 120), 8)
        line(d, [(gx - 30 * p, ys - 30 * p), (gx + 30 * p, ys - 30 * p)], (90, 100, 120), 8)
        text(d, gx, ys + 140, "Şebeke", font(FONT_SEMI, 26), (30, 40, 70), clamp(p))
    caption(d, t, 0.5, 3, "İnvertör, akımı evde kullanılır hale getirir")
    return img


def scene_outro(t):
    img = gradient((255, 168, 76), (240, 90, 100))
    d = ImageDraw.Draw(img, "RGBA")
    p = back_out(prog(t, 0, 0.7))
    sun(d, W / 2, 230, 70 * clamp(p, 0, 1.2), t)
    words = ["Temiz", "Sessiz", "Yenilenebilir"]
    f = font(FONT_BOLD, 58)
    gap = 60
    widths = [d.textlength(w, font=f) / S for w in words]
    x = (W - sum(widths) - gap * 2) / 2
    for i, (w, wd) in enumerate(zip(words, widths)):
        q = back_out(prog(t, 0.5 + i * 0.35, 0.5))
        text(d, x + wd / 2, 420 + (1 - clamp(q)) * 40, w, f, (255, 255, 255), clamp(q))
        if i < 2:
            circle(d, x + wd + gap / 2, 422, 6, rgba((255, 255, 255), clamp(q)))
        x += wd + gap
    q = ease_out(prog(t, 1.6, 0.6))
    text(d, W / 2, 510, "Güneşten gelen enerji, evinize.", font(FONT_MED, 32), (255, 240, 220), q)
    return img


SCENES = [  # (başlangıç, bitiş, fonksiyon)
    (0.0, 3.0, scene_title),
    (3.0, 8.0, scene_panel),
    (8.0, 13.0, scene_cell),
    (13.0, 17.0, scene_grid),
    (17.0, 20.0, scene_outro),
]


def render_frame(t):
    active = [(s, e, fn) for s, e, fn in SCENES if s - XFADE <= t < e]
    imgs = []
    for s, e, fn in active:
        local = max(0.0, t - s)
        alpha = clamp((t - (s - XFADE)) / XFADE) if s > 0 else 1.0
        imgs.append((fn(local), alpha))
    img = imgs[0][0]
    for im, a in imgs[1:]:
        img = Image.blend(img, im, a)
    img = img.resize((W, H), Image.LANCZOS)
    # başta ve sonda siyaha geçiş
    fade = min(clamp(t / 0.4), clamp((DURATION - t) / 0.6))
    if fade < 1:
        img = Image.blend(Image.new("RGB", (W, H)), img, fade)
    return img


# ----------------------------------------------------------------------- ses
def make_audio(path, sr=44100):
    n = int(sr * DURATION)
    tt = np.arange(n) / sr
    out = np.zeros(n)
    chords = [[261.63, 329.63, 392.0, 493.88],  # Cmaj7
              [220.0, 261.63, 329.63, 392.0],   # Am7
              [174.61, 220.0, 261.63, 329.63],  # Fmaj7
              [196.0, 246.94, 293.66, 392.0]]   # G
    seg = DURATION / len(chords)
    for i, ch in enumerate(chords):
        env = np.clip(np.minimum((tt - i * seg) / 0.8, (i * seg + seg + 0.8 - tt) / 0.8), 0, 1)
        for f in ch:
            out += env * (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * f * 2 * tt)) * 0.05
    for at in [0.8, 3.8, 8.6, 13.5, 17.5]:  # sahne/başlık anlarında çan
        m = tt >= at
        dt = tt[m] - at
        out[m] += 0.18 * np.exp(-dt * 3) * np.sin(2 * np.pi * 1046.5 * dt)
        out[m] += 0.08 * np.exp(-dt * 4) * np.sin(2 * np.pi * 1568.0 * dt)
    out *= np.clip(np.minimum(tt / 1.0, (DURATION - tt) / 1.5), 0, 1)
    out /= max(1e-9, np.abs(out).max()) / 0.8
    data = (out * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def main():
    make_audio(AUDIO)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", AUDIO, "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", OUT],
        stdin=subprocess.PIPE)
    for i in range(FRAMES):
        proc.stdin.write(render_frame(i / FPS).tobytes())
        if i % 60 == 0:
            print(f"kare {i}/{FRAMES}", flush=True)
    proc.stdin.close()
    proc.wait()
    print("Bitti:", OUT)


if __name__ == "__main__":
    main()
