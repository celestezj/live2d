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
import random
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
        self._last_click = {"L": (0.0, None),     # (glfw time, (x, y)) per button
                            "R": (0.0, None)}     #   for double-click detection
        self._pending = {}                        # key -> (deadline, on_single)
        self.clothes_cb = None                    # called on right-double-click (jacket)
        self.lock_cb = None                       # called on right-single-click (position)
        self.locked = False                       # right-single-click toggles: locked = no drag
        self.tug = (0.0, 0.0)                     # applied tug, smoothed toward tug_target
        self.tug_target = (0.0, 0.0)              # mouse writes this while tugging (locked)
        self._tug_anchor = None                   # (x, y) where the tug press started
        self.tug_zone = "body"                    # "head" | "body" (from the grab point)
        self.scale = 1.0                          # live model scale (the + / - / 0
                                                  # keys change it); the click-zone
                                                  # thresholds are derived from it
                                                  # in _zone_y, so they follow the
                                                  # scaled character on screen
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

    def set_scale(self, s):
        """Keep the click zones in sync with the model scale. main() calls this
        whenever the + / - / 0 keys change it (probed: the character is scaled
        around the window centre, so its parts move as scale changes)."""
        self.scale = s

    def _zone_y(self, canvas_frac):
        """Window y (content coords, y down) of a body-part boundary. The llny
        canvas (1300x1800) is scaled to fit the window by height and centred, so
        a canvas-height-fraction lands at h * (0.5 + (cf - 0.5) * scale) — the
        fixed HEAD_ZONE/LEG_ZONE fractions only match the window at scale 1.0."""
        return self.h * (0.5 + (canvas_frac - 0.5) * self.scale)

    def _on_mouse_button(self, window, button, action, mods):
        if action == glfw.PRESS:
            x, y = glfw.get_cursor_pos(window)
            if button == glfw.MOUSE_BUTTON_RIGHT:
                # right-single-click locks/unlocks the position; a right
                # double-click takes the jacket off/on instead (the pair is
                # consumed by the double-click, so the lock never fires).
                self._maybe_double_or_single(
                    "R", x, y, self._on_jacket_double_click,
                    self._on_lock_single_click)
                return
            if button != glfw.MOUSE_BUTTON_LEFT:
                return
            if not self._hit_character(x, y):
                return                       # ignore clicks on transparent pixels
            now = glfw.get_time()
            t0, p0 = self._last_click["L"]   # left double-click on the body
            dbl = ((now - t0) < 0.5 and p0 is not None
                   and abs(x - p0[0]) < 24 and abs(y - p0[1]) < 24)
            self._last_click["L"] = ((0.0, None) if dbl else (now, (x, y)))
            if dbl and self.locked and self._zone_y(DBL_ZONE_TOP) <= y < self._zone_y(DBL_ZONE_BOTTOM):
                self._drag = None                 # while locked, double-click on
                self._tug_anchor = None           # the skirt / private area or
                self.tug_zone = "legs"            # the thighs (a band that tracks
                self.tug = SHY_IMPULSE            # the scaled character, so it
                self.tug_target = SHY_IMPULSE     # stays put whatever the scale;
                                                  # the chest above and the calves
                                                  # below don't count): the same
                                                  # shy + legs-together as a
                                                  # thigh drag, but instant —
                                                  # holds while pressed, then
                                                  # eases out on release
                                                  # (tug_zone stays frozen in
                                                  # the decay)
                return
            if self.locked:
                self._tug_anchor = (x, y)    # locked: tug the pet in place
                self.tug_zone = ("head" if y < self._zone_y(HEAD_ZONE) else
                                 "legs" if y >= self._zone_y(LEG_ZONE) else "body")
                self.tug_target = (0.0, 0.0)
                return
            wx, wy = glfw.get_window_pos(window)
            self._drag = (wx, wy, x, y)
        elif button == glfw.MOUSE_BUTTON_LEFT and action == glfw.RELEASE:
            self._drag = None
            self._tug_anchor = None          # release: pet glides back to the
            self.tug_target = (0.0, 0.0)     # normal pose. Leave tug_zone frozen
                                             # (set at press) so the decaying tug
                                             # retraces the SAME params; resetting
                                             # it here made the remaining pull hit
                                             # the other zone's params mid-decay
                                             # and the pose snapped to front.

    def _maybe_double_or_single(self, key, x, y, on_double, on_single):
        """Handle a click that is either the first half of a double-click or a
        standalone single click. A second click within ~0.5s (and <24px from
        the previous one) fires on_double() immediately and cancels the pending
        single; otherwise the press is remembered and on_single() fires after
        SINGLE_CLICK_DELAY s (checked once per frame) if no second click turns
        it into a double-click."""
        if not self._hit_character(x, y):
            return False
        now = glfw.get_time()
        t0, p0 = self._last_click[key]
        if (now - t0) < 0.5 and p0 is not None and \
                abs(x - p0[0]) < 24 and abs(y - p0[1]) < 24:
            self._last_click[key] = (0.0, None)      # consumed the pair
            self._pending.pop(key, None)             # cancel the pending single
            on_double()
            return True
        # not a double-click: a still-pending single was a real one (no
        # qualifying second click arrived) — fire it now, then start a new one.
        pending = self._pending.pop(key, None)
        if pending is not None:
            pending[1]()
        self._last_click[key] = (now, (x, y))
        self._pending[key] = (now + SINGLE_CLICK_DELAY, on_single)
        return False

    def _fire_pending_click(self):
        """Fire single-click actions whose confirm window has elapsed. Called
        once per frame: a lone click still works, but the double-click detector
        gets its short wait to make sure no second click is coming."""
        now = glfw.get_time()
        for key in list(self._pending):
            deadline, on_single = self._pending[key]
            if now >= deadline:
                del self._pending[key]
                on_single()

    def _on_jacket_double_click(self):
        if self.clothes_cb is not None:
            self.clothes_cb()

    def _on_lock_single_click(self):
        self.locked = not self.locked
        if self.lock_cb is not None:
            self.lock_cb(self.locked)

    def _on_cursor_pos(self, window, x, y):
        if self._tug_anchor is not None:         # locked: react to the pull
            x0, y0 = self._tug_anchor
            # a ~15% width / 12% height drag already reaches the full reaction
            tx = max(-1.0, min(1.0, (x - x0) / (self.w * 0.15)))
            ty = max(-1.0, min(1.0, (y - y0) / (self.h * 0.12)))
            self.tug_target = (tx, ty)
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
# body angle [-10,10].
# NOTE (probed 2026-09-01): ParamEyeLSmile/RSmile(笑眼)、Param26(泪)、
# Param59/60(撅嘴)、ParamCheek(LipOpen) 在 llny 里不渲染(死参数)，勿用。
# Param13 脸红 / Param58 鼓脸 近不可见；Param7 黑脸是 0/1 阈值开关(0.6 不可见)。
# 可用 overlay：Param7(=1 全黑脸)、Param9 星星眼、Param11 生气、Param12 哭、
# Param61 Shrug(嘴角)、Param62 Jaw。Param51 Mouth X 侧移嘴也强。
EMOTIONS = {
    "平和": {"ParamMouthForm": 0.1, "ParamEyeLOpen": 0.9,
             "ParamEyeROpen": 0.9},
    "开心": {"ParamMouthForm": 0.7, "ParamMouthOpenY": 0.15,
             "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
             "ParamBrowLY": 0.15, "ParamBrowRY": 0.15,
             "ParamAngleZ": 4, "ParamAngleY": 3},
    "兴奋": {"ParamMouthForm": 0.8, "ParamMouthOpenY": 0.35,
             "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
             "ParamBrowLY": 0.3, "ParamBrowRY": 0.3,
             "ParamEyeBallX": 0.1, "ParamEyeBallY": -0.15,
             "ParamAngleX": -4, "ParamAngleZ": 2, "ParamAngleY": 5,
             "ParamBodyAngleY": 3},
    "惊喜": {"ParamMouthOpenY": 0.55, "ParamEyeLOpen": 1.0,
             "ParamEyeROpen": 1.0, "ParamBrowLY": 0.4, "ParamBrowRY": 0.4,
             "Param62": 0.3, "ParamAngleZ": -4, "ParamBodyAngleX": -3,
             "ParamAngleY": 6},
    "温柔": {"ParamMouthForm": 0.35, "ParamEyeLOpen": 0.85,
             "ParamEyeROpen": 0.85, "ParamBrowLY": -0.1, "ParamBrowRY": -0.1,
             "ParamAngleZ": 3, "Param13": 1.0, "Param28": -8,
             "ParamEyeBallY": -0.2, "ParamAngleY": -3},
    "关切": {"ParamBrowLAngle": -0.3, "ParamBrowRAngle": 0.3,
             "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7,
             "ParamEyeBallX": 0.3, "ParamMouthForm": -0.25,
             "ParamMouthOpenY": 0.06, "ParamAngleZ": 3,
             "ParamAngleX": 4, "ParamAngleY": -2},
    "好奇": {"ParamAngleZ": 22, "ParamEyeBallX": 0.3, "ParamEyeBallY": 0.1,
             "ParamBrowLY": 0.2, "ParamBrowRY": 0.05, "ParamMouthOpenY": 0.15,
             "ParamMouthForm": 0.1, "ParamEyeLOpen": 0.95,
             "ParamEyeROpen": 0.95},
    "期待": {"ParamBrowLY": 0.2, "ParamBrowRY": 0.2, "ParamEyeLOpen": 0.95,
             "ParamEyeROpen": 0.95, "ParamMouthForm": 0.3,
             "ParamMouthOpenY": 0.1, "ParamAngleY": -5, "ParamAngleZ": 2,
             "ParamEyeBallY": -0.1, "Param9": 1.0},
    "无奈": {"ParamBrowLY": -0.18, "ParamBrowRY": -0.18, "ParamEyeLOpen": 0.62,
             "ParamEyeROpen": 0.62, "ParamEyeBallY": -0.1, "ParamMouthForm": -0.4,
             "ParamMouthOpenY": 0.06, "ParamAngleY": -5, "ParamAngleZ": 2},
    "失望": {"ParamBrowLY": -0.25, "ParamBrowRY": -0.25, "ParamMouthForm": -0.35,
             "ParamEyeLOpen": 0.75, "ParamEyeROpen": 0.75, "ParamEyeBallY": -0.1,
             "ParamAngleX": 4, "ParamBodyAngleX": 3},
    "沮丧": {"ParamBrowLY": -0.3, "ParamBrowRY": -0.3, "ParamBrowLAngle": -0.15,
             "ParamBrowRAngle": 0.15, "ParamMouthForm": -0.5,
             "ParamEyeLOpen": 0.55, "ParamEyeROpen": 0.55, "ParamEyeBallY": -0.2,
             "Param7": 1.0, "ParamAngleX": 5, "ParamBodyAngleX": 4,
             "ParamAngleY": -7, "ParamBodyAngleY": -3},
    "难过": {"ParamBrowLY": -0.2, "ParamBrowRY": -0.2, "ParamBrowLAngle": 0.4,
             "ParamBrowRAngle": -0.4, "ParamMouthForm": -0.5,
             "ParamMouthOpenY": 0.2, "ParamEyeLOpen": 0.85,
             "ParamEyeROpen": 0.85, "ParamEyeBallY": -0.25, "Param12": 1.0,
             "ParamAngleX": 4, "ParamBodyAngleX": 2, "ParamAngleY": -4,
             "ParamBodyAngleY": -2},
    "担心": {"ParamBrowLY": 0.4, "ParamBrowRY": 0.4, "ParamBrowLAngle": -0.5,
             "ParamBrowRAngle": 0.5, "ParamMouthForm": -0.3,
             "ParamMouthOpenY": 0.15, "ParamEyeLOpen": 0.85,
             "ParamEyeROpen": 0.85, "ParamEyeBallX": 0.2, "ParamEyeBallY": 0.1,
             "ParamAngleZ": 6},
    "不满": {"ParamBrowLY": -0.35, "ParamBrowRY": -0.35, "ParamBrowLAngle": -0.4,
             "ParamBrowRAngle": 0.4, "ParamMouthForm": -1.0,
             "ParamEyeLOpen": 0.75, "ParamEyeROpen": 0.75, "ParamAngleZ": 3,
             "ParamAngleX": -3},
    "生气": {"ParamBrowLY": -0.4, "ParamBrowRY": -0.4, "ParamBrowLAngle": -0.5,
             "ParamBrowRAngle": 0.5, "ParamMouthForm": -0.3,
             "ParamEyeLOpen": 0.65, "ParamEyeROpen": 0.65, "ParamEyeBallY": -0.3,
             "Param11": 1.0, "Param61": 1.0, "ParamAngleY": 15,
             "ParamAngleX": 4, "ParamBodyAngleX": 4},
    "愤怒": {"ParamBrowLY": -0.7, "ParamBrowRY": -0.7, "ParamBrowLAngle": -0.9,
             "ParamBrowRAngle": 0.9, "ParamMouthForm": -0.75,
             "ParamMouthOpenY": 0.06, "ParamEyeLOpen": 0.8,
             "ParamEyeROpen": 0.8, "ParamEyeBallY": 0.9, "Param11": 1.0,
             "ParamAngleY": -18, "ParamAngleZ": -10},
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
        self.clothes = True               # True = dressed, False = undressed
                                          # (llny Param2 "去外套": 0 = wearing, 1 = removed)

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
            "mouth_cur": pose.get("ParamMouthOpenY", 0.0),
            # random body actions (only when main() turns random_actions on)
            "gesture": None,          # current action or None
            "gesture_at": 0.0,        # glfw time when the next action may start
            "random_actions": False}


