import unittest

from validate_feed import validate_feed


VALID = b'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Jazz Insights</title><link>https://jazzinsightsinfo.com/</link>
  <description>Episodes</description>
  <item><title>Episode 1</title><guid isPermaLink="false">https://cdn.test/1.mp3</guid>
    <pubDate>Thu, 01 Jan 2015 00:00:00 GMT</pubDate>
    <enclosure url="https://cdn.test/1.mp3" length="123" type="audio/mpeg" />
  </item>
</channel></rss>'''


class ValidateFeedTests(unittest.TestCase):
    def test_valid_feed_has_no_errors_without_network_checks(self):
        self.assertEqual(validate_feed(VALID), [])

    def test_validator_reports_missing_enclosure_attributes(self):
        invalid = VALID.replace(
            b'length="123" type="audio/mpeg"',
            b'length="0" type="text/plain"',
        )

        errors = validate_feed(invalid)

        self.assertTrue(any("positive length" in error for error in errors))
        self.assertTrue(any("audio" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
