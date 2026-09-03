"""_row1_zoom.py — temp: answer "row-1 表情没差异" directly.

Renders 平和/开心/兴奋/惊喜 through the REAL express_frame pipeline and shows,
side by side at large face scale:
  * the four faithful recipe poses (mouth 0 / 0.15 / 0.35 / 0.55), and
  * one reference pose with the SAME 开心 recipe but the mouth forced to 1.0
    (what speaking/laughing looks like) — proving llny's mouth range is big,
    the recipes just use it shallowly.

Annotated under each face with the actual recipe mouth/brow values so it's clear
the difference IS there, it's just small on llny. 用完即删.
"""
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import glfw
import live2d.v3 as live2d

import desktop_pet as pet

W, H = 1560, 2160
TARGET_W = 460                       # each face resized to this width
FONT_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                   r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]


def get_font(px):
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, px)
        except Exception:
            continue
    return None


glfw.init()
win = pet.LayeredWindow(W, H, x=-3200, y=-3200)
try:
    live2d.init()
    live2d.glInit()
    model = live2d.LAppModel()
    model.LoadModelJson(pet.DEFAULT_MODEL)
    model.SetAutoBlinkEnable(False)
    model.SetAutoBreathEnable(False)
    model.Resize(W, H)
    model.SetScale(1.0)
    if "Param14" in {model.GetParameter(i).id
                     for i in range(model.GetParameterCount())}:
        model.SetParameterValue("Param14", 1.0)

    def render_face(key, override=None):
        """Render one emotion through express_frame; return the head-shoulders
        crop (hair crown .. chest), already tightened. override: optional dict
        merged on top of the EMOTIONS recipe (temp, restored after). key must be
        a real EMOTIONS entry; the display label is separate."""
        saved = None
        if override:
            saved = pet.EMOTIONS[key]
            pet.EMOTIONS[key] = dict(saved)
            pet.EMOTIONS[key].update(override)
        try:
            control = pet.PetControl(key)
            estate = pet.new_emotion_state(model, key)
            for _ in range(12):          # converge cross-fade ~ (0.82^12≈0.09)
                pet.express_frame(win, model, 20, control, None, estate,
                                  clothes=0.0, tug=(0.0, 0.0))
            img = Image.fromarray(win.last_rgba, "RGBA").transpose(
                Image.FLIP_TOP_BOTTOM)
            fig = img.crop(img.getbbox())
        finally:
            if saved is not None:
                pet.EMOTIONS[key] = saved
        # head-and-shoulders: top of bbox .. 36% height (hair crown .. chest),
        # tighten to opaque columns, drop transparent bottom rows.
        w0, h0 = fig.size
        head = fig.crop((0, 0, w0, int(h0 * 0.36)))
        a = np.asarray(head)
        opcol = (a[:, :, 3] > 10).any(axis=0)
        cols = np.where(opcol)[0]
        c0 = max(0, int(cols.min()) - 6)
        c1 = min(w0, int(cols.max()) + 7)
        head = head.crop((c0, 0, c1, head.height))
        a = np.asarray(head)
        oprow = (a[:, :, 3] > 10).any(axis=1)
        rows = np.where(oprow)[0]
        head = head.crop((0, rows.min(), head.width, rows.max() + 1))
        r = TARGET_W / head.width
        return head.resize((TARGET_W, max(1, round(head.height * r))),
                           Image.LANCZOS)

    # 示意嘴型(图片专用, 不动 app): 嘴张 + Jaw 阶梯, 一眼可分。平和保持真实。
    ILLUS = {
        "开心": {"ParamMouthOpenY": 0.45, "ParamMouthForm": 1.0, "Param62": 0.30},
        "兴奋": {"ParamMouthOpenY": 0.70, "ParamMouthForm": 1.0, "Param62": 0.60},
        "惊喜": {"ParamMouthOpenY": 0.90, "ParamMouthForm": 0.90, "Param62": 0.90},
    }
    specs = [
        ("平和",  "平和", None, "真实配方 · 嘴 0.00 · 眉 0"),
        ("开心",  "开心", ILLUS["开心"], "示意嘴 · 开 0.45 · Jaw 0.30 (配方 0.15)"),
        ("兴奋",  "兴奋", ILLUS["兴奋"], "示意嘴 · 开 0.70 · Jaw 0.60 (配方 0.35)"),
        ("惊喜",  "惊喜", ILLUS["惊喜"], "示意嘴 · 开 0.90 · Jaw 0.90 (配方 0.55)"),
        ("嘴上限参考", "惊喜",
         {"ParamMouthOpenY": 1.0, "ParamMouthForm": 1.0, "Param62": 1.0},
         "嘴+Jaw 拉满 1.0 的上限"),
    ]
    faces = [(lab, render_face(key, ov), note) for lab, key, ov, note in specs]
finally:
    glfw.terminate()

max_h = max(f.height for _, f, _ in faces)
pad = 20
font = get_font(42)
sfont = get_font(30)
cap_font = get_font(40)
CW = TARGET_W + 26
sheet = Image.new("RGBA", (pad * 2 + CW * len(faces), pad + max_h + 170),
                  (255, 255, 255, 255))
d = ImageDraw.Draw(sheet)
d.text((pad, 6), "真实配方实拍 —— llny 嘴张范围很小，前 4 格嘴 0/0.15/0.35/0.55",
       font=cap_font, fill=(70, 70, 70, 255))
for i, (emo, face, note) in enumerate(faces):
    cx = pad + i * CW
    by = pad + 70 + max_h - face.height        # chins bottom-aligned
    sheet.alpha_composite(face, (cx + 13, by))
    if font is not None:
        w = d.textlength(emo, font=font)
        d.text((cx + (CW - w) / 2, pad + 70 + max_h + 8), emo,
               font=font, fill=(40, 40, 40, 255))
    if sfont is not None:
        w = d.textlength(note, font=sfont)
        d.text((cx + (CW - w) / 2, pad + 70 + max_h + 66), note,
               font=sfont, fill=(120, 120, 120, 255))

out = os.path.join(HERE, "emotions_row1_zoom.png")
sheet.convert("RGB").save(out)
print("saved", out, sheet.size)
print("face heights:", [f.height for _, f, _ in faces])