# Tug amplitudes (deg). Yaw/pitch ranges: head ±30, body ±10. llny's angle rig
# is unlabelled: AX is the yaw axis, AY the pitch axis (probed and confirmed
# against live drags and the model's own PRPRLive mapping). ParamBodyAngleX is
# the torso turn (chest sweeps, feet anchored — same sign as AX, the body
# follows the head), ParamBodyAngleY is a vertical stretch/squash (hgt 705 vs
# 678 at ±10), ParamBodyAngleZ sways hips with the chest counter-swinging. So a
# body tug drives head + torso turn together, and the vertical tug crouches.
SINGLE_CLICK_DELAY = 0.5          # wait this long (>= the 0.5s double-click
                                  # window) before a lone right-click locks
HEAD_ZONE = 0.40                  # canvas fraction: grab point below this (in the
                                  # model's own height) is the body, above is the
                                  # head. The on-screen threshold is scale-aware:
                                  # see LayeredWindow._zone_y
HEAD_TURN_AMP = 25.0              # head turns toward the drag (ParamAngleX, yaw)
HEAD_TUG_AMP = 20.0               # head looks up/down with the drag (ParamAngleY, pitch)
BODY_TURN_AMP = 15.0              # head leads the body turn toward the drag (ParamAngleX)
BODY_YAW_AMP = 10.0               # torso turns toward the drag (ParamBodyAngleX — the
                                  # real body-turn param, probed: chest sweeps sideways
                                  # while the feet stay anchored; llny names are swapped
                                  # vs Cubism, X=twist not lean). Full drag = full range.
