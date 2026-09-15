"""Tests for ffmpeg_helper.py's is_video_file()."""

import unittest
from unittest.mock import patch, MagicMock

from src.modules.ffmpeg_helper import is_video_file


def _fake_result(returncode, stdout):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


class IsVideoFileTest(unittest.TestCase):
    """ffprobe reports an mp3's embedded ID3 cover-art image as a genuine
    "video" stream (codec_type=video, codec mjpeg) - without checking the
    attached_pic disposition flag, is_video_file() wrongly treats a plain
    audio file with cover art as a video file, which then makes
    separate_audio_video() try to ffmpeg-extract audio from the file into
    itself (found live 2026-09-15 on a real mp3 with embedded artwork:
    "FFmpeg cannot edit existing files in-place")."""

    @patch("src.modules.ffmpeg_helper.get_ffmpeg_and_ffprobe_paths",
          return_value=("ffmpeg", "ffprobe"))
    @patch("src.modules.ffmpeg_helper.subprocess.run")
    def test_real_video_stream_is_detected_as_video(self, mock_run, _mock_paths):
        mock_run.return_value = _fake_result(
            0, "codec_type=video\ncodec_name=h264\nDISPOSITION:attached_pic=0\n")
        self.assertTrue(is_video_file("song.mp4"))

    @patch("src.modules.ffmpeg_helper.get_ffmpeg_and_ffprobe_paths",
          return_value=("ffmpeg", "ffprobe"))
    @patch("src.modules.ffmpeg_helper.subprocess.run")
    def test_mp3_with_embedded_cover_art_is_not_a_video_file(self, mock_run, _mock_paths):
        mock_run.return_value = _fake_result(
            0, "codec_type=video\ncodec_name=mjpeg\nDISPOSITION:attached_pic=1\n")
        self.assertFalse(is_video_file("song.mp3"))

    @patch("src.modules.ffmpeg_helper.get_ffmpeg_and_ffprobe_paths",
          return_value=("ffmpeg", "ffprobe"))
    @patch("src.modules.ffmpeg_helper.subprocess.run")
    def test_pure_audio_file_with_no_video_stream_is_not_a_video_file(self, mock_run, _mock_paths):
        mock_run.return_value = _fake_result(1, "")
        self.assertFalse(is_video_file("song.mp3"))

    @patch("src.modules.ffmpeg_helper.get_ffmpeg_and_ffprobe_paths",
          side_effect=FileNotFoundError("no ffprobe"))
    def test_missing_ffprobe_does_not_raise(self, _mock_paths):
        self.assertFalse(is_video_file("song.mp4"))


if __name__ == "__main__":
    unittest.main()
