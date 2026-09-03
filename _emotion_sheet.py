"""_emotion_sheet.py — temp: render all 16 llny emotion poses through the real
express_frame pipeline and assemble labeled grids:
  emotions_sheet.png   — full-body standing characters (4x4)
  emotions_faces.png   — head-and-shoulders close-up (4x4), so the mouth/eye/
                         brow differences of the row-1 (平和/开心/兴奋/惊喜…)
                         emotions are actually visible

用真实管线（express_frame + 物理 + 常驻摆动，无待机 motion 以便各格姿态可比、
固定帧号 f 避开眨眼），去掉水印、穿外套（默认态）。每情绪独立 state → 无残留。
本脚本用完即删。
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

W, H = 1560, 2160                    # physical render window (2× for sharper faces)
COLS, ROWS = 4, 4
FONT_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                   r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]

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
        model.SetParameterValue("Param14", 1.0)     # llny watermark off

    idle_motion = None                # no idle motion: uniform comparable stances

    # row-1 示意化 (图片专用, 不动 app): llny 的 ParamMouthOpenY 视觉范围极小,
    # 平和/开心/兴奋/惊喜 静止时几乎一样。示意 = 把嘴张拉到"说话/大笑水平"
    # (MouthOpenY + Param62 Jaw 阶梯), 让图表一眼可分。标签注明"示意"。
    # True = 重新生成带示意嘴型的版本; False = 完全忠实版。
    ILLUSTRATED = True
    ILLUS_OVERRIDES = {
        "开心": {"ParamMouthOpenY": 0.45, "ParamMouthForm": 1.0, "Param62": 0.30},
        "兴奋": {"ParamMouthOpenY": 0.70, "ParamMouthForm": 1.0, "Param62": 0.60},
        "惊喜": {"ParamMouthOpenY": 0.90, "ParamMouthForm": 0.90, "Param62": 0.90},
    }

    F = 20                            # f%170 not in blink band; sway phase identical
    figures = []                      # (emotion, full-body crop)
    for emotion in list(pet.EMOTIONS):
        saved = None
        if ILLUSTRATED and emotion in ILLUS_OVERRIDES:
            saved = pet.EMOTIONS[emotion]                  # 临时改配方渲染
            pet.EMOTIONS[emotion] = dict(saved)
            pet.EMOTIONS[emotion].update(ILLUS_OVERRIDES[emotion])
        try:
            control = pet.PetControl(emotion)
            estate = pet.new_emotion_state(model, emotion)
            for _ in range(6):        # let physics settle hair/etc. at the same f
                pet.express_frame(win, model, F, control, idle_motion, estate,
                                  clothes=0.0, tug=(0.0, 0.0))
            raw = win.last_rgba
            img = Image.fromarray(raw, "RGBA").transpose(Image.FLIP_TOP_BOTTOM)
            bb = img.getbbox()
            if bb is None:
                print(f"!! {emotion}: empty render")
                figures.append((emotion, None))
                continue
            figures.append((emotion, img.crop(bb)))
            print(f"ok {emotion}: bbox={bb}")
        finally:
            if saved is not None:
                pet.EMOTIONS[emotion] = saved
finally:
    glfw.terminate()


def get_font(px):
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, px)
        except Exception:
            continue
    return None


# ---- assemble the full-body grid -----------------------------------------
CELL_W, CELL_H = 270, 760
TARGET_CHAR_H = 650
pad, top = 24, 24
note_h = 0
NOTE = ("注:第一行 开心/兴奋/惊喜 的嘴型是\"说话/大笑\"水平的示意(原配方仅 "
        "MouthOpenY 0.15/0.35/0.55 + 加 Param62 Jaw)——llny 静止时嘴张幅度很小,"
        "不加示意前四格肉眼看不出差;其余行为真实配方。")
if ILLUSTRATED:
    note_h = 92
sheet = Image.new("RGBA",
                  (pad * 2 + COLS * CELL_W, top + ROWS * CELL_H + 8 + note_h),
                  (255, 255, 255, 255))
font = get_font(44)
nfont = get_font(28)
d = ImageDraw.Draw(sheet)
for i, (emotion, fig) in enumerate(figures):
    cx = pad + (i % COLS) * CELL_W
    cy = top + (i // COLS) * CELL_H
    if fig is not None:
        r = TARGET_CHAR_H / fig.height
        body = fig.resize((max(1, round(fig.width * r)), TARGET_CHAR_H),
                          Image.LANCZOS)
        assert body.width <= CELL_W - 16, f"{emotion} too wide: {body.width}"
        bx = cx + (CELL_W - body.width) // 2
        by = cy + CELL_H - TARGET_CHAR_H - 96     # feet baseline above the label
        sheet.alpha_composite(body, (bx, by))
    if font is not None:
        w = d.textlength(emotion, font=font)
        d.text((cx + (CELL_W - w) / 2, cy + CELL_H - 46), emotion,
               font=font, fill=(60, 60, 60, 255))
if ILLUSTRATED and nfont is not None:
    import textwrap
    wmax = COLS * CELL_W
    for li, ln in enumerate(textwrap.wrap(NOTE, width=56)):
        w = d.textlength(ln, font=nfont)
        d.text(((pad * 2 + COLS * CELL_W - w) / 2,
                top + ROWS * CELL_H + 12 + li * 36), ln,
               font=nfont, fill=(150, 150, 150, 255))

out_body = os.path.join(HERE, "emotions_sheet.png")
sheet.convert("RGB").save(out_body)
print("saved", out_body, sheet.size)


# ---- assemble the head-and-shoulders grid ---------------------------------
# Character crops are full-body: head is the top ~34% of the opaque box (hair
# up top, chin ~24%, shoulders ~34%). Crop [0.30*top .. shoulders] is unreliable
# because each pose's top differs; instead crop from the top of the bbox down to
# 36% of its height, then tighten horizontally to the opaque face region, and
# resize all to a common face width for a uniform grid.
TARGET_FACE_W = 360                    # width each head cell is scaled to
FACE_TOP = 0.0                          # fig is already bbox-cropped: start at the hair crown
FACE_H = 0.34                           # hair crown .. chest
face_cells = []
for emotion, fig in figures:
    if fig is None:
        face_cells.append((emotion, None))
        continue
    w0, h0 = fig.size
    top = int(h0 * FACE_TOP)
    bot = int(h0 * FACE_H)
    head = fig.crop((0, top, w0, bot))
    # tighten to opaque columns with a little margin
    a = np.asarray(head)
    opcol = (a[:, :, 3] > 10).any(axis=0)
    cols = np.where(opcol)[0]
    if cols.size == 0:
        face_cells.append((emotion, None))
        continue
    c0 = max(0, int(cols.min()) - 6)
    c1 = min(w0, int(cols.max()) + 7)
    head = head.crop((c0, 0, c1, head.height))
    # drop transparent rows at the bottom margin as well
    a = np.asarray(head)
    oprow = (a[:, :, 3] > 10).any(axis=1)
    rows = np.where(oprow)[0]
    if rows.size:
        head = head.crop((0, rows.min(), head.width, rows.max() + 1))
    r = TARGET_FACE_W / head.width
    head = head.resize((TARGET_FACE_W, max(1, round(head.height * r))),
                       Image.LANCZOS)
    face_cells.append((emotion, head))

max_h = max((c.height for _, c in face_cells if c is not None), default=1)
FCELL_W, FCELL_H = TARGET_FACE_W + 24, max_h + 130
fsheet = Image.new("RGBA", (pad * 2 + COLS * FCELL_W,
                            top + ROWS * FCELL_H + 8 + note_h),
                   (255, 255, 255, 255))
ffont = get_font(40)
fnfont = get_font(28)
fd = ImageDraw.Draw(fsheet)
for i, (emotion, head) in enumerate(face_cells):
    cx = pad + (i % COLS) * FCELL_W
    cy = top + (i // COLS) * FCELL_H
    if head is not None:
        bx = cx + (FCELL_W - head.width) // 2
        by = cy + max_h - head.height            # bottom-align the chins so the
                                                 # mouth rows line up across cells
        fsheet.alpha_composite(head, (bx, by))
    if ffont is not None:
        w = fd.textlength(emotion, font=ffont)
        fd.text((cx + (FCELL_W - w) / 2, cy + FCELL_H - 44), emotion,
                font=ffont, fill=(60, 60, 60, 255))
if ILLUSTRATED and fnfont is not None:
    import textwrap
    for li, ln in enumerate(textwrap.wrap(NOTE, width=64)):
        w = fd.textlength(ln, font=fnfont)
        fd.text(((pad * 2 + COLS * FCELL_W - w) / 2,
                 top + ROWS * FCELL_H + 12 + li * 36), ln,
                font=fnfont, fill=(150, 150, 150, 255))

out_face = os.path.join(HERE, "emotions_faces.png")
fsheet.convert("RGB").save(out_face)
print("saved", out_face, fsheet.size, "face_cells:", [c.size for _, c in face_cells if c is not None])