BODY_TUG_AMP = 5.0                # vertical body drag: crouch / stretch (ParamBodyAngleY,
                                  # probed: + = lower/shorter, - = taller)
LEG_ZONE = 0.55                   # canvas fraction: grab point at/above this (in
                                  # the model's own height) is the legs (covers the
                                  # thigh root, not just the knees)
DBL_ZONE_TOP = 0.45               # canvas fraction: the left-double-click shy band
                                  # starts here — the skirt / private area just
                                  # below the chest (probed: chest ends ~0.53;
                                  # nudged up 2% per user, x4)
DBL_ZONE_BOTTOM = 0.67            # ... and ends just above the calves / knee
                                  # (probed: the leg tapers sharply from ~0.76;
                                  # calves and below don't trigger)
LEG_CLOSE_AMP = 9.0               # Param28 "yy": probed — negative squeezes the legs
                                  # together (the two leg columns visibly merge)
SHY_SQUAT_AMP = 7.0               # ParamBodyAngleY -: whole body lowers a touch
                                  # (a slight squat; probed: - = shorter)
SHY_ARM_AMP = 15.0                # arms tuck inward (Param41 + / Param43 -). llny's
                                  # rig only swings the arms at the sides — the hands
                                  # cannot cross over the crotch, this is the shy
                                  # defensive tuck it can do.
