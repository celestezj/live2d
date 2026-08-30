"""Programmatic parameter control for Live2D v3 models (live2d-py).

Every frame you may set ANY model parameter from Python, then Update() + Draw().
This script provides:

  list   - print every parameter (id, cdi3 display name, min/max/default)
  anim   - run a keyframe-animation demo in a GLFW window, save screenshots
  set    - set specific params and render one frame to PNG (one-off snapshot)

Examples:
  python param_control.py list --model /path/to/llny.model3.json
  python param_control.py anim --model /path/to/llny.model3.json --frames 240
  python param_control.py set --model /path/to/llny.model3.json \
        --param Param14=1 Param2=0.6 ParamEyeLOpen=0.2 --out shot.png
"""
import argparse
import json
import math
import os
import time

import glfw
import live2d.v3 as live2d
from OpenGL import GL
from PIL import Image

W, H = 800, 600
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(
    HERE, "live2d-py", "Resources", "v3", "llny", "llny.model3.json")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load_cdi_names(model_json_path):
    """Read the sibling .cdi3.json to map parameter Id -> display Name."""
    d = os.path.dirname(os.path.abspath(model_json_path))
    base = os.path.basename(model_json_path)
    if base.lower().endswith(".model3.json"):     # llny.model3.json -> llny
        base = base[: -len(".model3.json")]
    cdi = os.path.join(d, base + ".cdi3.json")
    if not os.path.exists(cdi):
        return {}
    try:
        with open(cdi, encoding="utf-8") as f:
            data = json.load(f)
        return {p["Id"]: p.get("Name", "") for p in data.get("Parameters", [])}
    except Exception:
        return {}


