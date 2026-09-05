# Jazz Insights RSS Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a deterministic Overcast-compatible RSS feed from the Jazz Insights WordPress API, with tests, live enclosure validation, and a daily GitHub Actions refresh.

**Architecture:** A dependency-free Python module fetches published WordPress posts, extracts the JSON playlist embedded in each post's rendered HTML, normalizes unique episodes, and serializes deterministic RSS 2.0 XML. A separate validator checks the artifact and optionally performs live `HEAD` checks on every enclosure; GitHub Actions runs both programs daily and commits only changed `feed.xml`.

**Tech Stack:** Python 3.12 standard library (`urllib`, `json`, `dataclasses`, `xml.etree.ElementTree`, `unittest`), GitHub Actions, RSS 2.0 with Atom and iTunes namespaces.

**Spec:** `docs/superpowers/specs/2026-09-05-jazz-rss-feed-design.md`

## Global Constraints

- Use the public WordPress REST API at `https://jazzinsightsinfo.com/wp-json/wp/v2/posts?per_page=100` as the sole discovery surface.
- Use a dependency-free Python implementation based on the standard library.
- Emit UTF-8 RSS 2.0 XML to the checked-in `feed.xml` artifact.
- Use the fixed feed URL `https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml` as the Atom self-link.
- Fail generation if the API cannot be fetched or no valid audio entries remain.
- Skip posts without valid playlists and deduplicate by canonical audio URL.
- Run tests from local fixtures without external services.
- The workflow must use `contents: write` and commit only when `feed.xml` changes.

---

### Task 1: Extract and normalize source playlists

**Files:**
- Create: `tests/test_generate_feed.py`
- Create: `generate_feed.py`

**Interfaces:**
- Produces `extract_playlist(content_html: str) -> list[dict[str, object]]`.
- Produces `posts_to_episodes(posts: list[dict[str, object]]) -> list[Episode]`.
- Produces `Episode` with fields `title`, `artist`, `show_title`, `page_url`, `audio_url`, `published`, `modified`, `duration_seconds`, and `content_length`.

- [ ] **Step 1: Write the failing extraction and normalization tests**

Add a local fixture with the same player shape observed on the live site and tests for the public behavior:

```python
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
        self.assertEqual(playlist[0]["audio"],
                         "https://jazzinsightsinfo.com/wp-content/uploads/roach-1.mp3")

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

        self.assertEqual([episode.show_title for episode in episodes],
                         ["Newer", "Older"])
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
```

- [ ] **Step 2: Run the tests to verify they fail for the missing module**

Run: `python -m unittest tests.test_generate_feed -v`

Expected: FAIL because `generate_feed` and its public interfaces do not yet exist.

- [ ] **Step 3: Implement the minimal pure parser and normalizer**

Create `generate_feed.py` with the constants, frozen `Episode` dataclass, and these implementations:

