"""
icon_manager.py — Icon library manager for slide generation
=========================================================
Loads, indexes, and provides filtering for the SVG icon library.
"""

import os
import random
from pathlib import Path
from typing import Optional
import re


SCRIPT_DIR = Path(__file__).parent
SVG_DIR = SCRIPT_DIR / "SVGs"

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can", "need",
    "it", "its", "this", "that", "these", "those", "i", "you", "he",
    "she", "we", "they", "what", "which", "who", "when", "where", "why",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "also", "now", "here", "there",
    "then", "once", "using", "used", "use", "make", "made", "new", "one",
    "two", "three", "four", "five", "first", "second", "third", "last",
    "many", "much", "well", "way", "even", "like", "get", "look", "see",
    "come", "go", "know", "take", "think", "give", "find", "tell", "try",
    "leave", "call", "keep", "let", "begin", "seem", "help", "show",
    "hear", "play", "run", "move", "live", "believe", "hold", "bring",
    "happen", "write", "provide", "sit", "stand", "lose", "pay", "meet",
    "include", "continue", "set", "learn", "change", "lead", "understand",
    "watch", "follow", "stop", "create", "speak", "read", "allow", "add",
    "spend", "grow", "open", "walk", "win", "offer", "remember", "love",
    "consider", "appear", "buy", "wait", "serve", "die", "send", "expect",
    "build", "stay", "fall", "cut", "reach", "kill", "remain", "suggest",
    "raise", "pass", "sell", "require", "report", "decide", "pull",
}


class IconLibrary:
    """Manages the icon library with keyword filtering."""

    def __init__(self, svg_dir: Path = SVG_DIR):
        self.svg_dir = svg_dir
        self.icons: list[dict] = []
        self._load_icons()

    def _load_icons(self):
        """Load all icons from the SVG directory."""
        styles = ["filled", "outline"]
        
        for style in styles:
            style_dir = self.svg_dir / style
            if not style_dir.exists():
                continue

            for f in style_dir.iterdir():
                if f.suffix == ".svg":
                    name = f.stem
                    display_name = name.replace("-", " ").replace("_", " ")
                    
                    words = set(display_name.split())
                    for compound in name.replace("-", " ").replace("_", " ").split():
                        for i in range(len(compound)):
                            for j in range(i + 2, len(compound) + 1):
                                words.add(compound[i:j])

                    self.icons.append({
                        "name": name,
                        "path": str(f),
                        "style": style,
                        "display": display_name,
                        "keywords": words,
                    })

        print(f"[Icon] Loaded {len(self.icons)} icons from {self.svg_dir}")

    def extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords from text."""
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        return {w for w in words if w not in STOP_WORDS}

    def filter_icons(self, keywords: set[str], max_icons: int = 50) -> list[str]:
        """Filter icons by keywords and return icon paths."""
        if not keywords:
            return self._random_icons(max_icons)

        scores: dict[str, int] = {}
        for icon in self.icons:
            score = sum(1 for kw in keywords if kw in icon["keywords"])
            if score > 0:
                scores[icon["path"]] = score

        if not scores:
            return self._random_icons(max_icons)

        sorted_icons = sorted(scores.keys(), key=lambda p: scores[p], reverse=True)
        return sorted_icons[:max_icons]

    def _random_icons(self, count: int) -> list[str]:
        """Return random icons as fallback."""
        if len(self.icons) <= count:
            return [i["path"] for i in self.icons]
        return random.sample([i["path"] for i in self.icons], count)

    def format_icon_list(self, icon_paths: list[str]) -> str:
        """Format icon paths for LLM prompt."""
        names = []
        for path in icon_paths:
            for icon in self.icons:
                if icon["path"] == path:
                    names.append(f"{icon['name']}.svg")
                    break
        return ", ".join(names)


_icon_library: Optional[IconLibrary] = None


def get_icon_library() -> IconLibrary:
    """Get or create the global icon library instance."""
    global _icon_library
    if _icon_library is None:
        _icon_library = IconLibrary()
    return _icon_library


def recolor_svg(svg_content: str, color: str) -> str:
    """Apply a color to an SVG, replacing currentColor with the given color."""
    svg = svg_content.replace("currentColor", color)
    svg = svg.replace('fill="none"', f'fill="{color}"')
    
    svg = re.sub(
        r'fill="#[0-9a-fA-F]{6}"',
        f'fill="{color}"',
        svg
    )
    
    return svg


def load_and_recolor_icon(icon_name: str, theme_color: str) -> Optional[str]:
    """Load an icon SVG file by name and apply theme color."""
    try:
        # icon_name is just the filename, e.g., "code.svg"
        # We need to find it in either filled/ or outline/
        for style in ["filled", "outline"]:
            full_path = SVG_DIR / style / icon_name
            if full_path.exists():
                with open(full_path, "r", encoding="utf-8") as f:
                    svg = f.read()
                return recolor_svg(svg, theme_color)

        # If not found with exact name, search in the icon list
        lib = get_icon_library()
        for icon in lib.icons:
            if icon["name"] + ".svg" == icon_name:
                with open(icon["path"], "r", encoding="utf-8") as f:
                    svg = f.read()
                return recolor_svg(svg, theme_color)

        return None
    except Exception:
        return None
