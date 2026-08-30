"""lipsync.py — play a WAV and report per-chunk RMS energy.

Blocking API, call it from a daemon thread you own:

    play_wav_with_energy(path, on_energy, on_done=None, blocksize=512)

on_energy(rms01) is called from the audio-callback thread with a 0..1
normalized RMS per chunk (0 = silence). on_done() is called exactly once
when playback finishes. Both callbacks must be cheap and non-blocking.

The mouth target for a desktop pet is typically:

    mouth = rms01 * emotion_loudness

Requires sounddevice + soundfile (present in the voice-asr env).
"""
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

# RMS (0..1 normalized) below this is treated as silence
RMS_FLOOR = 0.012
# RMS (0..1) at which the mouth is considered fully open (~ -16 dBFS)
RMS_FULL = 0.16


def _norm(rms: float) -> float:
    if rms < RMS_FLOOR:
        return 0.0
    return min(1.0, rms / RMS_FULL)


class _Player:
    def __init__(self, data, on_energy, on_done):
        self.data = np.ascontiguousarray(data, dtype=np.float32)  # (N, ch)
        self.n = len(data)
        self.pos = 0
        self.on_energy = on_energy
        self.on_done = on_done
        self.last_advance = time.monotonic()   # watchdog: progress marker

    def callback(self, outdata, frames, timeinfo, status):
        end = min(self.pos + frames, self.n)
        take = max(0, end - self.pos)
        if take:
            outdata[:take] = self.data[self.pos:end]
        if take < frames:
            outdata[take:] = 0.0
        if take:
            mono = self.data[end - take:end].mean(axis=1)
            rms = float(np.sqrt(np.mean(mono * mono)))
            self.on_energy(_norm(rms))
            self.last_advance = time.monotonic()
        self.pos = end
        if end >= self.n:
            return sd.CallbackStop


def play_wav_with_energy(path, on_energy, on_done=None, blocksize=512,
                         device=None):
    """Play `path`; call on_energy(rms01) per chunk; call on_done() once at
    the end. Blocks until playback completes or fails — run it in a thread.

    device=None uses the system default output. On failure the pet never
    crashes: a warning is printed and on_done() is still called.
    """
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception as exc:
        print(f"lipsync: cannot read {path!r}: {exc}")
        if on_done:
            on_done()
        return
    if data.shape[0] == 0:
        print(f"lipsync: empty wav: {path!r}")
        if on_done:
            on_done()
        return

    player = _Player(data, on_energy, on_done)
    try:
        with sd.OutputStream(samplerate=sr, blocksize=blocksize, device=device,
                             channels=data.shape[1], dtype="float32",
                             callback=player.callback) as stream:
            # Wait until the last chunk has been handed to the callback. Do NOT
            # loop on stream.active: on MME that flag can stay true long after
            # CallbackStop was returned, which would hang the pet forever.
            while player.pos < player.n:
                # watchdog: a device that accepts the stream but never runs
                # callbacks (e.g. a dead RDP/remote audio device) would hang
                # forever; give up after ~1 s without progress instead
                if time.monotonic() - player.last_advance > 1.0:
                    print("lipsync: output device delivered no audio; "
                          "aborting (use --lipsync-device to pick another)")
                    stream.abort()
                    break
                time.sleep(0.01)
    except Exception as exc:
        print(f"lipsync: playback failed: {exc}")
    finally:
        if on_done:
            on_done()
