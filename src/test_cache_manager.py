"""Tests for the modular config cache manager."""

from meshtastic import mesh_pb2

from core.cache_manager import CacheManager
from core.stats import StatsCollector


def make_cache_manager():
    cache = CacheManager(
        enabled=True,
        max_nodes=500,
        stats=StatsCollector(),
    )
    cache.complete = True
    return cache


def make_want_config(config_id):
    to_radio = mesh_pb2.ToRadio()
    to_radio.want_config_id = config_id
    return to_radio.SerializeToString()


def test_legacy_config_request_can_be_served_from_cache():
    cache = make_cache_manager()

    assert cache.can_serve(make_want_config(123456789)) is True


def test_modern_stage_one_bypasses_cache():
    cache = make_cache_manager()

    assert cache.can_serve(make_want_config(69420)) is False


def test_modern_stage_two_bypasses_cache():
    cache = make_cache_manager()

    assert cache.can_serve(make_want_config(69421)) is False


def test_non_config_request_cannot_be_served_from_cache():
    cache = make_cache_manager()

    to_radio = mesh_pb2.ToRadio()

    assert cache.can_serve(to_radio.SerializeToString()) is False