```python
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlparse


PLAYLIST_RE = re.compile(r"playlist\s*:\s*(\[.*?\])\s*,\s*playlistId", re.DOTALL)


@dataclass(frozen=True)
class Episode:
    title: str
    artist: str
    show_title: str
    page_url: str
    audio_url: str
    published: datetime
    modified: datetime
    duration_seconds: int | None = None
    content_length: int = 0


def extract_playlist(content_html: str) -> list[dict[str, object]]:
    match = PLAYLIST_RE.search(unescape(content_html))
    if not match:
        return []
    try:
        playlist = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return playlist if isinstance(playlist, list) else []


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _duration(value: object) -> int | None:
    try:
        seconds = int(str(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def posts_to_episodes(posts: list[dict[str, object]]) -> list[Episode]:
    ordered_posts = sorted(posts, key=lambda post: post.get("date_gmt") or post.get("date") or "", reverse=True)
    episodes: list[Episode] = []
    seen_urls: set[str] = set()
    for post in ordered_posts:
        post_title = _text((post.get("title") or {}).get("rendered"))
        page_url = _text(post.get("link"))
        published = _parse_datetime(_text(post.get("date_gmt") or post.get("date")))
        modified = _parse_datetime(_text(post.get("modified_gmt") or post.get("modified") or post.get("date_gmt")))
        content = _text((post.get("content") or {}).get("rendered"))
        for item in extract_playlist(content):
            artist = _text(item.get("artist"))
            item_title = _text(item.get("title"))
            audio_url = _text(item.get("audio"))
            parsed_url = urlparse(audio_url)
            if not item_title or not audio_url or parsed_url.scheme not in {"http", "https"}:
                continue
            if audio_url in seen_urls:
                continue
            seen_urls.add(audio_url)
            episodes.append(Episode(
                title=f"{artist} — {item_title}" if artist else item_title,
                artist=artist,
                show_title=post_title,
                page_url=page_url,
                audio_url=audio_url,
                published=published,
                modified=modified,
                duration_seconds=_duration(item.get("all_time")),
            ))
    if not episodes:
        raise ValueError("no valid audio entries found")
    return episodes
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `python -m unittest tests.test_generate_feed -v`

Expected: PASS for all extraction, ordering, deduplication, and empty-input tests.

- [ ] **Step 5: Commit the tested parser**

```bash
git add generate_feed.py tests/test_generate_feed.py
git commit -m "feat: extract Jazz Insights playlists"
```

### Task 2: Serialize deterministic RSS 2.0

**Files:**
- Modify: `tests/test_generate_feed.py`
- Modify: `generate_feed.py`

**Interfaces:**
- Produces `render_feed(episodes: list[Episode], feed_url: str = FEED_URL) -> bytes`.
- Produces `format_duration(seconds: int | None) -> str | None`.

- [ ] **Step 1: Write failing serializer tests**

Extend the test module with assertions against parsed XML:

```python
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from generate_feed import Episode, format_duration, render_feed


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
        self.assertEqual(item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration").text,
                         "06:06:02")

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

        self.assertEqual(render_feed([episode]), render_feed([episode]))
        ET.fromstring(render_feed([episode]))
```

- [ ] **Step 2: Run the serializer tests to verify they fail**

Run: `python -m unittest tests.test_generate_feed.GenerateFeedTests.test_format_duration tests.test_generate_feed.GenerateFeedTests.test_render_feed_contains_overcast_compatible_items tests.test_generate_feed.GenerateFeedTests.test_render_feed_is_deterministic_and_escapes_text -v`

Expected: FAIL because the serializer interfaces are not implemented.

- [ ] **Step 3: Implement the minimal RSS serializer**

Add the Atom and iTunes namespace constants and implement:

```python
from email.utils import format_datetime
from xml.etree import ElementTree as ET


