"""system_listen.py — capture the system's output audio (WASAPI loopback) and
report per-chunk RMS energy.

This is the "follow whatever the audio player is playing" mode: your AI
pipeline (or any app) plays sound through the system output; this module taps
that stream and reports how loud each chunk is. Feed it straight into a
desktop pet's mouth:

    from system_listen import listen_system_output

    def on_energy(rms01):          # 0..1, called from the capture thread
        pet_mouth = rms01           # (scale by the emotion's loudness)

    threading.Thread(target=listen_system_output,
                     args=(on_energy,), kwargs={"device_index": 13},
                     daemon=True).start()

Requires pyaudiowpatch (installed in the voice-asr env); nothing else on
Windows. Blocks until the stream dies — run it in a daemon thread.

CRITICAL: the input stream's callback MUST return a tuple. pyaudiowpatch's C
wrapper parses the callback's return value with PyArg_ParseTuple even for
input-only streams; returning None raises
    SystemError: new style getargs format but argument is not a tuple
which the C layer prints and then leaks into the main thread. Always
`return (None, pyaudio.paContinue)`.

Loudness is normalised against a slowly-decaying PEAK instead of a fixed
full-scale value: system loopback levels vary a lot with the system volume /
what's playing, so a fixed threshold makes quiet videos barely move the mouth.
Adaptive normalisation keeps the mouth visible regardless of volume.
"""
import time
import traceback

import numpy as np
import pyaudiowpatch as pyaudio

# Silence threshold for system audio — below this RMS the mouth stays shut.
# Set low: a quiet system / low volume can otherwise read as "no signal".
FLOOR = 0.004


def _adaptive(rms: float, peak: float) -> float:
    """0..1 loudness against the current peak; quiet chunks don't exceed 1."""
    if rms < FLOOR:
        return 0.0
    return min(1.0, rms / max(peak, FLOOR))


class _Capture:
    """PyAudio-style stream callback that measures RMS of the loopback mix
    and normalises it against a self-adapting peak."""

    def __init__(self, on_energy):
        self.on_energy = on_energy
        self.n_callbacks = 0
        self.last_advance = time.monotonic()
        self.peak = FLOOR * 2          # start low so the first sound registers

    def callback(self, in_data, frame_count, time_info, status):
        try:
            if in_data:
                arr = np.frombuffer(in_data, dtype=np.float32)
                if arr.size:
                    rms = float(np.sqrt(np.mean(arr * arr)))
                    # track the recent peak (decay ~ -18%/s at 48000/512 chunks)
                    self.peak = max(rms, self.peak * 0.998)
                    if self.n_callbacks == 0 and rms >= FLOOR:
                        print(f"listen: audio detected (rms≈{rms:.3f}) — "
                              "mouth now follows the sound")
                    self.on_energy(_adaptive(rms, self.peak))
                    self.n_callbacks += 1
                    self.last_advance = time.monotonic()
        except Exception:
            traceback.print_exc()
        # MUST return a tuple — pyaudiowpatch parses it with PyArg_ParseTuple
        # even for input streams; returning None raises SystemError.
        return (None, pyaudio.paContinue)


def list_loopback_devices():
    """Print every WASAPI loopback device (index, name) to help pick
    --listen-device. Returns [(index, name), ...]."""
    p = pyaudio.PyAudio()
    try:
        rows = []
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info.get("isLoopbackDevice", False):
                rows.append((i, info["name"]))
                print(f"  [{i}] {info['name']}")
        return rows
    finally:
        p.terminate()


def listen_system_output(on_energy, on_fail=None, device_index=None):
    """Blocking: capture the system's output (WASAPI loopback) and call
    on_energy(rms01) per chunk. Never finishes on its own — daemon-thread it.

    device_index=None auto-picks the first [Loopback] device. If no loopback
    device exists, or capture fails, on_fail() is called once and we return.
    """
    p = pyaudio.PyAudio()
    stream = None
    try:
        dev = device_index
        if dev is None:
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("isLoopbackDevice", False):
                    dev = i
                    break
        if dev is None:
            print("listen: no WASAPI loopback device found — "
                  "is there an active output device? (see list_loopback_devices)")
            if on_fail:
                on_fail()
            return

        info = p.get_device_info_by_index(dev)
        rate = int(info["defaultSampleRate"])
        cap = _Capture(on_energy)
        stream = p.open(format=pyaudio.paFloat32,
                        channels=int(info["maxInputChannels"]) or 2,
                        rate=rate,
                        input=True, input_device_index=dev,
                        frames_per_buffer=512, stream_callback=cap.callback)
        stream.start_stream()
        print(f"listen: capturing system output on {info['name']} "
              f"({rate} Hz) — mouth follows whatever is playing")

        # if nothing arrives within ~1.5s, hint how to fix routing
        warned_at = None
        while True:
            if cap.n_callbacks == 0:
                if warned_at is None:
                    warned_at = time.monotonic()
                elif time.monotonic() - warned_at > 1.5:
                    print("listen: no audio reaching this device — is sound "
                          "actually playing out of the device above? (you may "
                          "need --listen-device <index>, see list_loopback_devices)")
                    warned_at = time.monotonic() + 30   # re-hint at most every 30s
            time.sleep(0.05)
    except Exception as exc:
        print(f"listen: capture failed: {exc}")
        traceback.print_exc()
        if on_fail:
            on_fail()
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        p.terminate()
