"""Pick the collector for a site. Everything is config-driven for now."""
from ..config import Site
from .base import Collector, RoomMismatch
from .ezee import EzeeCollector
from .generic import GenericCollector


def make_collector(site: Site, evidence_dir=None) -> Collector:
    if site.kind == "ezee":
        return EzeeCollector(site, evidence_dir=evidence_dir) if evidence_dir else EzeeCollector(site)
    return GenericCollector(site)


__all__ = ["Collector", "EzeeCollector", "GenericCollector", "RoomMismatch", "make_collector"]
