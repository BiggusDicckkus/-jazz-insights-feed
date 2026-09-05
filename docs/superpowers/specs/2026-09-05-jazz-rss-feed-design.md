# Jazz Insights RSS Feed Design

**Date:** 2026-09-05

**Status:** Approved for implementation

## Goal

Publish the podcast episodes embedded on `https://jazzinsightsinfo.com/` as a
stable RSS 2.0 feed that Overcast can subscribe to, with a daily GitHub Actions
refresh so newly published or corrected episodes are picked up automatically.

## Current source

The source site is a WordPress installation. Its public REST API exposes the
published show pages through:

`https://jazzinsightsinfo.com/wp-json/wp/v2/posts?per_page=100`

The live API currently reports 71 posts. Each post includes the page link,
publication dates, title, and rendered HTML content. The API content is not
complete: most current posts contain only the player placeholder, while the
individual post page includes the server-rendered player configuration. Older
pages contain a JavaScript configuration with a JSON array like:

```javascript
playlist: [{"artist":"Max Roach","title":"Episode 1",
"audio":"https://jazzinsightsinfo.com/wp-content/uploads/...mp3"}]
```

The media URLs are direct MP3 files hosted by the source site. The `/shows/`
page also lists show links, but it uses client-side infinite loading; the REST
API is the smaller and more reliable discovery surface. It remains the sole
source of show URLs; the individual URLs are fetched only to read their player
markup when the API representation has no playlist.

The source site currently presents an expired HTTPS certificate. Requests that
reach the exact source host use a narrowly scoped certificate-verification
fallback, while the generated MP3 enclosures use the source's direct HTTP
media URLs, which return `200 audio/mpeg` responses without redirecting.

## Recommended approach

Use a dependency-free Python implementation based on the standard library:

1. Fetch the WordPress REST collection with `per_page=100` and request only
   the fields needed by the feed.
2. Extract a `playlist: [...]` JSON array from the API content when present. If
   it is absent, fetch the post's `link` and parse either the JSON player or the
   newer `fap-in-page-playlist-list` markup. In the latter format, decode each
   base64 anchor `href` into the direct MP3 URL and use the track heading as the
   episode title.
3. Convert each playlist entry into one RSS item. Use the source post title and
   URL as the episode context, the playlist's artist/title as the item title,
   and the post's UTC publication date as `pubDate`.
4. Deduplicate by canonical audio URL, retain source order within each show,
   and order shows newest first.
5. Emit UTF-8 XML to `feed.xml`, including RSS 2.0, Atom self-link, and the
   iTunes fields needed by common podcast clients. Each item gets a stable GUID
   based on its audio URL and an `audio/mpeg` enclosure.

The generator will accept `--output` and `--api-url` options for local testing,
while defaulting to the production API and repository feed URL. Network
fetching will use a descriptive user agent, a finite timeout, and fail the run
if the API cannot be fetched or no audio entries are found. This prevents the
scheduled job from replacing a good feed with an empty one.

The enclosure byte length will be obtained from a lightweight HTTP `HEAD`
request when available and otherwise emitted as `0`; the source URL remains the
authoritative playable resource. A separate validator will parse the generated
XML and optionally check every enclosure with `HEAD`, requiring a successful
response and an audio content type.

## Components and responsibilities

- `generate_feed.py` — API/page fetching, both playlist extraction formats,
  feed model creation, and deterministic RSS serialization.
- `validate_feed.py` — RSS 2.0 structure checks and live enclosure checks for
  the generated artifact.
- `tests/test_feed_generator.py` — fixture-driven parser and serializer tests,
  including malformed/missing playlist handling and stable ordering.
- `.github/workflows/refresh-feed.yml` — daily schedule plus manual dispatch;
  installs the selected Python version, runs tests, generates the feed, checks
  it, and commits/pushes only if `feed.xml` changed.
- `README.md` — subscription URL, local commands, and workflow behavior.

## Data flow

```text
WordPress REST API
        |
        v
post metadata + show URLs
        |
        v
post page HTML when API content lacks a playlist
        |
        v
normalized unique episode records
        |
        v
RSS 2.0 feed.xml  --->  GitHub Pages/raw repository URL ---> Overcast
```

The feed URL is fixed as:

`https://raw.githubusercontent.com/BiggusDicckkus/-jazz-insights-feed/main/feed.xml`

## Failure handling

- HTTP errors, invalid JSON, or an empty result fail the generator with a
  non-zero exit status.
- A failure fetching a discovered post page fails the generator rather than
  silently dropping a show's episodes.
- Posts without a valid playlist are skipped; a run fails if no valid audio
  entries remain.
- Duplicate audio URLs are emitted once.
- An individual enclosure `HEAD` failure does not prevent feed generation, but
  the validator reports it and the scheduled workflow fails before committing
  a feed with broken media URLs.
- GitHub Actions uses `contents: write` and commits as the Actions bot only
  when the generated file differs from the checked-in version.

## Testing and verification

The test suite will use only local fixtures and standard-library test tools so
it can run without external services. It will cover playlist extraction,
metadata mapping, deduplication, XML escaping, stable GUIDs, and the failure
case where no playlist is present.

Before delivery, the implementation will be verified by:

1. Running the complete local test suite.
2. Running the generator against the live WordPress API.
3. Parsing the resulting `feed.xml` and validating RSS 2.0-required channel
   and enclosure fields.
4. Checking each generated enclosure URL against the live source with `HEAD`.
5. Running the workflow's commands locally in the same order.
6. Pushing the implementation and confirming the raw `main/feed.xml` URL
   returns HTTP 200 with valid XML.

## Scope exclusions

The first version will not introduce a database, persistent crawl state,
third-party scraping framework, audio downloads, or a custom web host. The
checked-in XML is the published artifact. The source site's WordPress API is
the sole discovery mechanism for show URLs; discovered post pages are read
only for their embedded player metadata. Certificate verification is never
disabled for hosts other than `jazzinsightsinfo.com`.
