"""Verify live2d.v3 (C extension) renders the bundled Haru model."""
import os
import time

import glfw
from OpenGL import GL
from PIL import Image

import live2d.v3 as live2d

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "live2d-py", "Resources", "v3", "Haru", "Haru.model3.json")
OUT = os.path.join(HERE, "verify_v3.png")
W, H = 800, 600


def main():
    print("module        :", live2d.__file__)
    print("LIVE2D_VERSION:", live2d.LIVE2D_VERSION)

    if not glfw.init():
        raise RuntimeError("glfw.init failed")

    window = glfw.create_window(W, H, "live2d.v3 verify", None, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("glfw window creation failed")
    glfw.make_context_current(window)

    live2d.init()
    live2d.glInit()

    model = live2d.LAppModel()
    model.LoadModelJson(MODEL)
    model.Resize(W, H)

    print("Parameter Count :", model.GetParameterCount())
    print("Part Count      :", len(model.GetPartIds()))
    print("Canvas Size     :", model.GetCanvasSize())
    print("Pixels Per Unit :", model.GetPixelsPerUnit())

    glfw.swap_interval(0)
    frames = 0
    start = time.time()

    while frames < 180 and time.time() - start < 5.0:
        glfw.poll_events()
        model.Update()
        live2d.clearBuffer(1.0, 1.0, 1.0, 1.0)
        GL.glEnable(GL.GL_BLEND)
        model.Draw()

        if frames == 179:  # grab the back buffer before it is swapped away
            GL.glReadBuffer(GL.GL_BACK)
            buf = GL.glReadPixels(0, 0, W, H, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
            img = Image.frombytes("RGB", (W, H), buf).transpose(Image.FLIP_TOP_BOTTOM)
            img.save(OUT)
            print("screenshot saved:", OUT, f"({img.size[0]}x{img.size[1]})")

        glfw.swap_buffers(window)
        frames += 1

    print("rendered frames :", frames)
    print("FPS (avg)       : %.1f" % (frames / (time.time() - start)))

    live2d.dispose()
    glfw.destroy_window(window)
    glfw.terminate()
    print("OK")


if __name__ == "__main__":
    main()
