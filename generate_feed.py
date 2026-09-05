import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlsplit, urlunsplit


PLAYLIST_RE = re.compile(
    r"playlist\s*:\s*(\[.*?\])\s*,\s*playlistId",
    re.DOTALL,
)


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
