from __future__ import annotations

import struct

import pytest

CRC_TABLE = (
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
)


def _fit_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = (crc >> 4) ^ CRC_TABLE[crc & 0xF] ^ CRC_TABLE[byte & 0xF]
        crc = (crc >> 4) ^ CRC_TABLE[crc & 0xF] ^ CRC_TABLE[byte >> 4]
    return crc


def make_fit(points: list[tuple[float, float, float]]) -> bytes:
    """Build a minimal CRC-valid FIT activity containing record messages."""
    definition = bytes(
        (
            0x40,  # definition message, local message 0
            0,
            0,  # little endian
        )
    ) + struct.pack("<H", 20)  # global message 20 is a record
    definition += bytes(
        (
            3,  # field count
            0,
            4,
            0x85,  # position_lat: sint32
            1,
            4,
            0x85,  # position_long: sint32
            2,
            2,
            0x84,  # altitude: uint16, scale 5, offset 500
        )
    )
    records = b"".join(
        bytes((0,))
        + struct.pack(
            "<iiH",
            round(latitude * 2**31 / 180),
            round(longitude * 2**31 / 180),
            round((elevation + 500) * 5),
        )
        for latitude, longitude, elevation in points
    )
    data = definition + records
    header = bytes((12, 0x20)) + struct.pack("<H", 2100)
    header += struct.pack("<I", len(data)) + b".FIT"
    body = header + data
    return body + struct.pack("<H", _fit_crc(body))


@pytest.fixture
def fit_bytes() -> bytes:
    return make_fit(
        [
            (47.0, 10.0, 500.0),
            (47.01, 10.0, 550.0),
            (47.02, 10.0, 525.0),
        ]
    )
