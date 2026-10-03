"""ids: hashes, canonical JSON, stable uniform sharding."""

import zlib

from etl.core.ids import board_shard, canonical_json, content_hash


def test_content_hash_is_sha256_hex():
    assert content_hash(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert len(content_hash(b"")) == 64


def test_canonical_json_sorted_no_whitespace():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert " " not in canonical_json({"a": 1})


def test_board_shard_matches_spec_formula():
    for board_id in ["a", "board-1", 123, ""]:
        for n in (1, 8, 16):
            assert board_shard(board_id, n) == zlib.crc32(str(board_id).encode()) % n


def test_board_shard_stable_and_uniform():
    n = 8
    counts = [0] * n
    total = 5000
    for i in range(total):
        shard = board_shard(f"board-{i}", n)
        assert 0 <= shard < n
        counts[shard] += 1
        # stable on repeat
        assert board_shard(f"board-{i}", n) == shard
    mean = total / n
    for c in counts:
        assert abs(c - mean) / mean < 0.10
