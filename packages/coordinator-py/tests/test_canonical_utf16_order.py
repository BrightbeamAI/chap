"""
Regression: JCS orders object keys by UTF-16 code unit, not Unicode code point.
An astral key (encoded as a surrogate pair) must sort before a BMP key such as
U+FFFF, so the Python canonicaliser agrees byte-for-byte with the TypeScript
reference and the two coordinators produce the same hash.
"""
from __future__ import annotations

from chap_coordinator.canonical import canonicalize


def test_object_keys_sort_by_utf16_code_unit():
    obj = {"\U0001F600": 1, "￿": 2}
    assert canonicalize(obj) == '{"\U0001F600":1,"￿":2}'.encode("utf-8")


def test_bmp_key_order_is_unchanged():
    obj = {"b": 1, "a": 2, "Z": 3}
    assert canonicalize(obj) == b'{"Z":3,"a":2,"b":1}'
