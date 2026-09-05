import unittest

from generate_feed import extract_playlist, posts_to_episodes


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


if __name__ == "__main__":
    unittest.main()
