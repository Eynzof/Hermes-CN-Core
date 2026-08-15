"""Regression test for salvaged PR #43911 — microsecond TTS output timestamps.

Default output paths used second-resolution ``%Y%m%d_%H%M%S`` timestamps, so
two text_to_speech_tool calls landing in the same wall-clock second produced
the same filename and the second synthesis overwrote the first. The format
now appends ``%f`` (microseconds).
"""

import datetime
import re


class TestDefaultOutputTimestampResolution:
    def test_timestamp_component_is_filename_safe(self):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        assert re.fullmatch(r"\d{8}_\d{6}_\d{6}", stamp)