SHY_BLUSH_AMP = 0.85              # Param13 脸红 (very shy blush)
SHY_PUFF_AMP = 0.6                # Param58 鼓脸 (puffed cheeks)
SHY_GAZE_DOWN = 0.35              # eye balls cast down (probed: ParamEyeBallY - = down)
SHY_GAZE_SIDE = 0.25              # averted gaze (probed: ParamEyeBallX + = right)
SHY_LID = 0.25                    # half-lidded bashful eyes (ParamEyeLOpen/R -)
SHY_LOOKDOWN = 5.0                # head pitches down a touch (ParamAngleY -)
SHY_MOUTH = 0.3                   # soft bashful smile (ParamMouthForm +)
SHY_IMPULSE = (0.8, 0.6)          # left-double-click on the skirt / private area
                                  # or the thighs (the DBL_ZONE band — the chest
                                  # above and the calves below don't fire): a pull
                                  # vector that ramps the legs-zone shy reaction
                                  # to full (hypot = 1.0), holds while pressed,
                                  # then eases out on release
TUG_ATTACK_RATE = 0.15            # follow the mouse pull while held (per-frame)
TUG_RELEASE_RATE = 0.07           # glide back to (0,0) on release: slower, so the
                                  # pose eases home instead of snapping


def _add_param_delta(model, pid, delta):
    """Add delta to a model parameter (guarded; skips missing params)."""
    try:
        idx = model.GetParamIds().index(pid)
    except ValueError:
        return
    model.SetParameterValue(pid, model.GetParameterValue(idx) + delta)


