# Kidstream

A local, kid-facing video portal that only shows videos from approved YouTube
channels and manually listed videos in a private allowlist file. It uses the
official YouTube embed for playback, so normal YouTube controls, fullscreen,
captions, and speed controls stay available.

## AI Agent Setup Prompt

Copy this prompt into your AI coding agent to have it install and configure this
local approved-video app for you:

```text
Set up this local approved-video app for me from GitHub:
https://github.com/madebydia/kidstream.git

Clone the repo, create a private local config at config/videos.local.json, keep
that file untracked, help me add approved YouTube channels or videos, validate
the config, and run the app locally so I can open it in a browser. If I provide a
YouTube Data API key, add it only to the private local config or a local
environment variable, never to committed example files.
```

## Run Locally

```bash
python3 server.py
```

Open:

```text
http://127.0.0.1:8787
```

The server uses `config/videos.local.json` when it exists. If it does not exist,
it falls back to `config/videos.example.json`.

## Configure Videos

Edit this private file:

```text
config/videos.local.json
```

It is listed in `.gitignore` so the real household allowlist does not get
committed. Channels can include a YouTube channel ID, manual videos, or both:

```json
{
  "appName": "Kidstream",
  "defaultPlaybackRate": 0.75,
  "recentVideosPerChannel": 15,
  "channels": [
    {
      "id": "math",
      "name": "Math",
      "description": "Approved math videos",
      "youtubeChannelId": "UCxxxxxxxxxxxxxxxxxxxxxx",
      "includeShorts": false,
      "blockedVideoIds": [],
      "videos": [
        {
          "youtubeId": "M7lc1UVf-VE",
          "title": "Example Video",
          "description": "Optional notes for parents.",
          "duration": "2:02",
          "tags": ["math", "counting"]
        }
      ]
    }
  ]
}
```

When `youtubeChannelId` is set, the app reads that channel's public uploads feed
and adds recent videos automatically. The `videos` array can still hold
hand-picked seed or extra videos. Clicking **Hide** in the app appends that
video's ID to `blockedVideoIds` for the channel and removes it from the catalog.
Shorts are excluded by default because YouTube includes them in channel upload
feeds; set `includeShorts` to `true` for a channel if you want them shown.

`defaultPlaybackRate` controls the starting YouTube player speed. Use values
between `0.25` and `2.0`.

`recentVideosPerChannel` controls how many recent feed videos to show per
approved channel. YouTube's public RSS feed only exposes a recent slice of each
channel, so increasing this cannot search the full archive; configure a YouTube
Data API key for older-video search. A channel can override the global number
with `recentVideosLimit`.

Search works against the videos currently loaded from approved channels. To let
search look deeper into older videos from those same approved channels, configure
a YouTube Data API key locally:

```bash
export YOUTUBE_API_KEY="..."
python3 server.py
```

You can also put `"youtubeApiKey": "..."` in `config/videos.local.json`, which is
ignored by git. Do not put API keys in `config/videos.example.json`.

See [Get a YouTube Data API Key](docs/youtube-api-key.md) for a step-by-step
guide with screenshots.

Validate the current config:

```bash
python3 scripts/validate_config.py
```

## Docker

```bash
docker compose up --build
```

The compose file mounts `config/videos.local.json` into the container as a
read-only local file.

## Deployment

Two good options are ready:

- Docker Compose: copy this folder to the target machine and run
  `docker compose up -d`.
- systemd: copy the folder to `/opt/kidstream`, install
  `deploy/systemd/kidstream.service`, and enable it.

For kiosk mode on a machine with Chromium:

```bash
chromium --kiosk http://127.0.0.1:8787
```

If another device on the local network should open the app, run the server with
`HOST=0.0.0.0` and use that machine's LAN address.

## Important Limits

The app only lists and routes to videos from approved channels or manual entries,
minus locally blocked videos. The embedded YouTube player is still YouTube's
player, so YouTube may show some player-level UI such as related video
affordances. App navigation, search, and channel pages remain local and
allowlist-only.
