"""Shared fixtures for HLS downloader integration tests."""

import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _make_hls(tmp_path: Path, duration: int = 20) -> Path:
    """Renders a test video into an HLS media dir. Returns the directory."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            # Short GOP: the HLS muxer can only cut on keyframes, and the default
            # 250-frame GOP would yield a handful of huge segments.
            "-g", "20", "-force_key_frames", "expr:gte(t,n_forced*2)",
            "-c:a", "aac", "-b:a", "64k",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
            "-hls_segment_filename", str(media_dir / "seg%03d.ts"),
            str(media_dir / "index.m3u8"),
        ],
        capture_output=True,
        check=True,
    )
    return media_dir


class _Server:
    """HTTP server for a media dir that can stall or hang mid-download.

    `stall_from` (1-based request ordinal) plus `stall_count` model a transient
    network problem: that many requests block for `stall_seconds` and then serve
    normally. With `stall_seconds=None` they stay silent until `stop()`.
    """

    def __init__(
        self,
        root: Path,
        stall_from: int | None = None,
        stall_count: int = 1,
        stall_seconds: float | None = None,
    ):
        self.root = root
        self.stall_from = stall_from
        self.stall_count = stall_count
        self.stall_seconds = stall_seconds
        self.requests: list[str] = []
        self.stalled = 0
        self._lock = threading.Lock()
        # Released only by stop(), so a blocked handler stays blocked.
        self._release = threading.Event()
        server_self = self

        class Handler(SimpleHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

            def log_message(self, *args):  # silence request logging
                pass

            def handle_one_request(self):
                # ffmpeg gives up and closes the socket while we still hold the
                # handler; that BrokenPipe noise is expected, not a failure.
                try:
                    super().handle_one_request()
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True

            def do_GET(self):
                with server_self._lock:
                    server_self.requests.append(self.path)
                    ordinal = len(server_self.requests)
                    should_stall = (
                        server_self.stall_from is not None
                        and server_self.stall_from
                        <= ordinal
                        < server_self.stall_from + server_self.stall_count
                    )
                    if should_stall:
                        server_self.stalled += 1

                if should_stall:
                    if server_self.stall_seconds is None:
                        server_self._release.wait(timeout=180)
                    else:
                        server_self._release.wait(
                            timeout=server_self.stall_seconds
                        )
                super().do_GET()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "_Server":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._release.set()
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def hls_media(tmp_path: Path) -> Path:
    """A local HLS media directory with a playable playlist."""
    return _make_hls(tmp_path)


@pytest.fixture
def hls_server(tmp_path: Path):
    """Factory fixture yielding started _Server instances, stopped on teardown."""
    servers: list[_Server] = []

    def factory(
        duration: int = 20,
        stall_from: int | None = None,
        stall_count: int = 1,
        stall_seconds: float | None = None,
    ) -> _Server:
        media = _make_hls(tmp_path / f"media{len(servers)}", duration=duration)
        server = _Server(
            media,
            stall_from=stall_from,
            stall_count=stall_count,
            stall_seconds=stall_seconds,
        ).start()
        servers.append(server)
        return server

    yield factory

    for server in servers:
        server.stop()