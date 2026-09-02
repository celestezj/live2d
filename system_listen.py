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

The mouth opening is gated against the AMBIENT NOISE FLOOR of the loopback
mix, not against an absolute dBFS line: system levels vary hugely with the
volume / content, so a fixed threshold either keeps the mouth half-open for
whole stretches (gate too low — no speech rhythm) or never opens it at a
normal listening volume (gate too high). We track a rolling 5th-percentile
floor (~quietest 3 s) and open the mouth only when a chunk sits ~+4 dB above
it, ramping up to the recent peak — volume-independent, and real gaps between
words stay shut.
"""
import time
import traceback
from collections import deque

import numpy as np
import pyaudiowpatch as pyaudio

FLOOR = 0.004                 # absolute detect floor — used only for the first-
                              # audio "hint" print and as the floor while the
                              # history is still short (see _noise_floor)
OPEN_RATIO = 1.58             # chunk must exceed ~+4 dB above ambient to open
FLOOR_HIST = 300              # chunks (~3 s @ 48 kHz / 512) for the floor estimate
FLOOR_PCT = 5                 # "ambient" = 5th percentile of recent chunk RMS
PEAK_DECAY = 0.998            # recent-peak decay per chunk (~ -18%/s)


def _noise_floor(hist) -> float:
    """Ambient floor = quietest 5% of the last ~3 s of chunk RMS. Adapts to any
    volume; the percentile tracks the gap/background level even as it slowly
    changes, while short speech syllables barely move it."""
    arr = np.asarray(hist, dtype=np.float64)
    if arr.size < 60:
        return max(float(arr.min()), 1e-6)
    return max(float(np.percentile(arr, FLOOR_PCT)), 1e-6)


def _level(rms: float, peak: float, hist) -> float:
    """0..1 mouth opening: stays 0 until `rms` clears OPEN_RATIO× the ambient
    floor, then ramps linearly to 1.0 as it approaches the recent peak. Quiet
    playback opens as easily as loud playback — the gate is relative."""
    lo = _noise_floor(hist) * OPEN_RATIO
    if rms <= lo:
        return 0.0
    hi = max(peak, lo * 4)            # avoid a ~0 span before the peak wakes up
    return min(1.0, (rms - lo) / max(hi - lo, 1e-6))


class _Capture:
    """PyAudio-style stream callback that measures RMS of the loopback mix and
    maps it to a mouth opening relative to the ambient noise floor (volume-
    independent: quiet playback opens as easily as loud, and gaps close)."""

    def __init__(self, on_energy):
        self.on_energy = on_energy
        self.n_callbacks = 0
        self.last_advance = time.monotonic()
        self.peak = FLOOR * 2          # start low so the first sound registers
        self.hist = deque(maxlen=FLOOR_HIST)   # chunk RMS for the floor estimate

    def callback(self, in_data, frame_count, time_info, status):
        try:
            if in_data:
                arr = np.frombuffer(in_data, dtype=np.float32)
                if arr.size:
                    rms = float(np.sqrt(np.mean(arr * arr)))
                    # recent peak (decay ~ -18%/s at 48000/512 chunks)
                    self.peak = max(rms, self.peak * PEAK_DECAY)
                    self.hist.append(rms)
                    if self.n_callbacks == 0 and rms >= FLOOR:
                        print(f"listen: audio detected (rms≈{rms:.3f}) — "
                              "mouth now follows the sound")
                    self.on_energy(_level(rms, self.peak, self.hist))
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
