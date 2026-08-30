"""Reusable API to introspect and control every parameter of a Live2D v3 model.

The list command equivalent, packaged for reuse in your own scripts:

    from model_api import ModelSession, parameter_table, Parameter

    # context manager: hidden GL window, auto cleanup
    with ModelSession("/path/to/llny.model3.json") as m:
        params = m.parameters()          # iterate Parameter objects
        for p in params:
            print(p.id, p.name, p.min, p.max, p.default)
        m.set("Param14", 1.0)            # 去掉水印
        m.set("Param2", 0.6)             # 脱外套 60%
        v = m.read("ParamMouthOpenY")    # 读当前值

    # one-shot: just get the full table, window is managed internally
    table = parameter_table("/path/to/llny.model3.json")
    for p in table:
        print(p.id, p.name, f"[{p.min}, {p.max}]", "default=", p.default)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

import glfw
import live2d.v3 as live2d
from OpenGL import GL


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------

@dataclass
class Parameter:
    """Full information about one model parameter."""
    id: str
    name: str
    type: str
    min: float
    max: float
    default: float
    value: float

    def to_dict(self) -> dict:
        return asdict(self)

    def clamp(self, value: float) -> float:
        """Clamp a value into this parameter's [min, max] range."""
        return max(self.min, min(self.max, value))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load_cdi_names(model_json):
    """Map parameter Id -> display Name from the sibling .cdi3.json file.

    Falls back to an empty mapping if the file is absent.
    """
    d = os.path.dirname(os.path.abspath(model_json))
    base = os.path.basename(model_json)
    if base.lower().endswith(".model3.json"):        # llny.model3.json -> llny
        base = base[: -len(".model3.json")]
    cdi = os.path.join(d, base + ".cdi3.json")
    if not os.path.exists(cdi):
        return {}
    with open(cdi, encoding="utf-8") as f:
        data = json.load(f)
    return {p["Id"]: p.get("Name", "") for p in data.get("Parameters", [])}


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------

class ModelSession:
    """A loaded model inside a (hidden by default) GL context.

    Use as a context manager; close() tears down the GL window and live2d.
    By default the SDK's auto blink/breath are disabled so that set() takes
    effect immediately; pass auto=True to keep them (the model then blinks and
    breathes on its own, overriding ParamEyeLOpen/R and ParamBreath).
    """

    def __init__(self, model_json, width=800, height=600, visible=False,
                 auto=False):
        glfw.init()
        if not visible:
            glfw.window_hint(glfw.VISIBLE, glfw.FALSE)   # headless-ish inspection
        self.window = glfw.create_window(width, height, "model_api", None, None)
        if not self.window:
            raise RuntimeError("glfw window creation failed")
        glfw.make_context_current(self.window)
        live2d.init()
        live2d.glInit()
        self.model = live2d.LAppModel()
        self.model.LoadModelJson(os.path.abspath(model_json))
        if not auto:
            # C++ SDK drives its own blink/breath each Update(); disable so that
            # SetParameterValue() keeps full control of the parameters.
            self.model.SetAutoBlinkEnable(False)
            self.model.SetAutoBreathEnable(False)
        self.model.Resize(width, height)
        self.width, self.height = width, height
        self._names = load_cdi_names(model_json)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            live2d.dispose()
        finally:
            glfw.destroy_window(self.window)
            glfw.terminate()

    # ---- introspection ----

    def parameters(self):
        """Yield every parameter as a Parameter object."""
        for i in range(self.model.GetParameterCount()):
            p = self.model.GetParameter(i)
            yield Parameter(
                id=p.id,
                name=self._names.get(p.id, ""),
                type=str(p.type),
                min=float(p.min),
                max=float(p.max),
                default=float(p.default),
                value=float(p.value),
            )

    def parameter_map(self) -> dict:
        """Return {parameter_id: Parameter}."""
        return {p.id: p for p in self.parameters()}

    def names(self) -> dict:
        """Return {parameter_id: display_name} from cdi3."""
        return dict(self._names)

    def find(self, pid) -> Parameter | None:
        for p in self.parameters():
            if p.id == pid:
                return p
        return None

    # ---- control ----

    def set(self, pid, value, clamp=True) -> float:
        """Set a parameter, clamped to its range by default. Returns the value set."""
        p = self.find(pid)
        if p is None:
            raise KeyError(f"no parameter named {pid!r}")
        v = p.clamp(value) if clamp else float(value)
        self.model.SetParameterValue(pid, v)
        return v

    def read(self, pid) -> float:
        """Read the current value of a parameter."""
        p = self.find(pid)
        if p is None:
            raise KeyError(f"no parameter named {pid!r}")
        return p.value

    # ---- rendering ----

    def update_draw(self, clear=(1.0, 1.0, 1.0)):
        """Drive one frame: Update() + Draw() on a cleared background."""
        self.model.Update()
        live2d.clearBuffer(*clear, 1.0)
        GL.glEnable(GL.GL_BLEND)
        self.model.Draw()
        glfw.swap_buffers(self.window)
        glfw.poll_events()


# --------------------------------------------------------------------------
# one-shot convenience
# --------------------------------------------------------------------------

def parameter_table(model_json, **kw) -> list:
    """Return the full parameter list for a model (list[Parameter]).

    The GL window is created and torn down inside; safe for one-off calls.
    """
    with ModelSession(model_json, **kw) as m:
        return list(m.parameters())


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Dump every parameter of a model.")
    ap.add_argument("--model", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "live2d-py", "Resources", "v3", "llny", "llny.model3.json"))
    ap.add_argument("--out", help="optional file to write the table to")
    args = ap.parse_args()

    rows = parameter_table(args.model)
    lines = [f"{p.id:<44} {p.name:<16} {p.min:>9.3f} {p.max:>9.3f} {p.default:>9.3f}"
             for p in rows]
    text = "\n".join([f"{'Id':<44} {'名称':<16} {'min':>9} {'max':>9} {'default':>9}",
                      "-" * 96, *lines])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("written:", args.out)
    else:
        print(text)
