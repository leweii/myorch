"""Images people attach to a message (a reply, or the request that starts a run).

They are saved in the run's workspace under docs/attachments/ (not sent through the workflow: the bytes
would exceed what a signal or an activity input may carry). The message names them after ATTACH_MARK,
so the agent can open them with its Read tool and the console can show them again (ui/static/app.js
splitAttachments), whichever channel the message came from.
"""

from __future__ import annotations

import re
import secrets

ATTACH_DIR = "docs/attachments"
ATTACH_MARK = "[myorch: attached images]"
MAX_ATTACH = 6
MAX_ATTACH_BYTES = 5_000_000  # what the model takes per image
ATTACH_PATH = re.compile(r"docs/attachments/[0-9a-f]{16}\.(?:png|jpg|gif|webp)")


def sniff_image(data: bytes) -> str | None:
    """The extension of what the bytes are, by their signature (the name or type a browser sends is not trusted)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def check_images(images: list[bytes]) -> list[str]:
    """The extension of each image, or ValueError for what may not be attached."""
    if len(images) > MAX_ATTACH:
        raise ValueError(f"Attach at most {MAX_ATTACH} images.")
    exts = []
    for data in images:
        if not data:
            raise ValueError("An image is empty.")
        if len(data) > MAX_ATTACH_BYTES:
            raise ValueError(f"An image is larger than {MAX_ATTACH_BYTES // 1_000_000} MB.")
        ext = sniff_image(data)
        if ext is None:
            raise ValueError("Only PNG, JPEG, GIF and WebP images can be attached.")
        exts.append(ext)
    return exts


def new_path(ext: str) -> str:
    return f"{ATTACH_DIR}/{secrets.token_hex(8)}.{ext}"


def attach_block(paths: list[str]) -> str:
    return (f"\n\n{ATTACH_MARK}\nThe user attached these images; open each with the Read tool to see it:\n"
            + "\n".join(f"- /sandbox/workspace/{p}" for p in paths))
