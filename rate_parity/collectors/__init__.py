"""Pick the collector for a site. Everything is config-driven for now."""
from ..config import Site
from .base import Collector, RoomMismatch
from .generic import GenericCollector


def make_collector(site: Site) -> Collector:
    return GenericCollector(site)


__all__ = ["Collector", "GenericCollector", "RoomMismatch", "make_collector"]