def _apply_tug(model, valid, tug, zone="body"):
    """React to a mouse tug while the pet's position is locked. Where you grab
    decides what responds:
    - head zone: a horizontal pull turns the head toward it (ParamAngleX —
      llny's AX is the yaw axis, + = toward the viewer's right), a vertical pull
      looks the head up/down (ParamAngleY, + = look up);
    - body zone: a horizontal pull turns the body toward it — the head leads
      (ParamAngleX) and the torso twists along it (ParamBodyAngleX, probed: the
      chest sweeps sideways while the feet stay anchored; llny's body-angle
      names are swapped vs Cubism, so X is the twist, not the lean); a vertical
      pull crouches / stretches her (ParamBodyAngleY, + = lower).
    - legs zone: any pull makes her shy — a slight squat (ParamBodyAngleY -),
      the thighs squeezed together (Param28 "yy" goes negative, probed: it closes
      the two leg columns, unlike Param27 which is a whole-body turn), the arms
      tucked inward (Param41 + / Param43 -), plus a bashful face: blush, puffed
      cheeks, downcast+averted eyes, half-lidded.
    Values are additive on top of the pose; the main loop decays `tug` to
    (0,0) on release so the pet returns to its normal pose."""
    if not valid or not tug or not any(tug):
        return 0.0
    tx, ty = tug
    if zone == "head":
        if tx and "ParamAngleX" in valid:
            _add_param_delta(model, "ParamAngleX", tx * HEAD_TURN_AMP)
        if ty and "ParamAngleY" in valid:
            _add_param_delta(model, "ParamAngleY", -ty * HEAD_TUG_AMP)
        return 0.0
    elif zone == "legs":                        # shy: squat + thighs together +
        s = min(1.0, math.hypot(tx, ty))        # bashful face + arms tucked inward
        if "Param28" in valid:                  # Param28 "yy": negative closes legs
            _add_param_delta(model, "Param28", -LEG_CLOSE_AMP * s)
        if "ParamBodyAngleY" in valid:          # slight squat: whole body lowers
            _add_param_delta(model, "ParamBodyAngleY", -SHY_SQUAT_AMP * s)
        if "Param41" in valid:                  # arms tuck toward the front (the rig
            _add_param_delta(model, "Param41", SHY_ARM_AMP * s)      # only swings at
        if "Param43" in valid:                  # the sides — can't cross the crotch)
            _add_param_delta(model, "Param43", -SHY_ARM_AMP * s)
        if "Param13" in valid:                  # 脸红 blush
            _add_param_delta(model, "Param13", SHY_BLUSH_AMP * s)
        if "Param58" in valid:                  # 鼓脸 puffed cheeks
            _add_param_delta(model, "Param58", SHY_PUFF_AMP * s)
        if "ParamEyeBallY" in valid:            # eyes cast down (probed: - = down)
            _add_param_delta(model, "ParamEyeBallY", -SHY_GAZE_DOWN * s)
        if "ParamEyeBallX" in valid:            # avert the gaze (probed: + = right)
            _add_param_delta(model, "ParamEyeBallX", SHY_GAZE_SIDE * s)
        if "ParamEyeLOpen" in valid:            # half-lidded, bashful
            _add_param_delta(model, "ParamEyeLOpen", -SHY_LID * s)
        if "ParamEyeROpen" in valid:
            _add_param_delta(model, "ParamEyeROpen", -SHY_LID * s)
        if "ParamAngleY" in valid:              # head casts down a touch
            _add_param_delta(model, "ParamAngleY", -SHY_LOOKDOWN * s)
        if "ParamMouthForm" in valid:           # soft bashful smile
            _add_param_delta(model, "ParamMouthForm", SHY_MOUTH * s)
        return -LEG_CLOSE_AMP * s if "Param28" in valid else 0.0
    else:                                       # body: head + torso turn together
        if tx and "ParamAngleX" in valid:
            _add_param_delta(model, "ParamAngleX", tx * BODY_TURN_AMP)
        if tx and "ParamBodyAngleX" in valid:
            _add_param_delta(model, "ParamBodyAngleX", tx * BODY_YAW_AMP)
        if ty and "ParamBodyAngleY" in valid:
            _add_param_delta(model, "ParamBodyAngleY", ty * BODY_TUG_AMP)
        return 0.0


