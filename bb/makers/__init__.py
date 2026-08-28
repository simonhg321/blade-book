# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Maker modules: prompt fragments, extension schema and consistency rules per
maker. `get('crk')` is the only entry today; adding a maker = one module."""
from bb.makers import crk

_REGISTRY = {'crk': crk}


def get(maker):
    return _REGISTRY[maker]  # KeyError on purpose — routes validate first
