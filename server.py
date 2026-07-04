#!/usr/bin/env python3
"""Kidstream local approved-video portal.

The server intentionally has no third-party dependencies. It reads a local JSON
catalog, serves the kid-facing app, and only exposes videos that are explicitly
listed in that catalog.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
DEFAULT_CONFIG = ROOT / "config" / "videos.local.json"
EXAMPLE_CONFIG = ROOT / "config" / "videos.example.json"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
YOUTUBE_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def env_value(name: str, default: str | None = None) -> str | None:
    if name in os.environ:
        return os.environ[name]
    return default


FEED_CACHE_SECONDS = int(env_value("KIDSTREAM_FEED_CACHE_SECONDS", "900"))
FEED_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
SEARCH_VIDEO_CACHE: dict[str, dict[str, Any]] = {}
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def config_path() -> Path:
    configured = env_value("KIDSTREAM_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    if DEFAULT_CONFIG.exists():
        return DEFAULT_CONFIG
    return EXAMPLE_CONFIG


def json_error(message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> tuple[int, dict[str, str]]:
    return int(status), {"error": message}


def clean_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def clean_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def clean_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def load_raw_config() -> dict[str, Any]:
    path = config_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise ValueError(f"Config file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Config JSON error at line {error.lineno}, column {error.colno}: {error.msg}") from error


def youtube_api_key(raw: dict[str, Any]) -> str:
    return clean_string(os.environ.get("YOUTUBE_API_KEY")) or clean_string(raw.get("youtubeApiKey"))


def fetch_json_url(url: str, params: dict[str, str], timeout: int = 12) -> dict[str, Any]:
    request = urllib.request.Request(f"{url}?{urlencode(params)}", headers={"User-Agent": "Kidstream/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def write_raw_config(raw: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(raw, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
        temp_name = handle.name
    shutil.move(temp_name, path)


def fetch_channel_feed(channel_youtube_id: str) -> list[dict[str, str]]:
    now = time.time()
    cached = FEED_CACHE.get(channel_youtube_id)
    if cached and now - cached[0] < FEED_CACHE_SECONDS:
        return cached[1]

    request = urllib.request.Request(feed_url(channel_youtube_id), headers={"User-Agent": "Kidstream/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read()
    except urllib.error.URLError:
        if cached:
            return cached[1]
        return []

    root = ET.fromstring(body)
    videos: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        video_id = clean_string(entry.findtext("yt:videoId", default="", namespaces=ATOM_NS))
        title = clean_string(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
        published = clean_string(entry.findtext("atom:published", default="", namespaces=ATOM_NS))
        link_node = entry.find("atom:link", ATOM_NS)
        link = clean_string(link_node.attrib.get("href")) if link_node is not None else ""
        media_group = entry.find("media:group", ATOM_NS)
        description = ""
        thumbnail = ""
        if media_group is not None:
            description = clean_string(media_group.findtext("media:description", default="", namespaces=ATOM_NS))
            thumbnail_node = media_group.find("media:thumbnail", ATOM_NS)
            if thumbnail_node is not None:
                thumbnail = clean_string(thumbnail_node.attrib.get("url"))
        if VIDEO_ID_RE.match(video_id) and title:
            videos.append(
                {
                    "youtubeId": video_id,
                    "title": title,
                    "description": description,
                    "duration": "",
                    "published": published,
                    "thumbnail": thumbnail or thumbnail_url(video_id),
                    "tags": [],
                    "isShort": "/shorts/" in link,
                }
            )

    FEED_CACHE[channel_youtube_id] = (now, videos)
    return videos


def normalize_catalog(raw: dict[str, Any]) -> dict[str, Any]:
    channels: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
    seen_video_ids: set[str] = set()
    seen_channel_ids: set[str] = set()
    default_playback_rate = clean_float(raw.get("defaultPlaybackRate"), 0.75, 0.25, 2.0)
    recent_videos_per_channel = clean_int(
        env_value(
            "KIDSTREAM_RECENT_VIDEOS_PER_CHANNEL",
            raw.get("recentVideosPerChannel"),
        ),
        15,
        0,
        50,
    )

    if not isinstance(raw.get("channels"), list):
        raise ValueError("Config must contain a channels array.")

    for channel in raw["channels"]:
        if not isinstance(channel, dict):
            raise ValueError("Each channel must be an object.")

        channel_id = clean_string(channel.get("id")).lower()
        if not SLUG_RE.match(channel_id):
            raise ValueError(f"Invalid channel id: {channel_id!r}. Use lowercase letters, numbers, and hyphens.")
        if channel_id in seen_channel_ids:
            raise ValueError(f"Duplicate channel id: {channel_id}.")
        seen_channel_ids.add(channel_id)

        channel_name = clean_string(channel.get("name"))
        if not channel_name:
            raise ValueError(f"Channel {channel_id} needs a name.")
        channel_avatar = clean_string(channel.get("avatar"))
        youtube_channel_id = clean_string(channel.get("youtubeChannelId"))
        if youtube_channel_id and not YOUTUBE_CHANNEL_ID_RE.match(youtube_channel_id):
            raise ValueError(f"Invalid YouTube channel id in {channel_id}: {youtube_channel_id!r}.")
        include_shorts = bool(channel.get("includeShorts", False))
        channel_recent_limit = clean_int(channel.get("recentVideosLimit"), recent_videos_per_channel, 0, 50)

        blocked_video_ids = channel.get("blockedVideoIds", [])
        if not isinstance(blocked_video_ids, list):
            raise ValueError(f"Channel {channel_id} blockedVideoIds must be an array.")
        blocked = {clean_string(video_id) for video_id in blocked_video_ids if clean_string(video_id)}
        invalid_blocked = [video_id for video_id in blocked if not VIDEO_ID_RE.match(video_id)]
        if invalid_blocked:
            raise ValueError(f"Channel {channel_id} has invalid blocked video ids: {', '.join(invalid_blocked)}.")

        channel_videos: list[dict[str, Any]] = []
        configured_videos = channel.get("videos", [])
        if configured_videos is None:
            configured_videos = []
        if not isinstance(configured_videos, list):
            raise ValueError(f"Channel {channel_id} must contain a videos array.")

        source_videos: list[dict[str, Any]] = []
        if youtube_channel_id:
            source_videos.extend(fetch_channel_feed(youtube_channel_id)[:channel_recent_limit])
        source_videos.extend(configured_videos)

        for video in source_videos:
            if not isinstance(video, dict):
                raise ValueError(f"Channel {channel_id} contains a video that is not an object.")

            youtube_id = clean_string(video.get("youtubeId"))
            if not VIDEO_ID_RE.match(youtube_id):
                raise ValueError(f"Invalid YouTube video id in {channel_id}: {youtube_id!r}.")
            is_short = bool(video.get("isShort", False))
            if is_short and not include_shorts:
                continue
            if youtube_id in blocked:
                continue
            if youtube_id in seen_video_ids:
                continue
            seen_video_ids.add(youtube_id)

            title = clean_string(video.get("title"))
            if not title:
                raise ValueError(f"Video {youtube_id} needs a title.")

            tags = video.get("tags", [])
            if not isinstance(tags, list):
                raise ValueError(f"Video {youtube_id} tags must be an array.")

            normalized_video = {
                "youtubeId": youtube_id,
                "title": title,
                "description": clean_string(video.get("description")),
                "duration": clean_string(video.get("duration")),
                "tags": [clean_string(tag).lower() for tag in tags if clean_string(tag)],
                "channelId": channel_id,
                "channelName": channel_name,
                "channelAvatar": channel_avatar,
                "thumbnail": clean_string(video.get("thumbnail")) or thumbnail_url(youtube_id),
                "published": clean_string(video.get("published")),
                "isShort": is_short,
                "source": "channel-feed" if youtube_channel_id else "manual",
            }
            channel_videos.append(normalized_video)
            videos.append(normalized_video)

        channels.append(
            {
                "id": channel_id,
                "name": channel_name,
                "description": clean_string(channel.get("description")),
                "avatar": channel_avatar,
                "youtubeChannelId": youtube_channel_id,
                "includeShorts": include_shorts,
                "recentVideosLimit": channel_recent_limit,
                "videos": channel_videos,
                "blockedVideoIds": sorted(blocked),
                "blockedCount": len(blocked),
                "videoCount": len(channel_videos),
            }
        )

    return {
        "appName": clean_string(raw.get("appName"), "Kidstream"),
        "profileName": clean_string(raw.get("profileName"), "Kids"),
        "settings": {
            "defaultPlaybackRate": default_playback_rate,
            "recentVideosPerChannel": recent_videos_per_channel,
            "youtubeArchiveSearchEnabled": bool(youtube_api_key(raw)),
        },
        "channels": channels,
        "videos": videos,
        "videoCount": len(videos),
        "configPath": str(config_path()),
    }


def load_catalog() -> dict[str, Any]:
    return normalize_catalog(load_raw_config())


def block_video(channel_id: str, youtube_id: str) -> None:
    if not SLUG_RE.match(channel_id):
        raise ValueError("Invalid channel id.")
    if not VIDEO_ID_RE.match(youtube_id):
        raise ValueError("Invalid video id.")

    raw = load_raw_config()
    for channel in raw.get("channels", []):
        if clean_string(channel.get("id")).lower() != channel_id:
            continue
        blocked = channel.setdefault("blockedVideoIds", [])
        if not isinstance(blocked, list):
            raise ValueError(f"Channel {channel_id} blockedVideoIds must be an array.")
        if youtube_id not in blocked:
            blocked.append(youtube_id)
            blocked.sort()
            write_raw_config(raw)
            FEED_CACHE.clear()
        return

    raise ValueError("Channel not found.")


def unblock_video(channel_id: str, youtube_id: str) -> None:
    if not SLUG_RE.match(channel_id):
        raise ValueError("Invalid channel id.")
    if not VIDEO_ID_RE.match(youtube_id):
        raise ValueError("Invalid video id.")

    raw = load_raw_config()
    for channel in raw.get("channels", []):
        if clean_string(channel.get("id")).lower() != channel_id:
            continue
        blocked = channel.setdefault("blockedVideoIds", [])
        if not isinstance(blocked, list):
            raise ValueError(f"Channel {channel_id} blockedVideoIds must be an array.")
        if youtube_id in blocked:
            blocked.remove(youtube_id)
            write_raw_config(raw)
            FEED_CACHE.clear()
        return

    raise ValueError("Channel not found.")


def local_search_videos(catalog: dict[str, Any], query: str) -> list[dict[str, Any]]:
    normalized = query.lower()
    if not normalized:
        return catalog["videos"]
    return [
        video
        for video in catalog["videos"]
        if normalized in " ".join([video["title"], video["description"], video["channelName"]]).lower()
    ]


def channel_by_youtube_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        channel["youtubeChannelId"]: channel
        for channel in catalog["channels"]
        if channel.get("youtubeChannelId")
    }


def thumbnail_from_snippet(snippet: dict[str, Any], youtube_id: str) -> str:
    thumbnails = snippet.get("thumbnails", {})
    for key in ["maxres", "standard", "high", "medium", "default"]:
        url = clean_string(thumbnails.get(key, {}).get("url"))
        if url:
            return url
    return thumbnail_url(youtube_id)


def normalize_api_search_video(item: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any] | None:
    youtube_id = clean_string(item.get("id"))
    if not VIDEO_ID_RE.match(youtube_id):
        return None

    snippet = item.get("snippet", {})
    title = clean_string(snippet.get("title"))
    if not title:
        return None

    description = clean_string(snippet.get("description"))
    blocked = set(channel.get("blockedVideoIds", []))
    if youtube_id in blocked:
        return None

    if not channel.get("includeShorts") and "#shorts" in f"{title} {description}".lower():
        return None

    return {
        "youtubeId": youtube_id,
        "title": title,
        "description": description,
        "duration": "",
        "tags": [],
        "channelId": channel["id"],
        "channelName": channel["name"],
        "channelAvatar": channel["avatar"],
        "thumbnail": thumbnail_from_snippet(snippet, youtube_id),
        "published": clean_string(snippet.get("publishedAt")),
        "isShort": False,
        "source": "youtube-api-search",
    }


def youtube_archive_search(raw: dict[str, Any], catalog: dict[str, Any], query: str) -> list[dict[str, Any]]:
    api_key = youtube_api_key(raw)
    if not api_key or not query:
        return []

    channels = channel_by_youtube_id(catalog)
    if not channels:
        return []

    ids_in_order: list[str] = []
    channel_for_video: dict[str, dict[str, Any]] = {}
    for youtube_channel_id, channel in channels.items():
        params = {
            "part": "snippet",
            "type": "video",
            "channelId": youtube_channel_id,
            "q": query,
            "maxResults": "25",
            "videoEmbeddable": "true",
            "safeSearch": "strict",
            "key": api_key,
        }
        try:
            response = fetch_json_url("https://www.googleapis.com/youtube/v3/search", params)
        except (urllib.error.URLError, json.JSONDecodeError):
            continue
        for item in response.get("items", []):
            youtube_id = clean_string(item.get("id", {}).get("videoId"))
            if VIDEO_ID_RE.match(youtube_id) and youtube_id not in channel_for_video:
                ids_in_order.append(youtube_id)
                channel_for_video[youtube_id] = channel

    if not ids_in_order:
        return []

    videos: list[dict[str, Any]] = []
    for start in range(0, len(ids_in_order), 50):
        chunk = ids_in_order[start : start + 50]
        params = {
            "part": "snippet,status",
            "id": ",".join(chunk),
            "key": api_key,
        }
        try:
            response = fetch_json_url("https://www.googleapis.com/youtube/v3/videos", params)
        except (urllib.error.URLError, json.JSONDecodeError):
            continue
        for item in response.get("items", []):
            youtube_id = clean_string(item.get("id"))
            channel = channel_for_video.get(youtube_id)
            if not channel:
                continue
            if item.get("status", {}).get("embeddable") is False:
                continue
            video = normalize_api_search_video(item, channel)
            if video:
                SEARCH_VIDEO_CACHE[youtube_id] = video
                videos.append(video)

    return videos


def merge_search_results(local_videos: list[dict[str, Any]], api_videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [local_videos, api_videos]:
        for video in source:
            youtube_id = video["youtubeId"]
            if youtube_id in seen:
                continue
            seen.add(youtube_id)
            merged.append(video)
    return merged


def match_video(catalog: dict[str, Any], youtube_id: str) -> dict[str, Any] | None:
    for video in catalog["videos"]:
        if video["youtubeId"] == youtube_id:
            return video
    cached = SEARCH_VIDEO_CACHE.get(youtube_id)
    if cached:
        return cached
    return None


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = "Kidstream/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/catalog":
            self.handle_catalog()
            return
        if path == "/api/search":
            self.handle_search(parsed.query)
            return
        if path.startswith("/api/videos/"):
            youtube_id = path.removeprefix("/api/videos/")
            self.handle_video(youtube_id)
            return
        if path == "/healthz":
            self.send_json({"ok": True})
            return

        self.serve_static(path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/healthz":
            self.send_json({"ok": True}, head_only=True)
            return
        if path in {"/api/catalog", "/api/search"} or path.startswith("/api/videos/"):
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_common_headers()
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.serve_static(path, head_only=True)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/block-video":
            self.handle_block_video()
            return
        if path == "/api/unblock-video":
            self.handle_unblock_video()
            return

        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def handle_catalog(self) -> None:
        try:
            catalog = load_catalog()
        except ValueError as error:
            status, body = json_error(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_json(body, status)
            return

        public_catalog = {key: value for key, value in catalog.items() if key != "configPath"}
        self.send_json(public_catalog)

    def handle_search(self, raw_query: str) -> None:
        query = clean_string(parse_qs(raw_query).get("q", [""])[0])
        try:
            raw = load_raw_config()
            catalog = normalize_catalog(raw)
        except ValueError as error:
            status, body = json_error(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_json(body, status)
            return

        local_results = local_search_videos(catalog, query)
        api_results = youtube_archive_search(raw, catalog, query)
        self.send_json(
            {
                "query": query,
                "apiEnabled": bool(youtube_api_key(raw)),
                "videos": merge_search_results(local_results, api_results),
            }
        )

    def handle_video(self, youtube_id: str) -> None:
        if not VIDEO_ID_RE.match(youtube_id):
            status, body = json_error("Invalid video id.", HTTPStatus.BAD_REQUEST)
            self.send_json(body, status)
            return

        try:
            catalog = load_catalog()
        except ValueError as error:
            status, body = json_error(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_json(body, status)
            return

        video = match_video(catalog, youtube_id)
        if not video:
            status, body = json_error("That video is not on the allowlist.", HTTPStatus.NOT_FOUND)
            self.send_json(body, status)
            return

        related: list[dict[str, Any]] = []

        def add_related(candidates: list[dict[str, Any]]) -> None:
            seen = {item["youtubeId"] for item in related}
            for item in candidates:
                if item["youtubeId"] == youtube_id or item["youtubeId"] in seen:
                    continue
                related.append(item)
                seen.add(item["youtubeId"])
                if len(related) == 8:
                    return

        add_related([item for item in catalog["videos"] if item["channelId"] == video["channelId"]])
        if len(related) < 8:
            add_related(catalog["videos"])

        self.send_json({"video": video, "related": related[:8]})

    def handle_block_video(self) -> None:
        self.handle_block_change(block_video, "blockedVideoId")

    def handle_unblock_video(self) -> None:
        self.handle_block_change(unblock_video, "unblockedVideoId")

    def handle_block_change(self, update: Any, response_key: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            status, body = json_error("Invalid request body.", HTTPStatus.BAD_REQUEST)
            self.send_json(body, status)
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            status, body = json_error("Request body must be JSON.", HTTPStatus.BAD_REQUEST)
            self.send_json(body, status)
            return

        channel_id = clean_string(payload.get("channelId")).lower()
        youtube_id = clean_string(payload.get("youtubeId"))
        try:
            update(channel_id, youtube_id)
            self.send_json({"ok": True, response_key: youtube_id})
        except ValueError as error:
            status, body = json_error(str(error), HTTPStatus.BAD_REQUEST)
            self.send_json(body, status)

    def serve_static(self, path: str, head_only: bool = False) -> None:
        if path == "/":
            target = STATIC_ROOT / "index.html"
        elif path == "/favicon.ico":
            target = STATIC_ROOT / "favicon.png"
        else:
            relative = path.lstrip("/")
            target = (STATIC_ROOT / relative).resolve()
            if not str(target).startswith(str(STATIC_ROOT.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN)
                return

        if not target.exists() or not target.is_file():
            target = STATIC_ROOT / "index.html"

        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_common_headers()
        self.send_header("Content-Type", content_type_for(target))
        if target.name in {"favicon.png", "index.html"}:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def send_json(self, body: dict[str, Any], status: int = 200, head_only: bool = False) -> None:
        data = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def send_common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https://*.ytimg.com https://yt3.ggpht.com https://yt3.googleusercontent.com data:; "
            "frame-src https://www.youtube-nocookie.com https://www.youtube.com; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )

    def log_message(self, format: str, *args: Any) -> None:
        if env_value("KIDSTREAM_QUIET") == "1":
            return
        super().log_message(format, *args)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), Handler)
    scheme = "http"
    tls_cert = env_value("KIDSTREAM_TLS_CERT")
    tls_key = env_value("KIDSTREAM_TLS_KEY")
    if tls_cert or tls_key:
        if not tls_cert or not tls_key:
            raise RuntimeError("Set both KIDSTREAM_TLS_CERT and KIDSTREAM_TLS_KEY to enable HTTPS.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    print(f"Kidstream running at {scheme}://{host}:{port}")
    print(f"Using config: {config_path()}")
    server.serve_forever()


if __name__ == "__main__":
    main()
