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

Usage:
  python desktop_pet.py [--model /path/to/model.model3.json]
                        [--width 520 --height 720] [--x 0 --y 0] [--scale 1.0]
                        [--viewer]           # SDK-native dynamics (see above)
                        [--self-test]        # one transparent frame + alpha stats

Note: GLFW_TRANSPARENT_FRAMEBUFFER is macOS-only, so on Windows we render to the
GL back buffer, read back RGBA, premultiply alpha, and present it through
UpdateLayeredWindow (classic per-pixel-alpha layered window).
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os

import glfw
import live2d.v3 as live2d
import numpy as np
from OpenGL import GL
from PIL import Image

W, H = 520, 720
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


def render_frame(win, model, f, present_lookup):
    """Drive one animation frame; returns the alpha channel (HxW uint8)."""
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
    if "Param2" in present_lookup:               # coat slowly on/off
        model.SetParameterValue("Param2", 0.5 + 0.5 * math.sin(f * 0.01))

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


def viewer_frame(win, model, f, idle_motion=None):
    """One frame of the Live2DViewer-style idle.

    A regular blink plus a gentle multi-axis head/body sway are written from
    Python (Live2DViewer drives its idle the same way); physics, evaluated
    inside model.Update(), then swings the hair/ear/bow ArtMeshes — that is
    what makes the ears/hair visibly move. The model's own idle motion loops
    underneath (llny's motions/idel.motion3.json: 3s breath + subtle body).
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
    model.Update()
    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)       # fully transparent background
    GL.glEnable(GL.GL_BLEND)
    model.Draw()
    win.present()
    glfw.swap_buffers(win.window)
    glfw.poll_events()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--width", type=int, default=W)
    ap.add_argument("--height", type=int, default=H)
    ap.add_argument("--x", type=int, default=None, help="window left (default top-right)")
    ap.add_argument("--y", type=int, default=None, help="window top (default top-right)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="character scale (SetScale is absolute; 1.0 fits the "
                         "window). +/-/0 keys resize it live.")
    ap.add_argument("--viewer", action="store_true",
                    help="Live2DViewer-style idle: regular blink + multi-axis "
                         "head/body sway drive the physics (hair/ears/bows "
                         "visibly swing) and the model's own idle motion loops, "
                         "instead of the procedural demo animation")
    ap.add_argument("--click-through", action="store_true",
                    help="let mouse clicks pass through the window (ESC then "
                         "may not work; Alt+F4 or task manager to quit)")
    ap.add_argument("--self-test", action="store_true",
                    help="render one transparent frame, print alpha stats, save "
                         "pet_preview.png, then exit")
    args = ap.parse_args()

    glfw.init()
    win = LayeredWindow(args.width, args.height, args.x, args.y,
                        click_through=args.click_through)
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

        idle_motion = None
        if args.viewer:
            idle_motion = find_idle_motion(args.model)
            if idle_motion is not None:
                model.LoadExtraMotion("Idle", idle_motion)
                model.StartMotion("Idle", 0, priority=1)
                print("viewer mode: looping idle motion "
                      + os.path.basename(idle_motion))
            else:
                print("viewer mode: no idle motion found; "
                      "auto blink/breath/physics/sway only")

        if args.viewer:
            viewer_frame(win, model, 0, idle_motion)
        else:
            render_frame(win, model, 0, present_lookup)

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

        print("transparent pet running — press ESC to quit, +/- to resize, "
              "0 to reset (Alt+F4 works too)")
        f = 0
        prev_keys = {}
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

            if args.viewer:
                viewer_frame(win, model, f, idle_motion)
            else:
                render_frame(win, model, f, present_lookup)
            f += 1
    finally:
        live2d.dispose()
        win.close()
        glfw.terminate()


if __name__ == "__main__":
    main()
