import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from generate_feed import (
    Episode,
    extract_playlist,
    format_duration,
    generate_feed,
    posts_to_episodes,
    render_feed,
    USER_AGENT,
)


PLAYER_HTML = '''
<script>
  $('#player').wptpa({
    playlist: [{"id":"117","artist":"Max Roach","title":"Episode 1",
      "audio":"https://jazzinsightsinfo.com/wp-content/uploads/roach-1.mp3",
      "all_time":"21962"}],
    playlistId: '43'
  });
</script>
'''


class GenerateFeedTests(unittest.TestCase):
    def test_extract_playlist_reads_embedded_player_json(self):
        playlist = extract_playlist(PLAYER_HTML)

        self.assertEqual(playlist[0]["artist"], "Max Roach")
        self.assertEqual(
            playlist[0]["audio"],
            "https://jazzinsightsinfo.com/wp-content/uploads/roach-1.mp3",
        )

    def test_posts_to_episodes_orders_shows_deduplicates_audio_and_preserves_episode_order(self):
        posts = [
            {
                "link": "https://jazzinsightsinfo.com/older/",
                "date_gmt": "2014-01-01T00:00:00",
                "modified_gmt": "2014-01-02T00:00:00",
                "title": {"rendered": "Older"},
                "content": {"rendered": PLAYER_HTML.replace("roach-1", "older-1")},
            },
            {
                "link": "https://jazzinsightsinfo.com/newer/",
                "date_gmt": "2015-01-01T00:00:00",
                "modified_gmt": "2015-01-02T00:00:00",
                "title": {"rendered": "Newer"},
                "content": {"rendered": PLAYER_HTML + PLAYER_HTML},
            },
        ]

        episodes = posts_to_episodes(posts)

        self.assertEqual([episode.show_title for episode in episodes], ["Newer", "Older"])
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0].duration_seconds, 21962)

    def test_posts_to_episodes_rejects_no_valid_audio_entries(self):
        posts = [{
            "link": "https://jazzinsightsinfo.com/empty/",
            "date_gmt": "2015-01-01T00:00:00",
            "modified_gmt": "2015-01-02T00:00:00",
            "title": {"rendered": "Empty"},
            "content": {"rendered": "<p>No player here</p>"},
        }]

        with self.assertRaises(ValueError):
            posts_to_episodes(posts)

    def test_format_duration_uses_hh_mm_ss(self):
        self.assertEqual(format_duration(21962), "06:06:02")
        self.assertIsNone(format_duration(None))

    def test_render_feed_contains_overcast_compatible_items(self):
        episode = Episode(
            title="Max Roach — Episode 1",
            artist="Max Roach",
            show_title="Max Roach",
            page_url="https://jazzinsightsinfo.com/max-roach/",
            audio_url="https://jazzinsightsinfo.com/wp-content/uploads/roach-1.mp3",
            published=datetime(2015, 1, 2, tzinfo=timezone.utc),
            modified=datetime(2015, 1, 3, tzinfo=timezone.utc),
            duration_seconds=21962,
            content_length=123456,
        )

        root = ET.fromstring(render_feed([episode]))
        channel = root.find("channel")
        item = channel.find("item")

        self.assertEqual(root.attrib["version"], "2.0")
        self.assertEqual(channel.findtext("title"), "Jazz Insights")
        self.assertEqual(item.findtext("title"), "Max Roach — Episode 1")
        self.assertEqual(item.find("guid").attrib["isPermaLink"], "false")
        self.assertEqual(item.find("enclosure").attrib["length"], "123456")
        self.assertEqual(item.find("enclosure").attrib["type"], "audio/mpeg")
        self.assertEqual(
            item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration").text,
            "06:06:02",
        )

    def test_render_feed_is_deterministic_and_escapes_text(self):
        episode = Episode(
            title="A & B — Episode <1>",
            artist="A & B",
            show_title="Show <1>",
            page_url="https://jazzinsightsinfo.com/show/one?x=1&y=2",
            audio_url="https://cdn.example.test/audio.mp3?x=1&y=2",
            published=datetime(2015, 1, 2, tzinfo=timezone.utc),
            modified=datetime(2015, 1, 3, tzinfo=timezone.utc),
        )

        rendered = render_feed([episode])

        self.assertEqual(rendered, render_feed([episode]))
        ET.fromstring(rendered)

    def test_generate_feed_fetches_api_and_head_lengths(self):
        class FakeResponse:
            def __init__(self, body=b"", headers=None, status=200):
                self.body = body
                self.headers = headers or {}
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.body

        api_url = "https://api.example.test/posts"
        audio_url = "https://audio.example.test/audio.mp3"
        body = json.dumps([{
            "link": "https://jazzinsightsinfo.com/show/",
            "date_gmt": "2015-01-01T00:00:00",
            "modified_gmt": "2015-01-02T00:00:00",
            "title": {"rendered": "Show"},
            "content": {"rendered": PLAYER_HTML.replace(
                "https://jazzinsightsinfo.com/wp-content/uploads/roach-1.mp3",
                audio_url,
            )},
        }]).encode()
        responses = [
            FakeResponse(body, {"Content-Type": "application/json"}),
            FakeResponse(headers={"Content-Type": "audio/mpeg", "Content-Length": "123"}),
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return responses.pop(0)

        with patch("generate_feed.urlopen", side_effect=fake_urlopen):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "feed.xml"
                episodes = generate_feed(api_url, output)
                rendered = output.read_bytes()

        self.assertEqual(len(episodes), 1)
        self.assertIn(b'length="123"', rendered)
        self.assertEqual(requests[0][0].full_url, api_url)
        self.assertEqual(requests[0][0].method, "GET")
        self.assertEqual(requests[0][0].get_header("User-agent"), USER_AGENT)
        self.assertEqual(requests[0][1], 30)
        self.assertEqual(requests[1][0].full_url, audio_url)
        self.assertEqual(requests[1][0].method, "HEAD")


if __name__ == "__main__":
    unittest.main()
