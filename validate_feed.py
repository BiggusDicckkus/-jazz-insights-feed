import argparse
import sys
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from generate_feed import _request


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
                    status = getattr(response, "status", None) or response.getcode()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if status < 200 or status >= 300:
                        errors.append(f"item {number} enclosure returned HTTP {status}")
                    elif not content_type.startswith("audio/"):
                        errors.append(
                            f"item {number} enclosure returned {content_type or 'no content type'}"
                        )
            except OSError as exc:
                errors.append(f"item {number} enclosure is unreachable: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Jazz Insights RSS feed")
    parser.add_argument("feed_path")
    parser.add_argument("--check-enclosures", action="store_true")
    args = parser.parse_args()
    with open(args.feed_path, "rb") as feed_file:
        errors = validate_feed(feed_file.read(), args.check_enclosures)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"valid RSS 2.0 feed: {args.feed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
