"""
slide_templates.py — Visual themes for AI-generated slide presentations
======================================================================
Defines color palettes, typography, and decorative elements for each theme.
Themes are adaptive and work with any resolution or orientation (landscape/portrait).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SlideTheme:
    """Complete visual theme for a slide presentation."""
    name: str
    background: str
    text_primary: str
    text_secondary: str
    accent_primary: str
    accent_secondary: str
    decorative_opacity: float

    def to_dict(self) -> dict:
        return {
            "background": self.background,
            "text_primary": self.text_primary,
            "text_secondary": self.text_secondary,
            "accent_primary": self.accent_primary,
            "accent_secondary": self.accent_secondary,
            "decorative_opacity": self.decorative_opacity,
        }


THEMES: dict[str, SlideTheme] = {
    "tokyo_night": SlideTheme(
        name="Tokyo Night",
        background="#1a1b26",
        text_primary="#c0caf5",
        text_secondary="#9aa5ce",
        accent_primary="#7aa2f7",
        accent_secondary="#bb9af7",
        decorative_opacity=0.15,
    ),
    "executive": SlideTheme(
        name="Executive",
        background="#1e2761",
        text_primary="#ffffff",
        text_secondary="#cadcfc",
        accent_primary="#4a6cf7",
        accent_secondary="#f7c948",
        decorative_opacity=0.12,
    ),
    "minimal": SlideTheme(
        name="Minimal",
        background="#0f0f0f",
        text_primary="#f8fafc",
        text_secondary="#94a3b8",
        accent_primary="#38bdf8",
        accent_secondary="#f472b6",
        decorative_opacity=0.10,
    ),
}


def get_theme(name: str) -> SlideTheme:
    """Returns a theme by name, defaulting to 'tokyo_night'."""
    return THEMES.get(name, THEMES["tokyo_night"])


def get_all_theme_names() -> list[str]:
    """Returns a list of all available theme display names."""
    return [t.name for t in THEMES.values()]


def get_theme_by_display_name(display_name: str) -> SlideTheme:
    """Returns a theme by its display name (e.g., 'Tokyo Night')."""
    for theme in THEMES.values():
        if theme.name == display_name:
            return theme
    return THEMES["tokyo_night"]
