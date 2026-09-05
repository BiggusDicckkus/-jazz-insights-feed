# -jazz-insights-feed

An RSS feed for the podcast episodes published on Jazz Insights.

## Subscribe

Add this URL to Overcast or another podcast app:

https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml

## Local refresh

The generator uses the public Jazz Insights WordPress API and writes the
checked-in feed artifact:

```sh
python generate_feed.py --output feed.xml
python validate_feed.py feed.xml --check-enclosures
```

The GitHub Actions workflow refreshes the feed daily at 03:17 UTC and can also
be started manually. It runs the test suite and validates every enclosure
before committing a changed `feed.xml`.

The source site currently presents an expired HTTPS certificate. The generator
therefore limits certificate verification bypasses to the known source API host
and uses the source server's direct HTTP MP3 URLs for playable enclosures; no
other host is allowed through that fallback.
