"""desktop_pet.py — show a Live2D v3 character in a transparent, frameless,
always-on-top window (Windows). Only the character is visible: the background is
fully transparent (per-pixel alpha via a Win32 layered window), no window chrome.

The character plays a looping demo animation (watermark off + blink + talk +
head sway + coat on/off). Press ESC (or Alt+F4) to quit. Drag with the left
mouse button (pressing on a visible pixel of the character) to move it around.
Resize the character live with the + / - keys (0 resets to --scale).

--viewer mode reproduces the idle Live2DViewer shows by default for llny:
regular blinking plus a multi-axis head/body sway drive the model's physics, so
the hair/ear/bow meshes visibly swing (physics3.json feeds 84 ArtMesh-rotation
params from head/body angles), and the model's own idle motion keeps looping
(the orphan motions/idel.motion3.json: breath + subtle body sway). The
procedural demo animation (talk + coat on/off) is skipped.

Express mode (--emotion / --lipsync / --listen / --control-port) adds 16
emotion poses on top of the viewer-style idle, cross-faded smoothly (~0.25 s).
The mouth follows audio energy — either from a wav the pet plays itself
(--lipsync) or from whatever the system is currently playing (--listen, WASAPI
loopback capture, so any audio player drives it) — scaled by the emotion's
loudness (MOUTH_AMP). With --control-port an external pipeline (AI voice
output, etc.) can switch the emotion or force the mouth at any time over TCP —
see the README for the JSON protocol.

Usage:
  python desktop_pet.py [--model /path/to/model.model3.json]
                        [--width 520 --height 720] [--x 0 --y 0] [--scale 1.0]
                        [--viewer]           # SDK-native dynamics (see above)
                        [--emotion NAME]     # express mode, start in this emotion
                        [--lipsync wav]      # pet plays wav, mouth follows it
                        [--listen]           # mouth follows any system audio
                        [--control-port PORT]  # TCP JSON control (switch emotion/mouth)
                        [--self-test]        # one transparent frame + alpha stats

Note: GLFW_TRANSPARENT_FRAMEBUFFER is macOS-only, so on Windows we render to the
GL back buffer, read back RGBA, premultiply alpha, and present it through
UpdateLayeredWindow (classic per-pixel-alpha layered window).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import socketserver
import sys
import threading
import time

import glfw
import live2d.v3 as live2d
import numpy as np
from OpenGL import GL
from PIL import Image

from lipsync import play_wav_with_energy
from system_listen import listen_system_output

try:                                    # Windows console may not default to UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

W, H = 520, 720                       # logical (design) window size in px

def _default_window_size():
    """Default PHYSICAL window size: the logical W x H scaled by the primary
    monitor's DPI. GLFW windows are sized in physical pixels, so on a 4K/250%
    display the old fixed 520x720 default rendered the pet ~2.5x smaller than
    intended. Scaling by the content scale keeps the pet a consistent size on
    any DPI (and much bigger on 4K/250% displays). Clamped to the workarea
    with a small margin so it never runs off-screen."""
    glfw.init()
    mon = glfw.get_primary_monitor()
    sx, _ = glfw.get_monitor_content_scale(mon)
    s = max(1.0, sx)                  # logical px -> physical px
    w, h = int(round(W * s)), int(round(H * s))
    _, _, mw, mh = glfw.get_monitor_workarea(mon)
    w = min(w, max(200, mw - 80))
    h = min(h, max(200, mh - 80))
    return w, h

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(
    HERE, "live2d-py", "Resources", "v3", "llny", "llny.model3.json")

# --------------------------------------------------------------------------
# Win32 layered-window plumbing
# --------------------------------------------------------------------------

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# 64-bit handles must not be truncated by ctypes' default int argtype.
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = ctypes.c_int
gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                   ctypes.c_uint]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = ctypes.c_int


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


# fix the placeholders used above
user32.UpdateLayeredWindow.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.POINTER(POINT), ctypes.POINTER(SIZE), ctypes.c_void_p,
    ctypes.POINTER(POINT), ctypes.c_uint32,
    ctypes.POINTER(BLENDFUNCTION), ctypes.c_uint32]
user32.UpdateLayeredWindow.restype = ctypes.c_int


class LayeredWindow:
    """A frameless, always-on-top GL window presented as a per-pixel-alpha
    layered window: each frame the GL back buffer is read back and blitted."""

    def __init__(self, width, height, x=None, y=None, click_through=False):
        self.w, self.h = width, height

        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)     # avoid flashing raw GL
        glfw.window_hint(glfw.DECORATED, glfw.FALSE)   # no window chrome
        glfw.window_hint(glfw.FLOATING, glfw.TRUE)     # always on top
        self.window = glfw.create_window(width, height, "Live2D pet", None, None)
        if not self.window:
            raise RuntimeError("glfw window creation failed")
        glfw.make_context_current(self.window)
        glfw.swap_interval(1)

        if x is None:
            mon = glfw.get_primary_monitor()
            _, _, mw, mh = glfw.get_monitor_workarea(mon)
            x, y = mw - width - 40, 40                    # top-right corner
        glfw.set_window_pos(self.window, x, y)

        self.hwnd = glfw.get_win32_window(self.window)
        style = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        ex = WS_EX_LAYERED | WS_EX_TOPMOST
        if click_through:
            ex |= WS_EX_TRANSPARENT
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, style | ex)

        # 32-bit BGRA bottom-up DIB that UpdateLayeredWindow paints
        self.hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_screen)
        bmi = _dib_info(width, height)
        pixels = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(
            self.hdc_mem, ctypes.byref(bmi), ctypes.c_uint(0),
            ctypes.byref(pixels), None, 0)
        if not self.bmp:
            raise RuntimeError("CreateDIBSection failed")
        self.pixels = pixels
        self.old_bmp = gdi32.SelectObject(self.hdc_mem, self.bmp)

        self.blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self.pt_dst = POINT(0, 0)
        self.pt_src = POINT(0, 0)
        self.size = SIZE(width, height)
        self.last_rgba = None                     # straight-alpha RGBA (bottom-up)

        self._drag = None                         # (win_x, win_y, cursor_x, cursor_y)
        self._last_rclick = (0.0, None)           # (glfw time, (x, y)) for right dbl-click
        self.clothes_cb = None                    # called on right-double-click (jacket)
        glfw.set_mouse_button_callback(self.window, self._on_mouse_button)
        glfw.set_cursor_pos_callback(self.window, self._on_cursor_pos)

        glfw.show_window(self.window)
        glfw.poll_events()

    # ---- drag to move the window ----------------------------------------

    def _hit_character(self, x, y):
        """True if the cursor (content coords, y down) is over a visible pixel."""
        alpha = self.last_rgba
        if alpha is None or not (0 <= x < self.w and 0 <= y < self.h):
            return False
        # last_rgba is bottom-up: row 0 = bottom of the window
        return alpha[self.h - 1 - int(y), int(x), 3] >= 16

    def _on_mouse_button(self, window, button, action, mods):
        if button == glfw.MOUSE_BUTTON_RIGHT:
            if action == glfw.PRESS:                 # right-double-click toggles clothes
                self._maybe_double_click(*glfw.get_cursor_pos(window))
            return
        if button != glfw.MOUSE_BUTTON_LEFT:
            return
        if action == glfw.PRESS:
            if not self._hit_character(*glfw.get_cursor_pos(window)):
                return                       # ignore clicks on transparent pixels
            wx, wy = glfw.get_window_pos(window)
            cx, cy = glfw.get_cursor_pos(window)
            self._drag = (wx, wy, cx, cy)
        elif action == glfw.RELEASE:
            self._drag = None

    def _maybe_double_click(self, x, y):
        """A second right-click on the character within ~0.5s (and near the
        previous one) is a double-click -> toggle the clothes via clothes_cb."""
        if not self._hit_character(x, y):
            return
        now = glfw.get_time()
        t0, p0 = self._last_rclick
        if (now - t0) < 0.5 and p0 is not None and \
                abs(x - p0[0]) < 24 and abs(y - p0[1]) < 24:
            self._last_rclick = (0.0, None)          # consumed the pair
            if self.clothes_cb is not None:
                self.clothes_cb()
        else:
            self._last_rclick = (now, (x, y))

    def _on_cursor_pos(self, window, x, y):
        if self._drag is None:
            return
        _, _, cx0, cy0 = self._drag              # grab offset inside the window
        wx, wy = glfw.get_window_pos(window)     # CURRENT position, not the press one
        # Target keeps the grabbed pixel under the cursor. Using the current
        # window position (rather than the position at press time) breaks the
        # feedback loop: moving the window under a stationary cursor changes the
        # content coords, which previously pulled the window back and forth.
        glfw.set_window_pos(window, round(wx + x - cx0), round(wy + y - cy0))

    def present(self):
        """Blit the just-rendered GL back buffer into the layered window."""
        GL.glReadBuffer(GL.GL_BACK)
        buf = GL.glReadPixels(0, 0, self.w, self.h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        raw = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 4)
        self.last_rgba = raw
        al = raw[:, :, 3].astype(np.uint16)
        out = np.empty((self.h, self.w, 4), dtype=np.uint8)
        out[:, :, 0] = (raw[:, :, 2].astype(np.uint16) * al // 255).astype(np.uint8)  # B
        out[:, :, 1] = (raw[:, :, 1].astype(np.uint16) * al // 255).astype(np.uint8)  # G
        out[:, :, 2] = (raw[:, :, 0].astype(np.uint16) * al // 255).astype(np.uint8)  # R
        out[:, :, 3] = al.astype(np.uint8)                                             # A
        ctypes.memmove(self.pixels, out.ctypes.data, out.nbytes)

        # pptDst must be NULL: passing a point would move the window there every
        # frame (dragging would snap back to that origin). None keeps the current
        # window position.
        user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_screen,
            None, ctypes.byref(self.size),
            self.hdc_mem, ctypes.byref(self.pt_src),
            0, ctypes.byref(self.blend), ULW_ALPHA)

    def close(self):
        gdi32.SelectObject(self.hdc_mem, self.old_bmp)
        gdi32.DeleteObject(self.bmp)
        gdi32.DeleteDC(self.hdc_mem)
        user32.ReleaseDC(None, self.hdc_screen)
        glfw.destroy_window(self.window)


def _dib_info(width, height):
    """BITMAPINFO for a 32-bit bottom-up DIB section."""
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", ctypes.c_uint32 * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = height             # positive -> bottom-up
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    return bmi


# --------------------------------------------------------------------------
# animation
# --------------------------------------------------------------------------

def param_lookup(model, ids):
    """Return {param_id: parameter} for the ids present in the model."""
    known = {}
    for i in range(model.GetParameterCount()):
        p = model.GetParameter(i)
        known[p.id] = p
    return {n: known[n] for n in ids if n in known}


# --------------------------------------------------------------------------
# emotions + lipsync (express mode)
# --------------------------------------------------------------------------

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# facial toggles that must fade back to 0 when an emotion stops using them,
# so a "生气/脸红/鼓脸" from a previous pose never lingers on the face
OVERLAY_TOGGLES = ("Param11", "Param12", "Param13", "Param8", "Param5",
                   "Param6", "Param9", "Param10", "Param7", "Param58")

# 16 emotions: poses written on top of the viewer-style idle. Ranges follow
# llny's parameters: brows/mouth [-1,1], eyes [0,1], head angle [-30,30],
# body angle [-10,10]. Param11 生气mark, Param12 哭, Param13 脸红, Param26 泪,
# Param58 鼓脸.
EMOTIONS = {
    "平和": {"ParamMouthForm": 0.15, "ParamEyeLSmile": 0.15,
             "ParamEyeRSmile": 0.15, "ParamEyeLOpen": 0.98,
             "ParamEyeROpen": 0.98},
    "开心": {"ParamMouthForm": 0.7, "ParamEyeLSmile": 0.8,
             "ParamEyeRSmile": 0.8, "ParamEyeLOpen": 0.92,
             "ParamEyeROpen": 0.92, "ParamBrowLY": 0.12, "ParamBrowRY": 0.12,
             "ParamAngleZ": 3, "Param13": 0.25},
    "兴奋": {"ParamMouthForm": 0.8, "ParamMouthOpenY": 0.35,
             "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
             "ParamEyeLSmile": 0.7, "ParamEyeRSmile": 0.7,
             "ParamBrowLY": 0.3, "ParamBrowRY": 0.3,
             "ParamEyeBallX": 0.1, "ParamEyeBallY": -0.15,
             "ParamAngleX": -4, "ParamAngleZ": 2},
    "惊喜": {"ParamMouthOpenY": 0.55, "ParamEyeLOpen": 1.0,
             "ParamEyeROpen": 1.0, "ParamBrowLY": 0.4, "ParamBrowRY": 0.4,
             "ParamAngleZ": -4, "ParamBodyAngleX": -3},
    "温柔": {"ParamMouthForm": 0.35, "ParamEyeLSmile": 0.5,
             "ParamEyeRSmile": 0.5, "ParamEyeLOpen": 0.9,
             "ParamEyeROpen": 0.9, "ParamBrowLY": -0.08, "ParamBrowRY": -0.08,
             "ParamAngleZ": 3, "Param13": 0.35},
    "关切": {"ParamBrowLAngle": -0.2, "ParamBrowRAngle": 0.2,
             "ParamBrowLY": 0.1, "ParamBrowRY": 0.1, "ParamMouthForm": -0.15,
             "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9,
             "ParamEyeBallX": 0.2},
    "好奇": {"ParamAngleZ": 9, "ParamEyeBallX": 0.3, "ParamEyeBallY": 0.1,
             "ParamBrowLY": 0.2, "ParamBrowRY": 0.05, "ParamMouthOpenY": 0.15,
             "ParamMouthForm": 0.1, "ParamEyeLOpen": 0.95,
             "ParamEyeROpen": 0.95},
    "期待": {"ParamBrowLY": 0.2, "ParamBrowRY": 0.2, "ParamEyeLOpen": 0.95,
             "ParamEyeROpen": 0.95, "ParamMouthForm": 0.3,
             "ParamMouthOpenY": 0.1, "ParamAngleY": -5, "ParamEyeBallY": -0.1},
    "无奈": {"ParamBrowLY": 0.25, "ParamBrowRY": -0.12, "ParamEyeLOpen": 0.85,
             "ParamEyeROpen": 0.85, "ParamMouthForm": 0.1, "ParamAngleZ": 5,
             "ParamBodyAngleZ": -2, "Param58": 0.5},
    "失望": {"ParamBrowLY": -0.2, "ParamBrowRY": -0.2, "ParamMouthForm": -0.3,
             "ParamEyeLOpen": 0.8, "ParamEyeROpen": 0.8, "ParamAngleX": 4,
             "ParamBodyAngleX": 3},
    "沮丧": {"ParamBrowLY": -0.3, "ParamBrowRY": -0.3, "ParamBrowLAngle": -0.15,
             "ParamBrowRAngle": 0.15, "ParamMouthForm": -0.5,
             "ParamEyeLOpen": 0.75, "ParamEyeROpen": 0.75, "Param26": 1.0,
             "ParamAngleX": 5, "ParamBodyAngleX": 4},
    "难过": {"ParamBrowLY": -0.2, "ParamBrowRY": -0.2, "ParamBrowLAngle": 0.3,
             "ParamBrowRAngle": -0.3, "ParamMouthForm": -0.55,
             "ParamEyeLOpen": 0.85, "ParamEyeROpen": 0.85, "Param12": 0.6,
             "Param26": 2.0, "ParamAngleX": 4, "ParamBodyAngleX": 2},
    "担心": {"ParamBrowLY": 0.15, "ParamBrowRY": 0.15, "ParamBrowLAngle": -0.3,
             "ParamBrowRAngle": 0.3, "ParamMouthForm": -0.15,
             "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9, "ParamEyeBallX": 0.1},
    "不满": {"ParamBrowLY": -0.2, "ParamBrowRY": -0.2, "ParamMouthForm": -0.4,
             "ParamEyeLOpen": 0.88, "ParamEyeROpen": 0.88, "ParamAngleZ": 2,
             "Param58": 0.5, "Param11": 0.2},
    "生气": {"ParamBrowLY": -0.3, "ParamBrowRY": -0.3, "ParamBrowLAngle": -0.4,
             "ParamBrowRAngle": 0.4, "ParamMouthForm": -0.3,
             "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9, "Param11": 1.0,
             "Param13": 0.3},
    "愤怒": {"ParamBrowLY": -0.45, "ParamBrowRY": -0.45, "ParamBrowLAngle": -0.5,
             "ParamBrowRAngle": 0.5, "ParamMouthForm": 0.4,
             "ParamMouthOpenY": 0.45, "ParamEyeLOpen": 1.0,
             "ParamEyeROpen": 1.0, "Param11": 1.0, "Param13": 0.5,
             "Param26": 1.0, "ParamAngleZ": -4},
}

# per-emotion mouth loudness: how wide the mouth opens per unit of audio
# energy (兴奋/愤怒 shout loudly, 温柔/沮丧 talk softly)
MOUTH_AMP = {"平和": 1.0, "开心": 1.0, "兴奋": 1.25, "惊喜": 1.15,
             "温柔": 0.6, "关切": 0.7, "好奇": 0.9, "期待": 0.9,
             "无奈": 0.7, "失望": 0.6, "沮丧": 0.6, "难过": 0.6,
             "担心": 0.7, "不满": 0.85, "生气": 1.1, "愤怒": 1.3}

# params driven via cross-fade: every emotion key plus every overlay toggle,
# so unused toggles smoothly return to 0. Angles + mouth are written separately.
ANGLE_KEYS = ("ParamAngleX", "ParamAngleY", "ParamAngleZ",
              "ParamBodyAngleX", "ParamBodyAngleY", "ParamBodyAngleZ")
MOUTH_KEYS = ("ParamMouthOpenY", "ParamMouthForm")
ALL_KEYS = sorted(set().union(*EMOTIONS.values()) | set(OVERLAY_TOGGLES))


class PetControl:
    """Thread-safe state shared between the GLFW thread (renderer) and the
    sounddevice/TCP threads (lipsync energy + external emotion commands)."""

    def __init__(self, emotion):
        self._lock = threading.Lock()
        self.emotion = emotion
        self.mouth = None                 # None = not talking, 0..1 = forced open
        self.clothes = None               # None = auto (demo coat sway), False = off, True = on

    def set_emotion(self, name):
        if name not in EMOTIONS:
            return False
        with self._lock:
            self.emotion = name
        return True

    def set_mouth(self, level):
        with self._lock:
            self.mouth = None if level is None else clamp(level, 0.0, 1.0)

    def toggle_clothes(self):
        """Flip the jacket: first call takes it off, next puts it back on.
        Returns the new state (True = dressed, False = undressed)."""
        with self._lock:
            self.clothes = False if self.clothes is None else (not self.clothes)
        return self.clothes

    def snapshot(self):
        with self._lock:
            return self.emotion, self.mouth


def new_emotion_state(model, emotion):
    """Build the cross-fade state for express mode, keeping only the params
    the model actually has (a non-llny model may lack some)."""
    valid = {model.GetParameter(i).id
             for i in range(model.GetParameterCount())}
    keys = [k for k in ALL_KEYS if k in valid]
    pose = EMOTIONS[emotion]
    return {"keys": keys,
            "valid": valid,
            "blend": {k: pose.get(k, 0.0) for k in keys},
            "mouth_cur": pose.get("ParamMouthOpenY", 0.0)}


def express_frame(win, model, f, control, idle_motion, state, clothes=None):
    """One frame of express mode: an emotion pose (cross-faded over ~15 frames)
    plus the viewer-style blink and sway, and a mouth that follows the WAV's
    RMS energy scaled by MOUTH_AMP while playing (otherwise the pose's own
    mouth opening).

    clothes: 0..1 jacket level from a right-double-click toggle (None = keep
    the model's default dress state)."""
    emotion, mouth_lvl = control.snapshot()
    keys = state["keys"]
    valid = state["valid"]
    blend = state["blend"]
    pose = EMOTIONS[emotion]

    # cross-fade toward the current pose; unused toggles fade to 0
    for k in keys:
        blend[k] = blend[k] * 0.82 + pose.get(k, 0.0) * 0.18

    # facial params (angles + mouth are written separately below)
    for k in keys:
        if k in ANGLE_KEYS or k in MOUTH_KEYS:
            continue
        model.SetParameterValue(k, blend[k])

    # blink on top of the emotion's resting eye openness (eyes never jump)
    blink_dur, blink_len = 170, 12
    k = f % blink_dur
    if k < blink_len and "ParamEyeLOpen" in valid and "ParamEyeROpen" in valid:
        t = k / blink_len
        pose_eye = blend.get("ParamEyeLOpen", 1.0)
        eye = pose_eye - (pose_eye - 0.05) * (2 * t if t < 0.5 else 2 * (1 - t))
        model.SetParameterValue("ParamEyeLOpen", eye)
        model.SetParameterValue("ParamEyeROpen", eye)

    # gentle sway on top of the emotion's head/body pose — feeds physics, so
    # hair/ears keep swinging (not written back into the cross-fade blend)
    sway = [("ParamAngleX", 3.0, 0.040, 0.0),
            ("ParamAngleY", 3.0, 0.027, 1.3),
            ("ParamAngleZ", 3.0, 0.050, 2.1),
            ("ParamBodyAngleX", 2.0, 0.030, 0.5),
            ("ParamBodyAngleY", 2.0, 0.024, 2.0),
            ("ParamBodyAngleZ", 1.0, 0.035, 3.0)]
    for pid, amp, rate, phase in sway:
        if pid in valid:
            model.SetParameterValue(
                pid, blend.get(pid, 0.0) + amp * math.sin(f * rate + phase))

    # mouth: while talking the audio energy drives the opening (open fast,
    # close slow); otherwise rest at the pose's own mouth opening
    base_open = pose.get("ParamMouthOpenY", 0.0)
    if mouth_lvl is None:
        tgt = base_open
    else:
        tgt = clamp(mouth_lvl * MOUTH_AMP[emotion] + base_open, 0.0, 1.0)
    m = state["mouth_cur"]
    m += (0.5 if tgt >= m else 0.25) * (tgt - m)
    state["mouth_cur"] = m
    if "ParamMouthOpenY" in valid:
        model.SetParameterValue("ParamMouthOpenY", m)
    if "ParamMouthForm" in valid:
        model.SetParameterValue("ParamMouthForm", blend.get("ParamMouthForm", 0.0))

    if idle_motion is not None and model.IsMotionFinished():
        model.StartMotion("Idle", 0, priority=1)
    if clothes is not None and "Param2" in valid:
        model.SetParameterValue("Param2", clothes)   # right-dbl-click jacket toggle
    model.Update()
    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)       # fully transparent background
    GL.glEnable(GL.GL_BLEND)
    model.Draw()
    win.present()
    glfw.swap_buffers(win.window)
    glfw.poll_events()