FEED_URL = "https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml"
SITE_URL = "https://jazzinsightsinfo.com/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def format_duration(seconds: int | None) -> str | None:
    if seconds is None or seconds <= 0:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def render_feed(episodes: list[Episode], feed_url: str = FEED_URL) -> bytes:
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("itunes", ITUNES_NS)
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "Jazz Insights"
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = "Jazz Insights podcast episodes from jazzinsightsinfo.com."
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {"href": feed_url, "rel": "self", "type": "application/rss+xml"})
    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = "Jazz Insights"
    ET.SubElement(channel, f"{{{ITUNES_NS}}}summary").text = "Jazz Insights podcast episodes."
    ET.SubElement(channel, f"{{{ITUNES_NS}}}explicit").text = "no"
    category = ET.SubElement(channel, f"{{{ITUNES_NS}}}category", {"text": "Music"})
    ET.SubElement(category, f"{{{ITUNES_NS}}}category", {"text": "Jazz"})
    if episodes:
        latest_modified = max(episode.modified for episode in episodes)
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(latest_modified, usegmt=True)
    for episode in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode.title
        ET.SubElement(item, "description").text = f"{episode.show_title}: {episode.title}"
        ET.SubElement(item, "link").text = episode.page_url
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = episode.audio_url
        ET.SubElement(item, "pubDate").text = format_datetime(episode.published, usegmt=True)
        ET.SubElement(item, "enclosure", {
            "url": episode.audio_url,
            "length": str(episode.content_length),
            "type": "audio/mpeg",
        })
        ET.SubElement(item, f"{{{ITUNES_NS}}}author").text = episode.artist or "Jazz Insights"
        duration = format_duration(episode.duration_seconds)
        if duration:
            ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = duration
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
```

- [ ] **Step 4: Run the full local test module**

Run: `python -m unittest tests.test_generate_feed -v`

Expected: PASS with no warnings or errors.

- [ ] **Step 5: Commit the RSS serializer**

```bash
git add generate_feed.py tests/test_generate_feed.py
git commit -m "feat: render deterministic RSS feed"
```

### Task 3: Add live fetching, enclosure lengths, and CLI generation

**Files:**
- Modify: `tests/test_generate_feed.py`
- Modify: `generate_feed.py`

**Interfaces:**
- Produces `fetch_posts(api_url: str = API_URL) -> list[dict[str, object]]`.
- Produces `fetch_content_length(audio_url: str) -> int`.
- Produces `generate_feed(api_url: str, output_path: pathlib.Path) -> list[Episode]`.
- Produces a command-line entry point accepting `--api-url` and `--output`.

- [ ] **Step 1: Write a local HTTP-server integration test**

Add a `unittest` test that starts `http.server.ThreadingHTTPServer` on an ephemeral port, serves a JSON WordPress response for `/posts`, serves `Content-Length: 123` and `Content-Type: audio/mpeg` for `/audio.mp3`, calls `generate_feed`, and asserts that the output file contains one enclosure with `length="123"`. This exercises the real `urllib` boundary without external network access.

```python
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from generate_feed import generate_feed


    def test_generate_feed_fetches_api_and_head_lengths(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps([{
                    "link": f"http://127.0.0.1:{self.server.server_port}/show/",
                    "date_gmt": "2015-01-01T00:00:00",
                    "modified_gmt": "2015-01-02T00:00:00",
                    "title": {"rendered": "Show"},
                    "content": {"rendered": PLAYER_HTML.replace(
                        "https://jazzinsightsinfo.com/wp-content/uploads/roach-1.mp3",
                        f"http://127.0.0.1:{self.server.server_port}/audio.mp3")},
                }]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", "123")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            server_url = f"http://127.0.0.1:{server.server_port}"
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "feed.xml"
                episodes = generate_feed(f"{server_url}/posts", output)
                self.assertEqual(len(episodes), 1)
                self.assertIn(b'length="123"', output.read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
```

- [ ] **Step 2: Run the integration test to verify it fails**

Run: `python -m unittest tests.test_generate_feed.GenerateFeedTests.test_generate_feed_fetches_api_and_head_lengths -v`

Expected: FAIL because the network functions and `generate_feed` entry point are not implemented.

- [ ] **Step 3: Implement standard-library HTTP fetching and CLI**

Use `urllib.request.Request` with `User-Agent: jazz-insights-feed/1.0` and a 30-second timeout. Decode API bytes as UTF-8 and raise `RuntimeError` for HTTP/JSON failures. For enclosure lengths, return the positive `Content-Length` header and return `0` when the header is absent or a `HEAD` request fails; the validator will separately report broken live URLs.

```python
import argparse
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://jazzinsightsinfo.com/wp-json/wp/v2/posts?per_page=100&_fields=link,date,date_gmt,modified,modified_gmt,title,content"
USER_AGENT = "jazz-insights-feed/1.0"


def _request(url: str, method: str = "GET"):
    request = Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urlopen(request, timeout=30)


def fetch_posts(api_url: str = API_URL) -> list[dict[str, object]]:
    try:
        with _request(api_url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to fetch WordPress posts: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("WordPress API response is not a list")
    return payload


def fetch_content_length(audio_url: str) -> int:
    try:
        with _request(audio_url, method="HEAD") as response:
            value = int(response.headers.get("Content-Length", "0"))
            return value if value > 0 else 0
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        return 0


def generate_feed(api_url: str = API_URL, output_path: Path = Path("feed.xml")) -> list[Episode]:
    episodes = posts_to_episodes(fetch_posts(api_url))
    sized = [replace(episode, content_length=fetch_content_length(episode.audio_url)) for episode in episodes]
    output_path.write_bytes(render_feed(sized))
    return sized


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Jazz Insights RSS feed")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--output", type=Path, default=Path("feed.xml"))
    args = parser.parse_args()
    episodes = generate_feed(args.api_url, args.output)
    print(f"wrote {len(episodes)} episodes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run all local tests**

Run: `python -m unittest discover -s tests -v`

Expected: PASS for parser, serializer, and local HTTP integration tests.

- [ ] **Step 5: Commit live generation support**

```bash
git add generate_feed.py tests/test_generate_feed.py
git commit -m "feat: generate feed from live WordPress API"
```

### Task 4: Add RSS and enclosure validator

**Files:**
- Create: `tests/test_validate_feed.py`
- Create: `validate_feed.py`

**Interfaces:**
- Produces `validate_feed(xml_bytes: bytes, check_enclosures: bool = False) -> list[str]`.
- Produces CLI arguments `feed_path` and `--check-enclosures`; exits non-zero when any error is returned.

- [ ] **Step 1: Write failing validator tests**

Create tests for a valid minimal feed and for missing required fields:

```python
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
        invalid = VALID.replace(b'length="123" type="audio/mpeg"', b'length="0" type="text/plain"')

        errors = validate_feed(invalid)

        self.assertTrue(any("positive length" in error for error in errors))
        self.assertTrue(any("audio" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run validator tests to verify they fail**

Run: `python -m unittest tests.test_validate_feed -v`

Expected: FAIL because `validate_feed` is not implemented.

- [ ] **Step 3: Implement structural and optional live URL checks**

Parse with `xml.etree.ElementTree.fromstring`. Require RSS root version `2.0`, channel `title`, `link`, and `description`, at least one item, and for every item a title, GUID, RFC 822-compatible `pubDate`, and enclosure with HTTP(S) URL, positive numeric length, and `audio/*` type. When `check_enclosures=True`, issue `HEAD` with the same user agent and require a 2xx response plus an audio content type; collect all errors instead of stopping at the first one.

```python
import argparse
import sys
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from generate_feed import USER_AGENT, _request


def validate_feed(xml_bytes: bytes, check_enclosures: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return [f"invalid XML: {exc}"]
    if root.tag != "rss" or root.attrib.get("version") != "2.0":
        errors.append("root must be RSS version 2.0")
    channel = root.find("channel")
    if channel is None:
        return errors + ["missing channel"]
    for field in ("title", "link", "description"):
        if not (channel.findtext(field) or "").strip():
            errors.append(f"missing channel {field}")
    items = channel.findall("item")
    if not items:
        errors.append("feed has no items")
    for number, item in enumerate(items, start=1):
        for field in ("title", "guid", "pubDate"):
            if not (item.findtext(field) or "").strip():
                errors.append(f"item {number} missing {field}")
        try:
            parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            errors.append(f"item {number} has invalid pubDate")
        enclosure = item.find("enclosure")
        if enclosure is None:
            errors.append(f"item {number} missing enclosure")
            continue
        url = enclosure.attrib.get("url", "")
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"}:
            errors.append(f"item {number} enclosure URL is not HTTP(S)")
        try:
            if int(enclosure.attrib.get("length", "0")) <= 0:
                errors.append(f"item {number} enclosure has no positive length")
        except ValueError:
            errors.append(f"item {number} enclosure length is not numeric")
        if not enclosure.attrib.get("type", "").startswith("audio/"):
            errors.append(f"item {number} enclosure is not audio")
        if check_enclosures and parsed_url.scheme in {"http", "https"}:
            try:
                with _request(url, method="HEAD") as response:
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if response.status < 200 or response.status >= 300:
                        errors.append(f"item {number} enclosure returned HTTP {response.status}")
                    elif not content_type.startswith("audio/"):
                        errors.append(f"item {number} enclosure returned {content_type or 'no content type'}")
            except OSError as exc:
                errors.append(f"item {number} enclosure is unreachable: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Jazz Insights RSS feed")
    parser.add_argument("feed_path")
    parser.add_argument("--check-enclosures", action="store_true")
    args = parser.parse_args()
    errors = validate_feed(open(args.feed_path, "rb").read(), args.check_enclosures)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"valid RSS 2.0 feed: {args.feed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run all local tests**

Run: `python -m unittest discover -s tests -v`

Expected: PASS with no warnings or errors.

- [ ] **Step 5: Commit the validator**

```bash
git add validate_feed.py tests/test_validate_feed.py
git commit -m "feat: validate RSS feed and enclosures"
```

### Task 5: Add refresh workflow, documentation, and checked-in feed

**Files:**
- Create: `.github/workflows/refresh-feed.yml`
- Modify: `README.md`
- Create: `feed.xml` by running the generator

**Interfaces:**
- Workflow runs `python -m unittest discover -s tests -v`, `python generate_feed.py --output feed.xml`, and `python validate_feed.py feed.xml --check-enclosures`.
- Public subscription URL is `https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml`.

- [ ] **Step 1: Write the workflow and README expectations as a repository test**

Add `tests/test_repository_config.py` with assertions that the workflow contains `schedule`, `workflow_dispatch`, `contents: write`, `git push`, the test command, the generator command, and the validator command, and that the README contains the raw feed URL.

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class RepositoryConfigTests(unittest.TestCase):
    def test_refresh_workflow_has_daily_write_and_validation_steps(self):
        workflow = (ROOT / ".github/workflows/refresh-feed.yml").read_text()
        for text in ("schedule:", "workflow_dispatch:", "contents: write",
                     "python -m unittest discover -s tests -v",
                     "python generate_feed.py --output feed.xml",
                     "python validate_feed.py feed.xml --check-enclosures",
                     "git push"):
            self.assertIn(text, workflow)

    def test_readme_has_subscription_url(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml", readme)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the config tests to verify they fail**

Run: `python -m unittest tests.test_repository_config -v`

Expected: FAIL because the workflow and README have not been updated.

- [ ] **Step 3: Implement the scheduled workflow and documentation**

Create `.github/workflows/refresh-feed.yml`:

```yaml
name: Refresh Jazz Insights feed

on:
  schedule:
    - cron: "17 3 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m unittest discover -s tests -v
      - run: python generate_feed.py --output feed.xml
      - run: python validate_feed.py feed.xml --check-enclosures
      - name: Commit changed feed
        run: |
          if git diff --quiet -- feed.xml; then
            echo "feed.xml is unchanged"
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add feed.xml
          git commit -m "chore: refresh Jazz Insights feed"
          git push
```

Update `README.md` with the subscription URL, local generation commands, and
the note that the workflow runs daily and can also be dispatched manually.

- [ ] **Step 4: Run config tests and syntax checks**

Run: `python -m unittest discover -s tests -v` and `python -m py_compile generate_feed.py validate_feed.py`

Expected: PASS with no warnings or errors.

- [ ] **Step 5: Generate and structurally validate the checked-in feed against the live site**

Run: `python generate_feed.py --output feed.xml` followed by `python validate_feed.py feed.xml --check-enclosures`.

Expected: the generator reports a positive episode count, the validator reports a valid RSS 2.0 feed, and no enclosure URL errors are reported.

- [ ] **Step 6: Review the complete diff and commit the implementation**

Run: `git diff --check`, `git status --short`, and `git diff --stat`.

Expected: only the planned crawler, validator, tests, workflow, README, and generated feed are changed.

```bash
git add .github/workflows/refresh-feed.yml README.md feed.xml generate_feed.py validate_feed.py tests
git commit -m "feat: publish Jazz Insights RSS feed"
```

### Task 6: Push, run, and verify the published workflow and raw feed

**Files:**
- No source changes expected; use the committed repository state.

**Interfaces:**
- Published artifact: `https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml`.

- [ ] **Step 1: Push `main` to the configured GitHub remote**

Run: `git push origin main`

Expected: the implementation commits and the generated feed are accepted by GitHub.

- [ ] **Step 2: Trigger the workflow manually**

Use the repository’s authenticated GitHub workflow-dispatch mechanism for `.github/workflows/refresh-feed.yml` on `main`. If the GitHub CLI is available and authenticated, run:

```bash
gh workflow run refresh-feed.yml --ref main
```

Expected: GitHub accepts the dispatch and creates a run for the `main` branch.

- [ ] **Step 3: Wait for the workflow run and inspect its conclusion**

Use the authenticated run-status mechanism (for example `gh run watch --exit-status` after selecting the dispatched run) and inspect the logs for the test, generation, validator, and commit steps.

Expected: the workflow concludes successfully; if `feed.xml` is unchanged, the commit step reports that explicitly, otherwise it pushes the refreshed artifact.

- [ ] **Step 4: Fetch and validate the public raw URL**

Run: `curl -fsSL https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml -o /tmp/jazz-insights-feed.xml` followed by `python validate_feed.py /tmp/jazz-insights-feed.xml`.

Expected: HTTP 200, valid XML, RSS version 2.0, and all generated items have valid enclosure attributes.

- [ ] **Step 5: Confirm final repository state**

Run: `git status --short --branch` and `git log --oneline -5`.

Expected: `main` is clean and includes the implementation commit(s); report the exact commit IDs, episode count, workflow run result, and public feed URL.

## Plan amendment from live source inspection

The live WordPress API exposed 71 post URLs but only one post's rendered API
content contained player markup. The implementation therefore fetches each
discovered post page when its API content has no playlist, then supports both
the older inline JSON player and the newer base64-encoded `fap` player links.
The execution sandbox also forbids localhost socket binding, so the HTTP
integration test uses a fake `urlopen` boundary while preserving request and
response assertions.
