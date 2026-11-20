"""The brand is drawn from two files; everything else is written from them and stays current."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

MAKE = Path(__file__).resolve().parent.parent / "brand" / "make.py"

# The source archive leaves brand/ and tutorials/ out, so from an sdist there is nothing to check.
pytestmark = pytest.mark.skipif(not MAKE.exists(), reason="brand/ is the repo's, not the sdist's")


def _make() -> ModuleType:
    spec = importlib.util.spec_from_file_location("brand_make", MAKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["brand_make"] = module
    spec.loader.exec_module(module)
    return module


def test_the_palette_keeps_its_contrast_rules() -> None:
    """Every ink reads on every ground, in both themes."""
    assert _make().contrast_failures() == []


def test_everything_drawn_from_the_brand_is_current() -> None:
    """Logo files, favicon, palette table and sketch colors match the theme.
    `python brand/make.py --write` brings them up to date."""
    assert _make().stale() == []


def test_a_sketch_keeps_its_theme_block_once() -> None:
    """Writing a sketch twice changes nothing: the block is replaced, never stacked."""
    make = _make()
    for path in sorted(make.SKETCHES.glob("*.svg")):
        once = make.sketch(path.read_text())
        assert make.sketch(once) == once
        assert once.count("theme:start") == 1