def render_frame(win, model, f, present_lookup, clothes=None):
    """Drive one animation frame; returns the alpha channel (HxW uint8).
    clothes: None = keep the demo's auto coat on/off, 0..1 = forced jacket
    level (right-double-click toggles it)."""
    blink_frames = [{"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0},
                    {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0},
                    {"ParamEyeLOpen": 0.05, "ParamEyeROpen": 0.05},
                    {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}]
    blink_dur = 60
    cycle = f % blink_dur
    t = cycle / blink_dur
    a = math.floor(t * len(blink_frames)) % len(blink_frames)
    b = (a + 1) % len(blink_frames)
    frac = (t * len(blink_frames)) % 1.0
    for pid in blink_frames[a]:
        v0 = blink_frames[a].get(pid, 1.0)
        v1 = blink_frames[b].get(pid, 1.0)
        model.SetParameterValue(pid, v0 + (v1 - v0) * frac)

    model.SetParameterValue("ParamMouthOpenY", 0.5 + 0.5 * math.sin(f * 0.30))
    model.SetParameterValue("ParamAngleZ", math.sin(f * 0.05) * 8.0)
    if "Param2" in present_lookup:               # coat on/off (auto sway or manual)
        if clothes is None:
            model.SetParameterValue("Param2", 0.5 + 0.5 * math.sin(f * 0.01))
        else:
            model.SetParameterValue("Param2", clothes)

    model.Update()
    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)       # fully transparent background
    GL.glEnable(GL.GL_BLEND)
    model.Draw()
    win.present()
    glfw.swap_buffers(win.window)
    glfw.poll_events()