# --- random body actions (express mode, only with --random-actions) ---------
ACTIVE_ACTION_EMOTIONS = ("兴奋", "开心", "惊喜")   # lively emotions get actions
# seconds between actions (TIME-based, not frame-based: the loop's actual fps is
# unpredictable with layered windows + audio capture, so frame counts would make
# the gap stretch out on slow machines — glfw.get_time() keeps the rhythm steady)
GESTURE_IDLE_MIN_S, GESTURE_IDLE_MAX_S = 1.5, 4.5  # between actions
GESTURE_FIRST_MIN_S, GESTURE_FIRST_MAX_S = 1.0, 2.5  # before the very first one
TUG_SETTLE_EPS = 0.05              # a pull this small (~<0.5°) counts as settled:
                                   # win.tug decays asymptotically toward (0,0) and
                                   # never reaches exact zero, so gating on `any`
                                   # would block gestures forever after a drag
# each action: (frames, [(curve, param, amp, cycles)]), curve = swing | pulse.
# swing = amp*sin(2π*cycles*t) (full round-trips), pulse = amp*sin(π*t) (one
# bump); both are 0 at t=0 and t=1, so the pose always returns home. Amps are
# on the model's own ranges (head/body ±30/±10, arms ±30).
GESTURES = {
    "sway":   (80, [("swing", "ParamBodyAngleZ", 5.0, 1),
                    ("swing", "ParamAngleZ", 3.0, 1)]),
    "twist":  (90, [("swing", "ParamBodyAngleX", 7.0, 2)]),
    "hop":    (45, [("pulse", "ParamBodyAngleY", -6.0, 1),
                    ("pulse", "ParamAngleY", -4.0, 1)]),
    "waveL":  (75, [("swing", "Param41", -16.0, 2),    # left arm swings out
                    ("swing", "ParamAngleZ", 4.0, 2)]),
    "waveR":  (75, [("swing", "Param43", 16.0, 2),     # right arm swings out
                    ("swing", "ParamAngleZ", -4.0, 2)]),
    "arms":   (60, [("swing", "Param41", -12.0, 1),    # both arms out and back
                    ("swing", "Param43", 12.0, 1)]),
    "shake":  (60, [("swing", "ParamAngleX", 8.0, 3)]),
}


def _random_gesture_frame(model, state, emotion, valid, tug):
    """One step of the random body actions. Only runs when main() turned
    state["random_actions"] on (the --random-actions flag); otherwise this is a
    no-op and the express look is exactly the original. Active only for the
    lively emotions and while the locked-drag tug is idle. After a random
    1.5-4.5 s wait picks a gesture at random (with a random amplitude), plays it
    by ADDING the curve values on top of the pose/sway, then idles again — the
    curves all return to 0, so the pet eases back to its normal pose."""
    if not state.get("random_actions"):
        return
    g = state.get("gesture")
    if g is None:
        if emotion not in ACTIVE_ACTION_EMOTIONS:
            return
        if tug is not None and (abs(tug[0]) > TUG_SETTLE_EPS or
                                abs(tug[1]) > TUG_SETTLE_EPS):
            # actively pulled, or still easing back from a pull: wait a fresh
            # full interval so we don't interrupt. Gate on MAGNITUDE, not `any`:
            # the release decay is asymptotic, so a residual 1e-9 would otherwise
            # count as "dragging" and block actions forever.
            state["gesture_at"] = glfw.get_time() + random.uniform(
                GESTURE_IDLE_MIN_S, GESTURE_IDLE_MAX_S)   # fresh full interval
            return
        if glfw.get_time() < state["gesture_at"]:
            return
        gid = random.choice(list(GESTURES))
        dur, _ = GESTURES[gid]
        state["gesture"] = {"id": gid, "f": 0, "dur": dur,
                            "amp": random.uniform(0.7, 1.3)}
        state["gesture_at"] = glfw.get_time() + random.uniform(
            GESTURE_IDLE_MIN_S, GESTURE_IDLE_MAX_S)
        return
    gf = g["f"]; t = gf / g["dur"]
    for curve, pid, amp, cycles in GESTURES[g["id"]][1]:
        if pid not in valid:
            continue
        v = (amp * math.sin(2 * math.pi * cycles * t) if curve == "swing"
             else amp * math.sin(math.pi * t))
        model.AddParameterValue(pid, v * g["amp"])
    g["f"] = gf + 1
    if g["f"] >= g["dur"]:
        state["gesture"] = None


