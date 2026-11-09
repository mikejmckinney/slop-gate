import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slopgate import cli, media, media_init
from slopgate.video import VIDEO_EXTS, check_video

REAL_FFMPEG = shutil.which("ffmpeg")
REAL_FFPROBE = shutil.which("ffprobe")
PROBE_ONLY = (
    """case "$*" in *-count_frames*) printf '{"streams":[{"nb_read_frames":"9"}]}' ;; """
    """*) printf '%s' "$PROBE" ;; esac"""
)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def rule_ids(findings):
    return {f.rule for f in findings}


H264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
LAVFI = ["-f", "lavfi", "-i"]
PICTURE = "testsrc2=size=160x90:rate=30:d=4"
TONE = "sine=frequency=441:d=4"


class VideoCase(unittest.TestCase):
    """Fixtures are generated once per class with lavfi; nothing binary is committed."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.dir)
        cls.clean = cls.make("clean.mp4", *LAVFI, PICTURE, *LAVFI, TONE, *H264, "-c:a", "aac", "-shortest")
        cls.muted = cls.make("muted.mp4", *LAVFI, PICTURE, *H264)

    @classmethod
    def make(cls, name, *args):
        path = cls.dir / name
        ffmpeg(*args, str(path))
        return path

    def rules(self, name="v.mp4", **rule):
        return media.rules_for(name, {"rules": [{"glob": "*", **rule}]} if rule else {})

    def check(self, path, **rule):
        return check_video(path, path.name, self.rules(path.name, **rule))

    def ids(self, path, **rule):
        return rule_ids(self.check(path, **rule))

    def warning(self, path, rule_id, **rule):
        found = [f for f in self.check(path, **rule) if f.rule == rule_id]
        self.assertTrue(found, f"{rule_id} did not fire")
        self.assertEqual({f.severity for f in found}, {"warning"})
        return found

    def error(self, path, rule_id, **rule):
        found = [f for f in self.check(path, **rule) if f.rule == rule_id]
        self.assertTrue(found, f"{rule_id} did not fire")
        self.assertEqual({f.severity for f in found}, {"error"})
        return found


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class MeasuredTierTests(VideoCase):
    def test_clean_control_has_no_findings_with_no_manifest(self):
        self.assertEqual(self.check(self.clean), [])
        self.assertEqual(self.check(self.muted), [])

    def test_clean_control_passes_every_declaration(self):
        self.assertEqual(
            self.check(self.clean, fps=30, size="160x90", max_duration_s=5, codec="h264"),
            [],
        )

    def test_webm_stream_lengths_come_from_tags(self):
        webm = self.make(
            "clean.webm",
            *LAVFI,
            PICTURE,
            *LAVFI,
            TONE,
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "200k",
            "-c:a",
            "libopus",
        )
        self.assertEqual(self.check(webm), [])

    def test_variable_frame_rate(self):
        vfr = self.make(
            "vfr.mp4",
            *LAVFI,
            PICTURE,
            "-vf",
            "select='if(lt(t,2),1,not(mod(n,3)))'",
            "-fps_mode",
            "vfr",
            *H264,
        )
        self.warning(vfr, "variable-frame-rate")
        self.assertNotIn("variable-frame-rate", self.ids(self.clean))

    def test_stream_length_mismatch(self):
        short = self.make(
            "short.mp4", *LAVFI, PICTURE, *LAVFI, "sine=frequency=441:d=2", *H264, "-c:a", "aac"
        )
        self.warning(short, "stream-length-mismatch")
        self.assertNotIn("stream-length-mismatch", self.ids(self.clean))

    def test_pixel_format(self):
        high = self.make("444.mp4", *LAVFI, PICTURE, "-c:v", "libx264", "-pix_fmt", "yuv444p")
        self.warning(high, "pixel-format")
        self.assertNotIn("pixel-format", self.ids(self.clean))

    def test_pixel_format_only_applies_to_h264(self):
        vp9 = self.make(
            "444.webm", *LAVFI, PICTURE, "-c:v", "libvpx-vp9", "-pix_fmt", "yuv444p", "-b:v", "200k"
        )
        self.assertNotIn("pixel-format", self.ids(vp9))


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class ManifestKeyTests(VideoCase):
    def test_wrong_fps(self):
        self.error(self.clean, "fps", fps=24)
        self.assertNotIn("fps", self.ids(self.clean, fps=30))

    def test_ntsc_rate_matches_its_round_number(self):
        ntsc = self.make("ntsc.mp4", *LAVFI, "testsrc2=size=160x90:rate=30000/1001:d=2", *H264)
        self.assertNotIn("fps", self.ids(ntsc, fps=30))

    def test_wrong_size(self):
        self.error(self.clean, "size", size="1280x720")
        self.assertNotIn("size", self.ids(self.clean, size="160x90"))

    def test_too_long(self):
        self.error(self.clean, "duration", max_duration_s=3)
        self.assertNotIn("duration", self.ids(self.clean, max_duration_s=5))

    def test_wrong_codec(self):
        self.error(self.clean, "codec", codec="vp9")
        self.assertNotIn("codec", self.ids(self.clean, codec="h264"))
        self.assertNotIn("codec", self.ids(self.clean, codec="AVC"))

    def test_malformed_size_is_rejected_like_an_image_size(self):
        with self.assertRaises(ValueError):
            self.check(self.clean, size="wide")


class ManifestValidationTests(unittest.TestCase):
    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            media.rules_for("a.mp4", {"rules": [{"glob": "*.mp4", "frame_rate": 30}]})

    def test_malformed_values_are_rejected_even_when_the_glob_does_not_match(self):
        for bad in (
            {"fps": "thirty"},
            {"fps": 0},
            {"fps": True},
            {"max_duration_s": "long"},
            {"max_freeze_s": -1},
            {"max_black_s": "x"},
            {"codec": 264},
            {"codec": " "},
        ):
            with self.subTest(bad), self.assertRaises(ValueError):
                media.rules_for("a.mp4", {"rules": [{"glob": "*.nomatch", **bad}]})


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class FilterTierTests(VideoCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        hold = "testsrc2=size=160x90:rate=30:d=1,tpad=stop_mode=clone:stop_duration=3,trim=duration=4"
        cls.frozen_end = cls.make("frozen_end.mp4", *LAVFI, hold, *H264)
        middle = (
            "[0:v]trim=duration=1,setpts=PTS-STARTPTS[a];"
            "[0:v]trim=start=1:duration=0.04,setpts=PTS-STARTPTS,"
            "tpad=stop_mode=clone:stop_duration=3[b];"
            "[0:v]trim=start=2:duration=1,setpts=PTS-STARTPTS[c];"
            "[a][b][c]concat=n=3:v=1"
        )
        cls.frozen_mid = cls.make("frozen_mid.mp4", *LAVFI, PICTURE, "-filter_complex", middle, *H264)
        black = "testsrc2=size=160x90:rate=30:d=4,drawbox=c=black:t=fill:enable='between(t,1,3)'"
        cls.black = cls.make("black.mp4", *LAVFI, black, *H264)
        fade = "color=c=white:size=160x90:rate=30:d=2,fade=in:0:60"
        cls.no_loop = cls.make("no_loop.mp4", *LAVFI, fade, *H264)
        # A box crossing the frame in exactly the clip's length comes back to where it began.
        graph = (
            "color=c=black:size=160x90:rate=25:d=2[bg];color=c=white:size=16x16:rate=25:d=2[box];"
            "[bg][box]overlay=x='mod(t*80,160)':y=30:shortest=1"
        )
        cls.looped = cls.make("looped.mp4", *LAVFI, graph, *H264)

    def test_opt_in_checks_stay_quiet_without_the_manifest(self):
        for path in (self.frozen_mid, self.frozen_end, self.black, self.no_loop):
            self.assertEqual(self.check(path), [], path.name)

    def test_freeze_in_the_middle(self):
        [found] = self.warning(self.frozen_mid, "freeze", max_freeze_s=2)
        self.assertIn("0:01.", found.message)
        self.assertNotIn("freeze", self.ids(self.frozen_mid, max_freeze_s=3.5))

    def test_freeze_that_runs_off_the_end(self):
        [found] = self.warning(self.frozen_end, "freeze", max_freeze_s=2)
        self.assertRegex(found.message, r"\(3\.\d s\)|\(2\.9\d? s\)")

    def test_freeze_clean_twin(self):
        self.assertNotIn("freeze", self.ids(self.clean, max_freeze_s=2))

    def test_black(self):
        self.warning(self.black, "black-frames", max_black_s=1)
        self.assertNotIn("black-frames", self.ids(self.black, max_black_s=3))
        self.assertNotIn("black-frames", self.ids(self.clean, max_black_s=1))

    def test_black_to_the_end_is_reported(self):
        path = self.make("black_end.mp4", *LAVFI, "color=c=black:size=160x90:rate=30:d=3", *H264)
        self.warning(path, "black-frames", max_black_s=1)

    def test_non_looping_clip_jumps(self):
        self.warning(self.no_loop, "loop-jump", loop=True)

    def test_looping_clip_does_not(self):
        self.assertEqual(self.check(self.looped, loop=True), [])

    def test_a_fast_seamless_loop_is_not_a_jump(self):
        # Every frame differs a lot from the next, so a fixed cut-off on first and last would fire here.
        graph = (
            "color=c=black:size=160x90:rate=20:d=1[bg];color=c=white:size=80x90:rate=20:d=1[box];"
            "[bg][box]overlay=x='mod(t*640,320)-80':y=0:shortest=1"
        )
        fast = self.make("fast_loop.mp4", *LAVFI, graph, *H264)
        self.assertNotIn("loop-jump", self.ids(fast, loop=True))


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class AudioTrackTests(VideoCase):
    def test_loudness_is_checked_only_when_declared_and_never_blocks(self):
        self.assertNotIn("loudness", self.ids(self.clean))
        [found] = self.warning(self.clean, "loudness", loudness_lufs=-5)
        self.assertTrue(found.message.startswith("Audio track:"))
        self.assertEqual(found.path, "clean.mp4")
        self.assertNotIn("loudness", self.ids(self.clean, loudness_lufs=-24, loudness_tolerance=10))

    def test_every_audio_finding_is_a_warning(self):
        found = self.check(self.clean, sample_rate=8000, channels=2, loudness_lufs=-5)
        self.assertLessEqual({"sample-rate", "channels", "loudness"}, rule_ids(found))
        self.assertEqual({f.severity for f in found}, {"warning"})

    def test_video_keys_alone_do_not_extract_the_track(self):
        with mock.patch.object(media, "check_audio") as audio:
            self.check(self.clean, loop=True, max_duration_s=9, fps=30)
        audio.assert_not_called()

    def test_max_duration_is_not_reported_twice(self):
        found = self.check(self.clean, max_duration_s=3, sample_rate=44100)
        self.assertEqual([f.severity for f in found if f.rule == "duration"], ["error"])

    def test_declared_audio_on_a_muted_video_is_reported(self):
        self.warning(self.muted, "no-audio-track", loudness_lufs=-16)

    def test_unmeasurable_loudness_is_reported(self):
        with mock.patch.object(media, "_ebur128", return_value=(None, None, None)):
            self.warning(self.clean, "video-check-failed", loudness_lufs=-16)


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class CorruptFileTests(VideoCase):
    def unreadable(self, path):
        [found] = self.check(path)
        self.assertEqual((found.rule, found.severity), ("video-unreadable", "error"))
        return found

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_garbage(self):
        self.unreadable(self.write("garbage.mp4", os.urandom(4096)))

    def test_empty_file(self):
        self.unreadable(self.write("empty.mp4", b""))

    def test_truncated_with_index_at_the_front(self):
        # Headers still parse, so only decoding shows the rest of the file is gone.
        whole = self.make("fast.mp4", *LAVFI, PICTURE, *H264, "-movflags", "+faststart")
        self.unreadable(self.write("truncated.mp4", whole.read_bytes()[: whole.stat().st_size // 3]))

    def test_damaged_middle(self):
        data = bytearray(self.clean.read_bytes())
        mid = len(data) // 2
        data[mid : mid + 3000] = bytes((i * 37 + 11) & 0xFF for i in range(3000))
        self.unreadable(self.write("damaged.mp4", bytes(data)))

    def test_audio_only_mp4_has_no_video_stream(self):
        found = self.unreadable(self.make("audio_only.mp4", *LAVFI, TONE, "-c:a", "aac"))
        self.assertIn("no video stream", found.message)

    def test_song_with_cover_art_has_no_video_stream(self):
        cover = self.make("cover.png", *LAVFI, "color=c=red:size=32x32:d=1", "-frames:v", "1")
        song = self.make(
            "song.m4v", *LAVFI, TONE, "-i", str(cover), "-map", "0", "-map", "1",
            "-c:a", "aac", "-c:v", "mjpeg", "-disposition:v", "attached_pic", "-f", "mp4",
        )  # fmt: skip
        self.assertIn("no video stream", self.unreadable(song).message)

    def test_other_containers_and_scripts_are_not_opened(self):
        ts = self.make("real.ts", *LAVFI, PICTURE, *H264)
        self.unreadable(self.write("renamed.mp4", ts.read_bytes()))
        script = b"ffconcat version 1.0\nfile /etc/hostname\n"
        self.unreadable(self.write("script.mp4", script))

    def test_paths_only_travel_as_arguments(self):
        name = "a b;touch pwned;$(touch pwned2).mp4"
        path = self.dir / name
        shutil.copy(self.clean, path)
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)
        found = self.ids(path, max_freeze_s=2, max_black_s=2, loudness_lufs=-24, loudness_tolerance=10)
        self.assertFalse(found & {"video-unreadable", "video-check-failed"}, found)
        self.assertFalse((self.dir / "pwned").exists())
        self.assertFalse((self.dir / "pwned2").exists())


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class FailClosedTests(VideoCase):
    """Fake tools on PATH stand in for ffprobe and ffmpeg failing or printing nonsense."""

    def fake(self, tool, body, **env):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        script = folder / tool
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{folder}:{os.environ['PATH']}", **env})
        patcher.start()
        self.addCleanup(patcher.stop)

    def blocks(self):
        [found] = self.check(self.clean)
        self.assertEqual((found.rule, found.severity), ("video-unreadable", "error"))
        return found

    def test_ffprobe_failure(self):
        self.fake("ffprobe", "echo boom >&2; exit 1")
        self.assertIn("boom", self.blocks().message)

    def test_ffprobe_failure_without_stderr(self):
        self.fake("ffprobe", "exit 3")
        self.assertIn("exited 3", self.blocks().message)

    def test_empty_output(self):
        self.fake("ffprobe", "exit 0")
        self.blocks()

    def test_malformed_json(self):
        self.fake("ffprobe", "echo '{not json'")
        self.blocks()

    def test_json_of_the_wrong_shape(self):
        for body in ("echo '[]'", "echo '{}'", """echo '{"streams":"x"}'""", """echo '{"streams":[1]}'"""):
            with self.subTest(body):
                self.fake("ffprobe", body)
                self.blocks()

    def test_decode_pass_failure(self):
        for body in ("exit 1", "exit 0", "echo '{not json'", """echo '{"streams":[{}]}'"""):
            with self.subTest(body):
                self.fake(
                    "ffprobe", f'case "$*" in *-count_frames*) {body} ;; *) exec {REAL_FFPROBE} "$@" ;; esac'
                )
                self.blocks()

    def test_decode_pass_counts_nothing(self):
        body = """echo '{"streams":[{"nb_read_frames":"0"}]}'"""
        self.fake("ffprobe", f'case "$*" in *-count_frames*) {body} ;; *) exec {REAL_FFPROBE} "$@" ;; esac')
        self.assertIn("decoded no frames", self.blocks().message)

    def fake_stream(self, **overrides):
        stream = {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "pix_fmt": "yuv420p",
            "width": 160,
            "height": 90,
            "r_frame_rate": "30/1",
            "avg_frame_rate": "30/1",
            "duration": "4.0",
            **overrides,
        }
        return json.dumps({"streams": [stream], "format": {"duration": "4.0"}})

    def test_unusable_frame_rate_is_reported_not_passed(self):
        self.fake("ffprobe", PROBE_ONLY, PROBE=self.fake_stream(r_frame_rate="0/0", avg_frame_rate="N/A"))
        self.assertEqual(self.ids(self.clean), {"video-check-failed"})

    def test_declared_values_that_cannot_be_read_block(self):
        probe = self.fake_stream(avg_frame_rate="0/0", width="N/A", duration="N/A", codec_name="")
        self.fake("ffprobe", PROBE_ONLY, PROBE=probe.replace('"format": {"duration": "4.0"}', '"format": {}'))
        found = self.check(self.clean, fps=30, size="160x90", max_duration_s=9, codec="h264")
        self.assertEqual(
            {(f.rule, f.severity) for f in found if f.rule != "video-check-failed"},
            {("fps", "error"), ("size", "error"), ("duration", "error"), ("codec", "error")},
        )

    def test_unknown_stream_length_is_reported(self):
        audio = {"index": 1, "codec_type": "audio"}
        data = json.loads(self.fake_stream())
        data["streams"].append(audio)
        self.fake("ffprobe", PROBE_ONLY, PROBE=json.dumps(data))
        self.assertEqual(self.ids(self.clean), {"video-check-failed"})

    def test_failed_filters_are_reported_as_warnings(self):
        for body in ("echo boom >&2; exit 1", "exit 0"):
            for key, value in (("max_freeze_s", 2), ("max_black_s", 2), ("loop", True)):
                with self.subTest(body=body, key=key):
                    self.fake("ffmpeg", body)
                    found = self.warning(self.clean, "video-check-failed", **{key: value})
                    self.assertEqual(len(found), 1)

    def test_garbled_filter_times_are_reported(self):
        summary = "echo 'video:1kB audio:0kB' >&2"
        for key, line in (
            ("max_freeze_s", "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: soon"),
            ("max_black_s", "[blackdetect @ 0x1] black_start:a black_end:b black_duration:c"),
        ):
            with self.subTest(key):
                self.fake("ffmpeg", f"echo '{line}' >&2; {summary}")
                self.warning(self.clean, "video-check-failed", **{key: 2})

    def test_a_file_tag_cannot_fake_a_freeze(self):
        self.fake("ffmpeg", "echo 'title : lavfi.freezedetect.freeze_start: 1' >&2; echo 'video:1kB' >&2")
        self.assertEqual(self.ids(self.clean, max_freeze_s=2), set())

    def test_loop_output_of_the_wrong_length_is_reported(self):
        self.fake("ffmpeg", "printf abc")
        self.warning(self.clean, "video-check-failed", loop=True)

    def test_one_frame_cannot_show_a_loop(self):
        self.fake("ffmpeg", "head -c 15552 /dev/zero")
        self.warning(self.clean, "video-check-failed", loop=True)

    def test_failed_audio_extraction_is_reported(self):
        for body in ("echo boom >&2; exit 1", "exit 0"):
            with self.subTest(body):
                self.fake("ffmpeg", body)
                self.warning(self.clean, "video-check-failed", loudness_lufs=-16)


@unittest.skipUnless(REAL_FFMPEG and REAL_FFPROBE, "ffmpeg not installed")
class DispatchTests(VideoCase):
    def repo_with(self, name, source):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        shutil.copy(source, repo / name)
        return repo

    def run_media(self, repo, name, cfg=None):
        return media.check(repo, {name: None}, lambda _: False, cfg or {})

    def test_every_video_extension_is_dispatched(self):
        for ext in sorted(VIDEO_EXTS | {".MP4"}):
            name = f"v{ext}"
            repo = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, repo)
            (repo / name).write_bytes(b"not a video")
            found, checked = self.run_media(repo, name)
            self.assertEqual(checked, 1, name)
            self.assertEqual(rule_ids(found), {"video-unreadable"}, name)

    def test_size_limit_applies_to_video(self):
        repo = self.repo_with("v.mp4", self.clean)
        found, _ = self.run_media(repo, "v.mp4", {"rules": [{"glob": "*.mp4", "max_file_mb": 0.001}]})
        self.assertEqual([(f.rule, f.severity) for f in found], [("file-size", "error")])
        found, _ = self.run_media(repo, "v.mp4")
        self.assertEqual(found, [])

    def test_skip_is_honored(self):
        repo = self.repo_with("v.mp4", self.clean)
        found, checked = self.run_media(repo, "v.mp4", {"rules": [{"glob": "*.mp4", "skip": True}]})
        self.assertEqual((found, checked), ([], 0))

    def test_missing_tool_stops_the_run(self):
        repo = self.repo_with("v.mp4", self.clean)
        for tool in ("ffmpeg", "ffprobe"):
            answers = {"ffmpeg": "/bin/ffmpeg", "ffprobe": "/bin/ffprobe", tool: None}
            with (
                self.subTest(tool),
                mock.patch("slopgate.media.shutil.which", answers.get),
                self.assertRaisesRegex(RuntimeError, f"{tool} is required"),
            ):
                self.run_media(repo, "v.mp4")

    def test_video_is_not_sent_to_the_paid_review(self):
        repo = self.repo_with("v.mp4", self.clean)
        args = mock.Mock(
            repo=str(repo), head="HEAD", base="HEAD~1", models="a/b", max_images=5, report_md=None
        )
        with (
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "unused"}),
            mock.patch.object(cli.gitdiff, "require_checked_out"),
            mock.patch.object(cli.gitdiff, "changed_lines", return_value={"v.mp4": None}),
            mock.patch.object(cli.judge, "run", return_value=[]) as vision,
            mock.patch.object(cli.judge, "report", return_value=""),
            mock.patch.object(cli.listen, "run") as listening,
        ):
            self.assertEqual(cli.run_judge(args), 0)
        self.assertEqual(vision.call_args.args[1], [])
        listening.assert_not_called()

    def test_media_init_drafts_a_video_rule(self):
        repo = self.repo_with("v.mp4", self.clean)
        with mock.patch.object(media_init, "all_files", return_value={"v.mp4": None}):
            text = media_init.draft(repo)
        for expected in (
            'glob: "*.mp4"',
            'size: "160x90"',
            "fps: 30",
            "codec: h264",
            "# max_freeze_s:",
            "# loop: true",
        ):
            self.assertIn(expected, text)

    def test_media_init_skips_an_unreadable_video(self):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        (repo / "bad.mp4").write_bytes(b"not a video")
        with mock.patch.object(media_init, "all_files", return_value={"bad.mp4": None}):
            self.assertNotIn("size:", media_init.draft(repo))


if __name__ == "__main__":
    unittest.main()
