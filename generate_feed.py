import argparse
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import format_datetime
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET


PLAYLIST_RE = re.compile(
    r"playlist\s*:\s*(\[.*?\])\s*,\s*playlistId",
    re.DOTALL,
)
FEED_URL = "https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml"
SITE_URL = "https://jazzinsightsinfo.com/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
API_URL = (
    "https://jazzinsightsinfo.com/wp-json/wp/v2/posts?per_page=100&"
    "_fields=link,date,date_gmt,modified,modified_gmt,title,content"
)
USER_AGENT = "jazz-insights-feed/1.0"


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
    if not isinstance(playlist, list):
        return []
    return [entry for entry in playlist if isinstance(entry, dict)]


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value: object) -> str:
    return unescape(str(value)).strip() if value is not None else ""


def _duration(value: object) -> int | None:
    try:
        seconds = int(str(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _canonical_audio_url(value: str) -> str:
    parts = urlsplit(value)
    hostname = parts.hostname.lower() if parts.hostname else ""
    if parts.port is not None:
        hostname = f"{hostname}:{parts.port}"
    netloc = f"{parts.username}:{parts.password}@" if parts.username or parts.password else ""
    netloc += hostname
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def posts_to_episodes(posts: list[dict[str, object]]) -> list[Episode]:
    ordered_posts = sorted(
        posts,
        key=lambda post: post.get("date_gmt") or post.get("date") or "",
        reverse=True,
    )
    episodes: list[Episode] = []
    seen_urls: set[str] = set()
    for post in ordered_posts:
        title_data = post.get("title") or {}
        content_data = post.get("content") or {}
        post_title = _text(title_data.get("rendered")) if isinstance(title_data, dict) else ""
        page_url = _text(post.get("link"))
        published = _parse_datetime(_text(post.get("date_gmt") or post.get("date")))
        modified = _parse_datetime(
            _text(post.get("modified_gmt") or post.get("modified") or post.get("date_gmt"))
        )
        content = _text(content_data.get("rendered")) if isinstance(content_data, dict) else ""
        for item in extract_playlist(content):
            artist = _text(item.get("artist"))
            item_title = _text(item.get("title"))
            audio_url = _canonical_audio_url(_text(item.get("audio")))
            parsed_url = urlsplit(audio_url)
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
    ET.SubElement(channel, "description").text = (
        "Jazz Insights podcast episodes from jazzinsightsinfo.com."
    )
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {
        "href": feed_url,
        "rel": "self",
        "type": "application/rss+xml",
    })
    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = "Jazz Insights"
    ET.SubElement(channel, f"{{{ITUNES_NS}}}summary").text = "Jazz Insights podcast episodes."
    ET.SubElement(channel, f"{{{ITUNES_NS}}}explicit").text = "no"
    category = ET.SubElement(channel, f"{{{ITUNES_NS}}}category", {"text": "Music"})
    ET.SubElement(category, f"{{{ITUNES_NS}}}category", {"text": "Jazz"})
    if episodes:
        latest_modified = max(episode.modified for episode in episodes)
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(
            latest_modified,
            usegmt=True,
        )
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


def generate_feed(
    api_url: str = API_URL,
    output_path: Path = Path("feed.xml"),
) -> list[Episode]:
    episodes = posts_to_episodes(fetch_posts(api_url))
    sized = [
        replace(episode, content_length=fetch_content_length(episode.audio_url))
        for episode in episodes
    ]
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