def express_frame(win, model, f, control, idle_motion, state, clothes=None,
                  tug=None, tug_zone="body"):
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

    # optional random body actions (--random-actions): lively emotions
    # occasionally sway / twist / hop / wave to break the idle monotony
    _random_gesture_frame(model, state, emotion, valid, tug)

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
    p28_shy = _apply_tug(model, valid, tug, tug_zone)   # locked-drag reaction
    model.Update()
    # llny's physics drives Param28 ("yy" hip/leg) off the head/body sway and
    # overwrites the emotion's leg pose every frame; re-apply the pose value
    # (plus any shy-tug squeeze) so the legs actually hold the pose.
    if "Param28" in valid and ("Param28" in pose or p28_shy):
        model.SetParameterValue("Param28", blend.get("Param28", 0.0) + p28_shy)
    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)       # fully transparent background
    GL.glEnable(GL.GL_BLEND)
    model.Draw()
    win.present()
    glfw.swap_buffers(win.window)
    glfw.poll_events()


def render_frame(win, model, f, present_lookup, clothes=None, tug=None, valid=None,
                 tug_zone="body", demo_mouth=False):
    """Drive one animation frame; returns the alpha channel (HxW uint8).
    clothes: None = keep the demo's auto coat on/off, 0..1 = forced jacket
    level (right-double-click toggles it). tug: (tx, ty) mouse-pull reaction
    while locked, with tug_zone "head"/"body" choosing the reacting part
    (for _apply_tug); valid: the model's parameter ids. demo_mouth: True =
    the old idle auto open/close mouth (default off: mouth stays shut)."""
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

    if demo_mouth:
        model.SetParameterValue("ParamMouthOpenY", 0.5 + 0.5 * math.sin(f * 0.30))
    else:
        model.SetParameterValue("ParamMouthOpenY", 0.0)   # idle demo: mouth shut
    model.SetParameterValue("ParamAngleZ", math.sin(f * 0.05) * 8.0)
    if "Param2" in present_lookup:               # coat on/off (auto sway or manual)
        if clothes is None:
            model.SetParameterValue("Param2", 0.5 + 0.5 * math.sin(f * 0.01))
        else:
            model.SetParameterValue("Param2", clothes)

    _apply_tug(model, valid, tug, tug_zone)      # locked-drag reaction
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


