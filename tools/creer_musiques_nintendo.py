"""Courtes compositions originales pour les intros Nintendo de PhantomStation.

Ces consoles n'ont pas de musique de démarrage système commune aux jeux.
"""
import math
import wave
from pathlib import Path

import numpy as np

RATE = 44100
DURATION = 8.4
ASSETS = Path(__file__).resolve().parents[1] / "assets"


def note_frequency(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def synth_note(output, at, length, pitch, gain, kind):
    start = int(at * RATE)
    count = min(int(length * RATE), len(output) - start)
    t = np.arange(count, dtype=np.float64) / RATE
    f = note_frequency(pitch)
    attack = np.minimum(1.0, t / .035)
    release = np.minimum(1.0, np.maximum(0, (length - t) / .32))
    envelope = attack * release
    phase = 2 * math.pi * f * t
    if kind == "pulse":
        tone = np.tanh(3 * np.sin(phase)) * .75 + .25 * np.sin(phase / 2)
    elif kind == "soft":
        tone = .75 * np.sin(phase) + .18 * np.sin(2 * phase) + .07 * np.sin(3 * phase)
    else:
        tone = .75 * np.sin(phase) + .20 * np.sin(2.01 * phase) * np.exp(-2 * t)
        tone += .05 * np.sin(3.98 * phase) * np.exp(-4 * t)
    output[start:start + count] += gain * envelope * tone


def build(name, melody, kind):
    sound = np.zeros(int(RATE * DURATION), dtype=np.float64)
    for at, length, pitch in melody:
        synth_note(sound, at, length, pitch, .16, kind)
        if kind != "pulse":
            synth_note(sound, at + .07, length * .8, pitch - 12, .035, "soft")
    sound *= np.minimum(1.0, np.maximum(0, (DURATION - np.arange(len(sound)) / RATE) / .7))
    samples = (np.clip(sound, -.90, .90) * 32767).astype("<i2")
    with wave.open(str(ASSETS / ("son-demarrage-" + name + ".wav")), "wb") as wav:
        wav.setparams((1, 2, RATE, len(samples), "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())


build("nes", [(0.35,.55,60),(.95,.55,64),(1.55,.55,67),(2.15,.8,72),
              (3.1,.65,71),(3.85,.65,67),(4.6,1.9,72)], "pulse")
build("snes", [(0.3,1.0,60),(1.15,1.0,64),(2.0,1.05,67),(2.9,1.2,71),
               (3.9,1.1,69),(4.8,2.2,72)], "soft")
build("n64", [(0.3,.8,55),(.95,.8,60),(1.6,.8,64),(2.25,1.1,67),
              (3.2,.85,72),(4.0,.85,71),(4.8,2.1,76)], "bell")
