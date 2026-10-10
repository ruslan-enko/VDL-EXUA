"""Mac OS System 1 (1984) theme helpers for vdl TUI."""


def mac_title_bar(title: str, width: int = 58) -> str:
    """Generate Mac OS 1 striped title bar text.

    Produces a string like: ▒▒▒▒▒▒▒▒▒ Title ▒▒▒▒▒▒▒▒▒
    The ▒ characters simulate the horizontal line pattern
    of the original Macintosh window title bars (1984).
    """
    inner = f" {title} "
    remaining = max(0, width - len(inner))
    left = remaining // 2
    right = remaining - left
    return f"{'▒' * left}{inner}{'▒' * right}"