def viewer_frame(win, model, f, idle_motion=None, clothes=None, tug=None,
                 valid=None, present_lookup=None, tug_zone="body"):
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
    _apply_tug(model, valid, tug, tug_zone)          # locked-drag reaction
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
    ap.add_argument("--demo-talk", action="store_true",
                    help="default demo (no args): keep the old auto open/close "
                         "mouth while idle (default off: mouth stays shut)")
    ap.add_argument("--self-test", action="store_true",
                    help="render one transparent frame, print alpha stats, save "
                         "pet_preview.png, then exit")
    ap.add_argument("--random-actions", action="store_true",
                    help="express mode only: lively emotions (兴奋/开心/惊喜) "
                         "occasionally make a random body action — body sway, "
                         "twist, hop, wave an arm, arms out, head shake — to "
                         "break the idle monotony (default off: original look)")
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
        win.set_scale(scale)                     # click zones track the scaled body

        present_lookup = param_lookup(model, ["Param14", "Param2"])
        if "Param14" in present_lookup:          # llny: remove watermark
            model.SetParameterValue("Param14", 1.0)
        model_ids = {model.GetParameter(i).id
                     for i in range(model.GetParameterCount())}

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
        estate["random_actions"] = args.random_actions   # --random-actions flag
        if args.random_actions:
            # first action comes after only ~1-2.5 s (past the emotion's
            # cross-fade) so you see it immediately; the steady rhythm between
            # actions stays 1.5-4.5 s
            estate["gesture_at"] = glfw.get_time() + random.uniform(
                GESTURE_FIRST_MIN_S, GESTURE_FIRST_MAX_S)
            print("random actions: on — 兴奋/开心/惊喜 will occasionally "
                  "sway / twist / hop / wave an arm (1.5-4.5 s apart)")

        def _toggle_clothes():                      # right-double-click on the pet
            dressed = control.toggle_clothes()
            print("jacket: " + ("on" if dressed else "off"))
        win.clothes_cb = _toggle_clothes

        def _on_lock(locked):                       # right-click on the pet
            print("position: " + ("locked (drag disabled)" if locked
                                  else "unlocked (drag to move)"))
        win.lock_cb = _on_lock

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
            express_frame(win, model, 0, control, idle_motion, estate, clothes=0.0,
                          tug=(0.0, 0.0))
        elif args.viewer:
            viewer_frame(win, model, 0, idle_motion, clothes=0.0,
                         present_lookup=present_lookup, tug=(0.0, 0.0),
                         valid=model_ids)
        else:
            render_frame(win, model, 0, present_lookup, clothes=0.0,
                         tug=(0.0, 0.0), valid=model_ids,
                         demo_mouth=args.demo_talk)

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
              "0 to reset; right-click to lock/unlock position, "
              "right-DOUBLE-click to take the jacket off/on; while locked, "
              "drag the head to turn/nod it, drag the body to turn it, drag "
              "the legs to make her shy (thighs together); double-click her "
              "hips/thighs to make her shy instantly (Alt+F4 works too)")
        f = 0
        prev_keys = {}
        clothes_level = 0.0               # Param2 level: 0 = dressed, 1 = coat removed
        while True:
            if glfw.window_should_close(win.window) or \
               glfw.get_key(win.window, glfw.KEY_ESCAPE) == glfw.PRESS:
                break

            win._fire_pending_click()     # confirm lone right-clicks (lock/unlock)

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
                win.set_scale(scale)
                print(f"scale = {scale:.2f}")
            if pressed.get(glfw.KEY_MINUS) or pressed.get(glfw.KEY_KP_SUBTRACT):
                scale = max(0.1, scale / 1.15)
                model.SetScale(scale)
                win.set_scale(scale)
                print(f"scale = {scale:.2f}")
            if pressed.get(glfw.KEY_0):
                scale = args.scale
                model.SetScale(scale)
                win.set_scale(scale)
                print(f"scale reset = {scale:.2f}")

            # jacket: Param2 0 = dressed, 1 = coat removed (probed: llny names
            # it 去外套). Default dressed; a right-double-click toggles it and
            # the animated level makes the take-off / put-on smooth.
            c = control.clothes
            target = 0.0 if c else 1.0
            clothes_level += (target - clothes_level) * 0.06
            clothes_value = clothes_level

            # locked-drag tug: follow the mouse pull while held, then glide back
            # to (0,0) on release — attack fast, decay slow so it eases home
            t, tt = win.tug, win.tug_target
            rate = (TUG_ATTACK_RATE if tt != (0.0, 0.0) else TUG_RELEASE_RATE)
            win.tug = (t[0] + (tt[0] - t[0]) * rate,
                       t[1] + (tt[1] - t[1]) * rate)

            if express:
                express_frame(win, model, f, control, idle_motion, estate,
                              clothes=clothes_value, tug=win.tug,
                              tug_zone=win.tug_zone)
            elif args.viewer:
                viewer_frame(win, model, f, idle_motion, clothes=clothes_value,
                             present_lookup=present_lookup, tug=win.tug,
                             valid=model_ids, tug_zone=win.tug_zone)
            else:
                render_frame(win, model, f, present_lookup, clothes=clothes_value,
                             tug=win.tug, valid=model_ids,
                             tug_zone=win.tug_zone,
                             demo_mouth=args.demo_talk)
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
