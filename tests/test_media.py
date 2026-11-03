import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from slopgate.media import check_audio, check_image, rules_for

SR = 44100


def write_wav(path: Path, samples: np.ndarray) -> None:
    """Mono from a 1-D array, stereo from an (n, 2) array."""
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def tone(seconds: float, freq: float = 441.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def decaying(seconds: float, freq: float = 441.0, amp: float = 0.3) -> np.ndarray:
    """A tone with a natural exponential decay and a short fade-in."""
    t = np.arange(int(SR * seconds)) / SR
    env = np.exp(-6 * t / seconds) * np.minimum(1, t / 0.005)
    return amp * env * np.sin(2 * np.pi * freq * t)


def noise(seconds: float, amp: float, seed: int = 0) -> np.ndarray:
    return amp * np.random.default_rng(seed).standard_normal(int(SR * seconds))


def rule_ids(findings):
    return {f.rule for f in findings}


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def save(self, name, img, fmt=None):
        path = self.dir / name
        img.save(path, format=fmt)
        return path

    def sprite(self, size=32, smooth=False):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        for x in range(size // 4, size * 3 // 4):
            for y in range(size // 4, size * 3 // 4):
                img.putpixel((x, y), (200, 40, 40, 255))
        if smooth:
            img = img.resize((size * 4, size * 4), Image.BILINEAR).resize((size, size), Image.BILINEAR)
        return img

    def test_clean_pixel_sprite_passes(self):
        path = self.save("hero.png", self.sprite())
        r = rules_for(
            "hero.png",
            {
                "rules": [
                    {
                        "glob": "*.png",
                        "size": "32x32",
                        "pixel_art": True,
                        "max_colors": 4,
                        "alpha": "required",
                    }
                ]
            },
        )
        self.assertEqual(check_image(path, "hero.png", r), [])

    def test_smoothed_sprite_fails_palette_and_soft_alpha(self):
        path = self.save("hero.png", self.sprite(smooth=True))
        r = rules_for("hero.png", {"rules": [{"glob": "*.png", "pixel_art": True, "max_colors": 4}]})
        self.assertTrue({"palette", "soft-alpha"} <= rule_ids(check_image(path, "hero.png", r)))

    def test_wrong_size_and_frame_grid(self):
        path = self.save("sheet.png", Image.new("RGBA", (100, 32)))
        r = rules_for("sheet.png", {"rules": [{"glob": "*.png", "size": "128x32", "frame": "32x32"}]})
        self.assertEqual(rule_ids(check_image(path, "sheet.png", r)), {"size", "frame-grid"})

    def test_format_mismatch(self):
        path = self.save("photo.png", Image.new("RGB", (8, 8), "white"), fmt="JPEG")
        self.assertIn("format-mismatch", rule_ids(check_image(path, "photo.png", rules_for("photo.png", {}))))

    def test_off_grid_upscale(self):
        base = self.sprite(8)
        path = self.save("up.png", base.resize((32, 32), Image.BILINEAR))
        r = rules_for("up.png", {"rules": [{"glob": "*.png", "pixel_scale": 4}]})
        self.assertIn("off-grid", rule_ids(check_image(path, "up.png", r)))
        clean = self.save("up2.png", base.resize((32, 32), Image.NEAREST))
        self.assertNotIn("off-grid", rule_ids(check_image(clean, "up2.png", r)))

    def test_tile_seam(self):
        grad = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
        path = self.save("tile.png", Image.fromarray(np.dstack([grad] * 3)))
        r = rules_for("tile.png", {"rules": [{"glob": "*.png", "tileable": True}]})
        self.assertIn("tile-seam", rule_ids(check_image(path, "tile.png", r)))
        noise = np.random.default_rng(1).integers(100, 140, (64, 64, 3), dtype=np.uint8)
        ok = self.save("noise.png", Image.fromarray(noise))
        self.assertNotIn("tile-seam", rule_ids(check_image(ok, "noise.png", r)))

    def test_unknown_manifest_key_is_rejected(self):
        with self.assertRaises(ValueError):
            rules_for("a.png", {"rules": [{"glob": "*.png", "colour_count": 4}]})


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class AudioTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def run_check(self, name, samples, **rule):
        path = self.dir / name
        write_wav(path, samples)
        r = rules_for(name, {"rules": [{"glob": "*.wav", **rule}]} if rule else {})
        return rule_ids(check_audio(path, name, r))

    def test_clean_loop_passes(self):
        # 441 Hz completes a whole number of cycles in 2 s, so the wrap is seamless.
        self.assertEqual(self.run_check("loop.wav", tone(2.0), loop=True), set())

    def test_loop_with_click(self):
        self.assertIn("loop-click", self.run_check("loop.wav", tone(2.0, freq=437.3), loop=True))

    def test_loop_with_gap(self):
        samples = np.concatenate([tone(2.0), np.zeros(SR // 4)])
        self.assertIn("loop-gap", self.run_check("loop.wav", samples, loop=True))

    def test_loop_level_jump(self):
        samples = tone(2.0) * np.linspace(1.0, 0.1, SR * 2)
        self.assertIn("loop-level-jump", self.run_check("loop.wav", samples, loop=True))

    def test_clipping(self):
        self.assertIn("clipping", self.run_check("hit.wav", tone(1.0, amp=1.6)))

    def test_one_shot_leading_silence_warns(self):
        samples = np.concatenate([np.zeros(SR), tone(0.5)])
        self.assertIn("leading-silence", self.run_check("hit.wav", samples))

    def test_loudness_target(self):
        self.assertIn("loudness", self.run_check("music.wav", tone(3.0, amp=0.02), loudness_lufs=-16))
        self.assertNotIn(
            "loudness",
            self.run_check("music.wav", tone(3.0, amp=0.02), loudness_lufs=-40, loudness_tolerance=5),
        )

    def test_mp3_loop_padding_is_reported(self):
        src = self.dir / "src.wav"
        write_wav(src, tone(2.0))
        mp3 = self.dir / "loop.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-write_xing", "0", str(mp3)], check=True
        )
        r = rules_for("loop.mp3", {"rules": [{"glob": "*.mp3", "loop": True}]})
        found = rule_ids(check_audio(mp3, "loop.mp3", r))
        self.assertTrue(found & {"loop-gap", "loop-click"}, found)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class AudioDefectTests(unittest.TestCase):
    """The checks from the audio-generation antipattern list, each with a clean twin."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def check(self, name, samples, **rule):
        path = self.dir / name
        write_wav(path, samples)
        return self.check_file(path, **rule)

    def check_file(self, path, **rule):
        glob = "*" + path.suffix
        r = rules_for(path.name, {"rules": [{"glob": glob, **rule}]} if rule else {})
        return rule_ids(check_audio(path, path.name, r))

    def music(self, seconds=4.0):
        # Broadband and busy enough to stand in for a mix: noise shaped by a beat.
        t = np.arange(int(SR * seconds)) / SR
        beat = 0.4 + 0.6 * (np.sin(2 * np.pi * 2 * t) > 0)
        return decaying(seconds, 220) + 0.15 * beat * noise(seconds, 1.0) * np.minimum(1, t / 0.005)

    def test_dropout(self):
        clean = self.music()
        broken = clean.copy()
        broken[SR * 2 : SR * 2 + SR // 20] = 0  # 50 ms of digital silence mid-sound
        self.assertIn("dropout", self.check("drop.wav", broken))
        self.assertNotIn("dropout", self.check("ok.wav", clean))

    def test_cut_off_ending(self):
        hit = decaying(1.0)
        self.assertNotIn("cut-off-ending", self.check("hit.wav", hit))
        self.assertIn("cut-off-ending", self.check("cut.wav", hit[: SR // 4]))
        # A loop ends at full level on purpose; the loop checks cover it instead.
        self.assertNotIn("cut-off-ending", self.check("loop.wav", tone(2.0), loop=True))

    def test_starts_mid_waveform(self):
        self.assertIn("starts-mid-waveform", self.check("late.wav", decaying(1.0)[SR // 100 :] + 0.2))
        self.assertNotIn("starts-mid-waveform", self.check("hit.wav", decaying(1.0)))

    def test_lossy_source(self):
        src = self.dir / "master.wav"
        write_wav(src, self.music(6.0))
        self.assertNotIn("lossy-source", self.check_file(src))
        mp3, back = self.dir / "gen.mp3", self.dir / "gen.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-b:a", "128k", str(mp3)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), str(back)], check=True)
        self.assertIn("lossy-source", self.check_file(back))
        # The MP3 itself is what it says it is; only lossless containers are checked.
        self.assertNotIn("lossy-source", self.check_file(mp3))

    def test_lossy_source_also_fires_on_a_steep_lowpass(self):
        # Not every wall comes from a codec: a 16 kHz low-pass on a native WAV
        # looks the same, which is why the finding is a warning that names both.
        src = self.dir / "master.wav"
        write_wav(src, self.music(6.0))
        lowpassed = self.dir / "filtered.wav"
        steep = ",".join(["lowpass=f=16000:poles=2"] * 4)
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", steep, str(lowpassed)]
        subprocess.run(cmd, check=True)
        found = check_audio(lowpassed, "filtered.wav", rules_for("filtered.wav", {}))
        [f] = [f for f in found if f.rule == "lossy-source"]
        self.assertEqual(f.severity, "warning")
        self.assertIn("low-pass", f.message)

    def test_mono_collapse(self):
        left = self.music()
        self.assertIn("mono-collapse", self.check("flip.wav", np.stack([left, -left], axis=1)))
        wide = np.stack([left, 0.8 * left + 0.2 * noise(4.0, 0.1, seed=3)], axis=1)
        self.assertNotIn("mono-collapse", self.check("ok.wav", wide))

    def test_hiss_in_pauses(self):
        phrase = decaying(0.6, 300)
        gap = np.zeros(SR // 2)
        speech = np.concatenate([phrase, gap, phrase, gap, phrase])
        self.assertNotIn("hiss", self.check("clean.wav", speech))
        hissy = speech + 0.002 * np.random.default_rng(7).standard_normal(len(speech))  # about -54 dBFS
        self.assertIn("hiss", self.check("hissy.wav", hissy))

    def test_flat_dynamics_only_when_declared(self):
        flat = self.music(40.0) * 0.5
        self.assertNotIn("flat-dynamics", self.check("song.wav", flat))
        self.assertIn("flat-dynamics", self.check("song.wav", flat, min_loudness_range_lu=8))


if __name__ == "__main__":
    unittest.main()
