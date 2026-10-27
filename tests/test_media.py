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
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def tone(seconds: float, freq: float = 441.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


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


if __name__ == "__main__":
    unittest.main()
