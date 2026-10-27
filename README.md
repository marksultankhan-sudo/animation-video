# Güneş Enerjisi Nasıl Çalışır? — 20 sn animasyonlu açıklama videosu

`explainer.mp4` — 1280×720, 30 fps, 20 saniye, arka plan müziğiyle.

| Süre | Sahne |
|------|-------|
| 0–3 sn | Başlık: güneş doğar |
| 3–8 sn | 1. Güneş ışığı (fotonlar) panele çarpar |
| 8–13 sn | 2. Fotonlar silisyumdaki elektronları harekete geçirir, ampul yanar |
| 13–17 sn | 3. Panel → (DC) → İnvertör → (AC) → Ev → Şebeke |
| 17–20 sn | Kapanış: Temiz • Sessiz • Yenilenebilir |

Yeniden üretmek için (Python 3, Pillow, numpy, ffmpeg gerekir):

```bash
python3 render.py
```

Metinler, renkler ve zamanlamalar `render.py` içindeki sahne fonksiyonlarından değiştirilebilir.

---

# Hi Vit — 9:16 3D product ad (`make_ad.py`)

A ~19 s, 1080×1920, 30 fps floating-product commercial for Hi Vit multifruit juice
(orange, passion fruit, sea buckthorn). It is generated entirely by `make_ad.py`
(Blender 5.2 LTS + Cycles, Pillow, ffmpeg). All tunables (colours, shot timings,
camera moves, lights, text) are in the `CONFIG` dict at the top of the script.

```bash
# Blender as a Python module (what this repo was built with):
python -m venv venv && venv/bin/pip install bpy==5.2.3 numpy pillow scipy fonttools librosa
venv/bin/python make_ad.py --stage prep,stills     # look-dev stills -> build/stills/
venv/bin/python make_ad.py --stage preview         # 540x960 low-sample animation -> preview.mp4
venv/bin/python make_ad.py --stage final           # 1080x1920 -> hivit_ad.mp4
# or with a Blender binary:  blender -b -P make_ad.py -- --stage final
```

Inputs: `assets/hivit_*.png` (front product photos with alpha), optional
`assets/music.mp3` (beats are detected with librosa and cuts snapped to them;
without it the video is encoded silent and cuts sit on a 120 BPM grid).
The Nunito font is downloaded from the google/fonts repo on first run.

Look-dev stills for approval are in `previews/`.
