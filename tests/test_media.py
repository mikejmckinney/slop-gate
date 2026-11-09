import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from slopgate import media_init
from slopgate.media import check_audio, check_image, rules_for
from tests.fake_tools import FakeToolMixin

SR = 44100
REAL_FFMPEG = shutil.which("ffmpeg")
REAL_FFPROBE = shutil.which("ffprobe")


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


def only_when(arg: str, body: str, real: str) -> str:
    """A fake tool's script: `body` when the command line has `arg`, the real tool otherwise."""
    return f'case "$*" in *{arg}*) {body} ;; *) exec {real} "$@" ;; esac'


SUMMARY_HEAD = "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -21.8 LUFS\n"
SUMMARY_LRA = "  Loudness range:\n    LRA:        20.0 LU\n    LRA low:   -41.8 LUFS\n"
SUMMARY_PEAK = "  True peak:\n    Peak:      -18.1 dBFS\n"


def on_stderr(text: str, code: int = 0) -> str:
    quoted = text.replace("\n", "\\n")
    return f"printf '{quoted}' >&2; exit {code}"


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class FailedReadTests(FakeToolMixin, unittest.TestCase):
    """Fake tools on PATH stand in for each ffmpeg and ffprobe read failing or printing nonsense."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = self.dir / "a.wav"
        write_wav(self.path, tone(2.0))

    def check(self, **rule):
        name = self.path.name
        return check_audio(self.path, name, rules_for(name, {"rules": [{"glob": "*.wav", **rule}]}))

    def fails_loudness(self, body):
        self.fake("ffmpeg", only_when("ebur128", body, REAL_FFMPEG))
        [found] = [f for f in self.check(loop=True) if f.rule == "audio-check-failed"]
        self.assertEqual(found.severity, "warning")
        self.assertIn("was not checked", found.message)
        self.assertIn("Treat it as unknown, not as a pass.", found.message)
        return found

    def test_loudness_run_exits_non_zero(self):
        self.assertIn("boom", self.fails_loudness("echo banner >&2; echo boom >&2; exit 1").message)

    def test_loudness_run_exits_non_zero_without_output(self):
        self.assertIn("ffmpeg exited 3", self.fails_loudness("exit 3").message)

    def test_loudness_run_exits_non_zero_after_a_good_summary(self):
        self.fails_loudness(on_stderr(SUMMARY_HEAD + SUMMARY_LRA + SUMMARY_PEAK, code=1))

    def test_loudness_run_prints_nothing(self):
        self.assertIn("without its loudness summary", self.fails_loudness("exit 0").message)

    def test_loudness_run_stops_before_the_summary(self):
        frame = (
            "[Parsed_ebur128_0 @ 0x1] t: 0.1 TARGET:-23 LUFS M: -21.8 S:-120.7 I: -21.8 LUFS LRA: 0.0 LU\n"
        )
        self.fails_loudness(on_stderr(frame))

    def test_a_file_tag_cannot_pose_as_the_summary(self):
        self.fails_loudness(on_stderr("    title : Summary:\n    I: -21.8 LUFS\n"))

    def test_summary_missing_a_value(self):
        for parts in (SUMMARY_HEAD + SUMMARY_PEAK, SUMMARY_HEAD + SUMMARY_LRA):
            with self.subTest(parts=parts):
                self.fails_loudness(on_stderr(parts))

    def test_summary_with_a_garbled_number(self):
        garbled = SUMMARY_HEAD.replace("-21.8", "-2.1.8") + SUMMARY_LRA + SUMMARY_PEAK
        self.fails_loudness(on_stderr(garbled))

    def test_the_other_audio_checks_still_run(self):
        self.fake("ffmpeg", only_when("ebur128", "exit 1", REAL_FFMPEG))
        self.assertLessEqual({"audio-check-failed", "channels"}, rule_ids(self.check(channels=2)))

    def test_a_good_summary_is_read(self):
        good = SUMMARY_HEAD + SUMMARY_LRA + SUMMARY_PEAK
        self.fake("ffmpeg", only_when("ebur128", on_stderr(good), REAL_FFMPEG))
        self.assertNotIn("audio-check-failed", rule_ids(self.check(loop=True)))
        found = self.check(loudness_lufs=-14, loudness_tolerance=1, loop=True)
        self.assertIn(
            "Integrated loudness -21.8 LUFS", next(f for f in found if f.rule == "loudness").message
        )

    def test_clean_audio_gets_no_new_finding(self):
        clips = {"tone": tone(2.0), "silent": np.zeros(SR * 2), "short": tone(0.1)}
        for name, samples in clips.items():
            with self.subTest(name):
                write_wav(self.path, samples)
                self.assertNotIn("audio-check-failed", rule_ids(self.check()))

    def unreadable(self):
        [found] = self.check()
        self.assertEqual((found.rule, found.severity), ("audio-unreadable", "error"))
        return found

    def test_probe_failures_are_reported(self):
        bodies = {
            "non-zero": "echo boom >&2; exit 1",
            "non-zero, silent": "exit 3",
            "empty": "exit 0",
            "not json": "echo '{not json'",
            "not an object": "echo '[1]'",
            "no streams": "echo '{}'",
            "streams not a list": """echo '{"streams": "x"}'""",
            "stream not an object": """echo '{"streams": [1]}'""",
            "no sample rate": """echo '{"streams": [{"channels": 1}]}'""",
            "unusable sample rate": """echo '{"streams": [{"sample_rate": "N/A", "channels": 1}]}'""",
            "zero channels": """echo '{"streams": [{"sample_rate": "44100", "channels": 0}]}'""",
        }
        for name, body in bodies.items():
            with self.subTest(name):
                self.fake("ffprobe", body)
                self.unreadable()

    def test_probe_without_a_usable_duration_still_checks_the_file(self):
        streams = '{"streams": [{"sample_rate": "44100", "channels": 1}], "format": {"duration": "N/A"}}'
        self.fake("ffprobe", f"echo '{streams}'")
        self.assertEqual(rule_ids(self.check(loop=True)), set())

    def test_decode_exits_non_zero(self):
        self.fake("ffmpeg", only_when("f32le", "echo boom >&2; exit 1", REAL_FFMPEG))
        self.assertIn("boom", self.unreadable().message)

    def test_decode_prints_nothing(self):
        self.fake("ffmpeg", only_when("f32le", "exit 0", REAL_FFMPEG))
        [found] = self.check()
        self.assertEqual((found.rule, found.severity), ("audio-empty", "error"))

    def test_decode_prints_a_partial_sample(self):
        self.fake("ffmpeg", only_when("f32le", "printf abc", REAL_FFMPEG))
        self.assertIn("3 bytes", self.unreadable().message)

    def test_media_init_skips_a_file_whose_loudness_read_failed(self):
        self.fake("ffmpeg", only_when("ebur128", "exit 1", REAL_FFMPEG))
        with mock.patch.object(media_init, "all_files", return_value={"a.wav": None}):
            self.assertNotIn("loudness_lufs", media_init.draft(self.dir))


if __name__ == "__main__":
    unittest.main()