class _GLFW:
    """Borrowed window helper: creates a GL context, inits live2d, owns a model."""

    def __init__(self, model_json, width=W, height=H):
        glfw.init()
        self.window = glfw.create_window(width, height, "live2d param control", None, None)
        if not self.window:
            glfw.terminate()
            raise RuntimeError("glfw window creation failed")
        glfw.make_context_current(self.window)
        live2d.init()
        live2d.glInit()
        self.model = live2d.LAppModel()
        self.model.LoadModelJson(os.path.abspath(model_json))
        self.model.Resize(width, height)
        self.width, self.height = width, height

    def draw_frame(self, r=1.0, g=1.0, b=1.0):
        self.model.Update()
        live2d.clearBuffer(r, g, b, 1.0)
        GL.glEnable(GL.GL_BLEND)
        self.model.Draw()
        glfw.swap_buffers(self.window)
        glfw.poll_events()

    def snapshot(self, out_path):
        GL.glReadBuffer(GL.GL_BACK)
        buf = GL.glReadPixels(0, 0, self.width, self.height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        Image.frombytes("RGB", (self.width, self.height), buf) \
            .transpose(Image.FLIP_TOP_BOTTOM).save(out_path)

    def close(self):
        live2d.dispose()
        glfw.destroy_window(self.window)
        glfw.terminate()


def ensure_params(model, names):
    """Get {id: (min, max, default)} for the given ids, ignoring missing ones."""
    known = {}
    for i in range(model.GetParameterCount()):
        p = model.GetParameter(i)
        known[p.id] = (p.min, p.max, p.default)
    return {n: known[n] for n in names if n in known}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_list(model_json):
    names = load_cdi_names(model_json)
    v = _GLFW(model_json)
    m = v.model
    rows = []
    for i in range(m.GetParameterCount()):
        p = m.GetParameter(i)
        rows.append((p.id, names.get(p.id, ""), p.min, p.max, p.default, p.value))
    v.close()
    print(f"{'Id':<24} {'名称':<20} {'min':>8} {'max':>8} {'default':>8}")
    print("-" * 78)
    for pid, nm, lo, hi, dflt, cur in rows:
        print(f"{pid:<24} {nm:<20} {lo:>8.3f} {hi:>8.3f} {dflt:>8.3f}")


def cmd_set(model_json, param_values, out):
    v = _GLFW(model_json)
    for spec in param_values:
        k, _, val = spec.partition("=")
        v.model.SetParameterValue(k, float(val))
        print(f"set {k} = {val}")
    v.draw_frame()
    v.snapshot(out)
    print("saved:", out)
    v.close()


def cmd_anim(model_json, frames, out_dir):
    """Keyframe animation demo: blink + talk + slight head sway + (llny) coat.
    Parameters are linearly interpolated between keyframes."""
    v = _GLFW(model_json)
    m = v.model
    names = load_cdi_names(model_json)
    print("available params for demo:")
    for i in range(m.GetParameterCount()):
        p = m.GetParameter(i)
        if p.id in ("Param2", "Param14", "ParamEyeLOpen", "ParamEyeROpen",
                    "ParamMouthOpenY", "ParamAngleZ", "ParamAngleX", "ParamAngleY"):
            print(f"  {p.id:<24} {names.get(p.id,''):<16} [{p.min}, {p.max}]")

    # static: remove watermark (llny) if the param exists
    static = ensure_params(m, ["Param14"])
    if "Param14" in static:
        m.SetParameterValue("Param14", 1.0)
        print("Param14(去掉水印) -> 1")

    # build keyframes: each frame dict {param: value}; animation loops
    blink_frames = [{"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0},
                    {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0},
                    {"ParamEyeLOpen": 0.05, "ParamEyeROpen": 0.05},
                    {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}]
    blink_dur = 60          # frames per blink cycle

    fps_frames, fps_timer = 0, time.time()
    os.makedirs(out_dir, exist_ok=True)

    for f in range(frames):
        # --- interpolate the animation params ---
        cycle = f % blink_dur
        t = cycle / blink_dur
        a = math.floor(t * len(blink_frames)) % len(blink_frames)
        b = (a + 1) % len(blink_frames)
        frac = (t * len(blink_frames)) % 1.0
        for pid in blink_frames[a]:
            lo = static.get(pid, (0, 1, 0))
            v0 = blink_frames[a].get(pid, 1.0)
            v1 = blink_frames[b].get(pid, 1.0)
            m.SetParameterValue(pid, v0 + (v1 - v0) * frac)

        # talk (mouth) — sine
        m.SetParameterValue("ParamMouthOpenY",
                            0.5 + 0.5 * math.sin(f * 0.30))  # ~10 Hz / 60fps

        # head sway
        m.SetParameterValue("ParamAngleZ", math.sin(f * 0.05) * 8.0)

        # coat: slowly take it off over the whole run (llny Param2, 0..1)
        if "Param2" in static:
            m.SetParameterValue("Param2", min(1.0, f / max(1, frames - 1) * 1.0))

        v.draw_frame()

        if f % 30 == 0:  # snapshot every 30 frames
            v.snapshot(os.path.join(out_dir, f"frame_{f:04d}.png"))

        fps_frames += 1
        if time.time() - fps_timer >= 1.0:
            print(f"frame {f}/{frames}  FPS={fps_frames}")
            fps_frames, fps_timer = 0, time.time()

    v.close()
    print(f"done. screenshots in {out_dir}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--model", default=DEFAULT_MODEL)

    p_set = sub.add_parser("set")
    p_set.add_argument("--model", default=DEFAULT_MODEL)
    p_set.add_argument("--param", action="append", required=True,
                       help="KEY=VALUE, repeatable")
    p_set.add_argument("--out", default=os.path.join(HERE, "param_shot.png"))

    p_anim = sub.add_parser("anim")
    p_anim.add_argument("--model", default=DEFAULT_MODEL)
    p_anim.add_argument("--frames", type=int, default=240)
    p_anim.add_argument("--out-dir", default=os.path.join(HERE, "anim_frames"))

    args = ap.parse_args()
    if args.cmd == "list":
        cmd_list(args.model)
    elif args.cmd == "set":
        cmd_set(args.model, args.param, args.out)
    elif args.cmd == "anim":
        cmd_anim(args.model, args.frames, args.out_dir)


if __name__ == "__main__":
    main()
