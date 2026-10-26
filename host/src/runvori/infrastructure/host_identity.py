"""Opaque installation identity shared by Host storage and pairing records."""

import secrets


def new_host_id() -> str:
    return "host_" + secrets.token_hex(16)


def valid_host_id(value: object) -> bool:
    return (isinstance(value, str) and value.startswith("host_")
            and len(value) == 37
            and all(char in "0123456789abcdef" for char in value[5:]))