def find_idle_motion(model_json):
    """Locate a motion to loop in --viewer mode (llny: motions/idel.motion3.json).
    Returns None if the model ships no motions/ directory."""
    d = os.path.dirname(os.path.abspath(model_json))
    motions_dir = os.path.join(d, "motions")
    if not os.path.isdir(motions_dir):
        return None
    preferred = os.path.join(motions_dir, "idel.motion3.json")
    if os.path.exists(preferred):
        return preferred
    for name in sorted(os.listdir(motions_dir)):
        if name.endswith(".motion3.json"):
            return os.path.join(motions_dir, name)
    return None


def viewer_frame(win, model, f, idle_motion=None, clothes=None, present_lookup=None):
    """One frame of the Live2DViewer-style idle.

    A regular blink plus a gentle multi-axis head/body sway are written from
    Python (Live2DViewer drives its idle the same way); physics, evaluated
    inside model.Update(), then swings the hair/ear/bow ArtMeshes — that is
    what makes the ears/hair visibly move. The model's own idle motion loops
    underneath (llny's motions/idel.motion3.json: 3s breath + subtle body).

    clothes: 0..1 jacket level from a right-double-click toggle (None = keep
    the model's default dress state).
    """
    # blink: eyes closed to 0.05 and open again, every ~2.8 s
    blink_dur, blink_len = 170, 12
    k = f % blink_dur
    if k < blink_len:
        t = k / blink_len
        eye = 1.0 - 0.95 * (2 * t if t < 0.5 else 2 * (1 - t))
    else:
        eye = 1.0
    model.SetParameterValue("ParamEyeLOpen", eye)
    model.SetParameterValue("ParamEyeROpen", eye)

    # multi-axis sway — the physics inputs that drive hair/ear movement
    model.SetParameterValue("ParamAngleX", 10.0 * math.sin(f * 0.040))
    model.SetParameterValue("ParamAngleY", 8.0 * math.sin(f * 0.027 + 1.3))
    model.SetParameterValue("ParamAngleZ", 6.0 * math.sin(f * 0.050 + 2.1))
    model.SetParameterValue("ParamBodyAngleX", 4.0 * math.sin(f * 0.030 + 0.5))
    model.SetParameterValue("ParamBodyAngleY", 3.0 * math.sin(f * 0.024 + 2.0))
    model.SetParameterValue("ParamBodyAngleZ", 1.5 * math.sin(f * 0.035 + 3.0))

    if idle_motion is not None and model.IsMotionFinished():
        model.StartMotion("Idle", 0, priority=1)
    if clothes is not None and present_lookup is not None and "Param2" in present_lookup:
        model.SetParameterValue("Param2", clothes)   # right-dbl-click jacket toggle
    model.Update()
    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)       # fully transparent background
    GL.glEnable(GL.GL_BLEND)
    model.Draw()
    win.present()
    glfw.swap_buffers(win.window)
    glfw.poll_events()


