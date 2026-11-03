"""Decode the unfiltered PNGs the grader writes, to check what the judge would see."""

from __future__ import annotations

import base64
import struct
import zlib
from typing import Any

import numpy as np


def decode_png(data: bytes) -> np.ndarray[Any, Any]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    width = height = color = 0
    tags = []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF
        tags.append(tag)
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert depth == 8
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    assert tags[0] == b"IHDR" and tags[-1] == b"IEND"
    channels = {0: 1, 2: 3, 6: 4}[color]
    rows = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(
        height, 1 + width * channels
    )
    assert (rows[:, 0] == 0).all()
    return rows[:, 1:].reshape(height, width, channels)


def decode_data_url(url: str) -> np.ndarray[Any, Any]:
    prefix = "data:image/png;base64,"
    assert url.startswith(prefix)
    return decode_png(base64.b64decode(url[len(prefix) :]))
