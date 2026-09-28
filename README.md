# vdl — Minimalist Terminal Video Downloader

`vdl` is a polished, interactive terminal application (TUI) for downloading videos from web pages via HLS (`.m3u8`) and `ffmpeg`.

Instead of memorizing long command flags, you simply launch `vdl`, paste a URL, choose your preferred quality, and let it download with a clean progress bar.

```text
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   vdl                                                       │
│                                                             │
│   Paste video page URL                                      │
│                                                             │
│   › https://uafix.net/serials/lihtari/season-01-episode-07/ │
│                                                             │
│   Enter continue                                  Esc quit  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Features

- **Interactive TUI:** Built with [Textual](https://textual.textualize.io/) for smooth keyboard navigation, clean borders, and terminal resize adaptation.
- **Automatic Stream Discovery:**
  - Detects embedded video players (`Playerjs`, `JWPlayer`, `video.js`, etc.).
  - Resolves nested `<iframe>` sources and respects `Referer` headers.
  - Automatically parses master `.m3u8` playlists and extracts stream resolutions (`1080p`, `720p`, `480p`, `360p`).
- **Lossless Download:** Uses `ffmpeg -c copy` to stream and remux directly into MP4 without re-encoding (maximum speed, 0% CPU loss).
- **Real-time Progress:** Displays percentage, downloaded/estimated size, download speed, and ETA countdown.
- **Smart Metadata:** Derives clean titles from OpenGraph / HTML metadata and episode patterns (e.g. `Lanterns — S01E07.mp4`).
- **Clipboard Integration:** Detects URLs in your macOS clipboard (`pbpaste`) for immediate one-keypress downloads.
- **Graceful Cancellation:** Pressing `Esc` cleans up partial `.part` files and sends `SIGINT` to `ffmpeg`.

---

## Installation & Setup

Requirements:
- Python >= 3.10
- `ffmpeg` (available via Homebrew: `brew install ffmpeg`)
- `uv`

Install locally:

```bash
cd ~/Documents/Projects/vdl
uv tool install --editable .
```

The `vdl` binary is installed into `~/.local/bin/vdl`.

---

## Usage

### 1. Interactive Mode (Default)

Launch the interactive interface:

```bash
vdl
```

1. Paste the video page URL and press **Enter**.
2. Wait a moment for stream discovery (`✓ Page found`, `✓ Video player found`, `✓ HLS stream found`).
3. Select desired quality using `↑` / `↓` and press **Enter**.
4. The file will be saved directly into `~/Downloads/`.

### 2. Direct Shortcut

Pass the URL directly to skip the paste prompt:

```bash
vdl "https://uafix.net/serials/lihtari/season-01-episode-07/"
```

---

## Keyboard Navigation

| Key | Action |
|---|---|
| `Enter` | Confirm / Continue / Download another |
| `↑` / `↓` (or `k` / `j`) | Select video quality |
| `Esc` | Back / Cancel download / Quit |
| `q` | Quit application |

---

## Project Structure

```text
vdl/
├── pyproject.toml
├── src/
│   └── vdl/
│       ├── cli.py             # CLI entry point (vdl / vdl <URL>)
│       ├── core/
│       │   ├── finder.py      # Multi-level HLS stream discovery
│       │   ├── downloader.py  # Async ffmpeg process manager
│       │   ├── metadata.py    # Title extraction and sanitization
│       │   └── models.py      # VideoInfo, QualityOption, DownloadProgress
│       └── ui/
│           ├── app.py         # Main Textual App & screen coordinator
│           ├── styles.tcss    # Polished minimalist styling
│           └── screens/       # Input, Finding, Quality, Download, Complete, Error
└── tests/                     # Comprehensive pytest test suite
```