# --------------------------------------------------------------------------
# TCP control (external pipeline: switch emotion / force the mouth anytime)
# --------------------------------------------------------------------------

class _ControlServer(socketserver.ThreadingTCPServer):
    """allow_reuse_address must be in effect before bind, so it lives on the
    class (not an instance)."""
    allow_reuse_address = True
    daemon_threads = True


def make_control_handler(control):
    """A StreamRequestHandler that reads one JSON per line:
    {"emotion":"开心"} / {"mouth":0.7} / {"mouth":null} (release the mouth)."""
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                try:
                    msg = json.loads(line.decode("utf-8"))
                except Exception as exc:
                    print(f"control: bad JSON {line!r}: {exc}")
                    continue
                if not isinstance(msg, dict):
                    continue
                if isinstance(msg.get("emotion"), str):
                    if control.set_emotion(msg["emotion"]):
                        print(f"control: emotion -> {msg['emotion']}")
                    else:
                        print(f"control: unknown emotion {msg['emotion']!r}")
                if "mouth" in msg:
                    control.set_mouth(msg["mouth"])
                    print(f"control: mouth -> {msg['mouth']}")
    return Handler


def start_control_server(port, control):
    """Listen on 127.0.0.1:port in a daemon thread; returns the server object
    so the caller can shutdown() it on exit."""
    server = _ControlServer(("127.0.0.1", port), make_control_handler(control))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"control server listening on 127.0.0.1:{port} — one JSON per line: "
          '{"emotion":"开心"}, {"mouth":0.7}, {"mouth":null}')
    return server


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--width", type=int, default=None,
                    help="window width in physical pixels (default: W=520 "
                         "scaled by the monitor DPI, so the pet is a "
                         "consistent size on high-DPI screens)")
    ap.add_argument("--height", type=int, default=None,
                    help="window height in physical pixels (default: H=720 "
                         "scaled by the monitor DPI)")
    ap.add_argument("--x", type=int, default=None, help="window left (default top-right)")
    ap.add_argument("--y", type=int, default=None, help="window top (default top-right)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="character scale (1.0 = fitted to the window). Larger "
                         "values zoom in, but the pet already fills the window "
                         "height so the head clips as scale grows — enlarge the "
                         "window (--width/--height) to show a physically bigger "
                         "pet. +/-/0 keys resize it live.")
    ap.add_argument("--viewer", action="store_true",
                    help="Live2DViewer-style idle: regular blink + multi-axis "
                         "head/body sway drive the physics (hair/ears/bows "
                         "visibly swing) and the model's own idle motion loops, "
                         "instead of the procedural demo animation")
    ap.add_argument("--emotion", metavar="NAME", default=None,
                    help="start in this emotion (" + "、".join(EMOTIONS) +
                         "); enables express mode")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--lipsync", metavar="WAV", default=None,
                     help="loop this wav and sync the mouth to its energy "
                          "(express mode)")
    src.add_argument("--listen", action="store_true",
                     help="capture the system's output audio (WASAPI loopback) "
                          "and sync the mouth to whatever is playing — no file "
                          "needed, works with any audio player")
    ap.add_argument("--lipsync-device", type=int, default=None,
                    help="sounddevice output index for --lipsync (default: "
                         "system default; list devices with "
                         "`python -c \"import sounddevice as sd; print(sd.query_devices())\"`)")
    ap.add_argument("--listen-device", type=int, default=None,
                    help="pyaudiowpatch loopback device index for --listen "
                         "(default: first [Loopback] device; list with "
                         "`python -c \"from system_listen import list_loopback_devices; list_loopback_devices()\"`)")
    ap.add_argument("--control-port", type=int, default=None,
                    help="listen on 127.0.0.1:PORT for JSON control lines "
                         '(e.g. {"emotion":"开心"}, {"mouth":0.7}, {"mouth":null})')
    ap.add_argument("--click-through", action="store_true",
                    help="let mouse clicks pass through the window (ESC then "
                         "may not work; Alt+F4 or task manager to quit)")
    ap.add_argument("--self-test", action="store_true",
                    help="render one transparent frame, print alpha stats, save "
                         "pet_preview.png, then exit")
    args = ap.parse_args()

    glfw.init()
    if args.width is None or args.height is None:
        dw, dh = _default_window_size()      # DPI-scaled default window
        if args.width is None:
            args.width = dw
        if args.height is None:
            args.height = dh
    win = LayeredWindow(args.width, args.height, args.x, args.y,
                        click_through=args.click_through)
    control_server = None
    try:
        live2d.init()
        live2d.glInit()
        model = live2d.LAppModel()
        model.LoadModelJson(os.path.abspath(args.model))
        # C++ SDK auto-blink/breath would overwrite our params; disable them
        # in both modes. --viewer drives its own blink/sway (Live2DViewer's
        # idle is procedural too) and relies on model.Update() only for the
        # motion, physics and expression layers.
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        model.Resize(args.width, args.height)
        scale = args.scale                       # absolute factor (scale ~= fit window)
        model.SetScale(scale)

        present_lookup = param_lookup(model, ["Param14", "Param2"])
        if "Param14" in present_lookup:          # llny: remove watermark
            model.SetParameterValue("Param14", 1.0)

        # express mode: emotion poses + optional lipsync/TCP on top of the
        # viewer-style idle
        express = (args.emotion is not None or args.lipsync or args.listen
                   or args.control_port)
        emotion = args.emotion or "平和"
        if emotion not in EMOTIONS:
            print(f"unknown emotion {emotion!r}; falling back to 平和")
            emotion = "平和"
        control = PetControl(emotion)
        estate = new_emotion_state(model, emotion)

        def _toggle_clothes():                      # right-double-click on the pet
            dressed = control.toggle_clothes()
            print("jacket: " + ("on" if dressed else "off"))
        win.clothes_cb = _toggle_clothes

        idle_motion = None
        if args.viewer or express:
            idle_motion = find_idle_motion(args.model)
            if idle_motion is not None:
                model.LoadExtraMotion("Idle", idle_motion)
                model.StartMotion("Idle", 0, priority=1)
                print("looping idle motion " + os.path.basename(idle_motion))
            else:
                print("no idle motion found; blink/sway/physics only")

        if express:
            express_frame(win, model, 0, control, idle_motion, estate, clothes=1.0)
        elif args.viewer:
            viewer_frame(win, model, 0, idle_motion, clothes=1.0,
                         present_lookup=present_lookup)
        else:
            render_frame(win, model, 0, present_lookup)   # clothes None = auto sway

        if args.self_test:
            raw = win.last_rgba
            al = raw[:, :, 3].astype(int)
            h, w = al.shape
            corner = np.concatenate([al[:10, :10].ravel(), al[:10, -10:].ravel(),
                                     al[-10:, :10].ravel(), al[-10:, -10:].ravel()])
            print(f"size {w}x{h}")
            print(f"corner alpha mean = {corner.mean():.3f}  (0=transparent)")
            print(f"frame alpha mean  = {al.mean():.3f}")
            print(f"opaque px (a>250) = {(al > 250).mean() * 100:.2f}%")
            Image.fromarray(raw, "RGBA").transpose(
                Image.FLIP_TOP_BOTTOM).save(os.path.join(HERE, "pet_preview.png"))
            print("saved pet_preview.png")
            return

        # background services (after --self-test so the smoke test is clean):
        # lipsync / listen feed RMS energy into control.mouth; the TCP server
        # lets an external pipeline switch emotion / mouth.
        if args.listen:
            def _listen_energy(rms01):
                control.set_mouth(rms01)
            threading.Thread(
                target=listen_system_output,
                args=(_listen_energy,),
                kwargs={"device_index": args.listen_device},
                daemon=True).start()
        if args.lipsync:
            def _lip_energy(rms01):
                control.set_mouth(rms01)

            def _lip_done():
                control.set_mouth(None)

            def _lip_loop():
                while True:
                    play_wav_with_energy(args.lipsync, _lip_energy, _lip_done,
                                         device=args.lipsync_device)
                    time.sleep(0.05)          # avoid a tight retry loop if playback fails
            threading.Thread(target=_lip_loop, daemon=True).start()
            print("lipsync: looping " + args.lipsync)
        if args.control_port:
            control_server = start_control_server(args.control_port, control)

        print("transparent pet running — press ESC to quit, +/- to resize, "
              "0 to reset; right-DOUBLE-click the pet to take the jacket "
              "off/on (Alt+F4 works too)")
        f = 0
        prev_keys = {}
        clothes_level = 1.0               # animated jacket level (0 = off, 1 = on)
        while True:
            if glfw.window_should_close(win.window) or \
               glfw.get_key(win.window, glfw.KEY_ESCAPE) == glfw.PRESS:
                break

            # live resize: edge-triggered + / - / 0 (window needs focus first)
            pressed = {}
            for key in (glfw.KEY_EQUAL, glfw.KEY_KP_ADD, glfw.KEY_MINUS,
                        glfw.KEY_KP_SUBTRACT, glfw.KEY_0):
                now = glfw.get_key(win.window, key) == glfw.PRESS
                pressed[key] = now and not prev_keys.get(key, False)
            prev_keys = {k: glfw.get_key(win.window, k) == glfw.PRESS
                         for k in (glfw.KEY_EQUAL, glfw.KEY_KP_ADD,
                                   glfw.KEY_MINUS, glfw.KEY_KP_SUBTRACT, glfw.KEY_0)}
            if pressed.get(glfw.KEY_EQUAL) or pressed.get(glfw.KEY_KP_ADD):
                scale = min(10.0, scale * 1.15)
                model.SetScale(scale)
                print(f"scale = {scale:.2f}")
            if pressed.get(glfw.KEY_MINUS) or pressed.get(glfw.KEY_KP_SUBTRACT):
                scale = max(0.1, scale / 1.15)
                model.SetScale(scale)
                print(f"scale = {scale:.2f}")
            if pressed.get(glfw.KEY_0):
                scale = args.scale
                model.SetScale(scale)
                print(f"scale reset = {scale:.2f}")

            # jacket: follow the manual toggle (None = the demo keeps its auto
            # coat on/off). Animating the level gives a smooth take-off / put-on.
            c = control.clothes
            if c is None and not express and not args.viewer:
                clothes_value = None
            else:
                target = 1.0 if c is not False else 0.0
                clothes_level += (target - clothes_level) * 0.06
                clothes_value = clothes_level

            if express:
                express_frame(win, model, f, control, idle_motion, estate,
                              clothes=clothes_value)
            elif args.viewer:
                viewer_frame(win, model, f, idle_motion, clothes=clothes_value,
                             present_lookup=present_lookup)
            else:
                render_frame(win, model, f, present_lookup, clothes=clothes_value)
            f += 1
    finally:
        if control_server is not None:
            control_server.shutdown()
            control_server.server_close()
        live2d.dispose()
        win.close()
        glfw.terminate()


if __name__ == "__main__":
    main()
