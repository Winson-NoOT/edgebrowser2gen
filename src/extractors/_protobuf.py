"""Small, bounded protobuf wire reader for Edge's stored sync messages."""

from __future__ import annotations


def varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated protobuf integer")
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise ValueError("Protobuf integer exceeds 64 bits")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Invalid protobuf integer")


def fields(data: bytes) -> dict[int, list[int | bytes]]:
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Protobuf message is too large")
    result: dict[int, list[int | bytes]] = {}
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < 2**29:
            raise ValueError("Invalid protobuf field number")
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire in {1, 2, 5}:
            if wire == 2:
                length, offset = varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            if offset + length > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError("Unsupported protobuf wire type")
        result.setdefault(number, []).append(value)
    return result


def one(message: dict, number: int, default=None):
    return message.get(number, [default])[-1]


def text(message: dict, number: int, default: str | None = None) -> str:
    value = one(message, number)
    if value is None and default is not None:
        return default
    if not isinstance(value, bytes):
        raise ValueError("Missing or invalid protobuf string")
    return value.decode("utf-8")


def nested(message: dict, number: int) -> dict:
    value = one(message, number)
    if not isinstance(value, bytes):
        raise ValueError("Missing or invalid nested protobuf message")
    return fields(value)
