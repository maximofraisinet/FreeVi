"""
slide_renderer.py — Renders AI-generated slides as PNG images
============================================================
Uses Pillow for image composition and cairosvg for SVG elements.
Output resolution: dynamically set based on orientation (default 1920x1080).
"""

import io
import logging
import os
import sys
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("freevi.slides")

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

FONT_CACHE: dict[str, ImageFont.FreeTypeFont] = {}


def _get_system_font(family: str = "sans-serif", size: int = 40) -> ImageFont.FreeTypeFont:
    """Returns a cached PIL ImageFont, attempting to find the requested family."""
    cache_key = f"{family}_{size}"
    if cache_key in FONT_CACHE:
        return FONT_CACHE[cache_key]

    font_paths: dict[str, list[str]] = {
        "sans-serif": [
            "C:\\Windows\\Fonts\\arial.ttf",
            "C:\\Windows\\Fonts\\segoeui.ttf",
            "/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ],
        "serif": [
            "C:\\Windows\\Fonts\\times.ttf",
            "C:\\Windows\\Fonts\\georgia.ttf",
            "/Library/Fonts/Georgia.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        ],
        "monospace": [
            "C:\\Windows\\Fonts\\consola.ttf",
            "C:\\Windows\\Fonts\\cour.ttf",
            "/Library/Fonts/Courier New.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ],
    }

    candidates = font_paths.get(family, font_paths["sans-serif"])
    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                FONT_CACHE[cache_key] = font
                log.debug(f"Loaded font: {path} ({size}pt)")
                return font
            except Exception as e:
                log.debug(f"Could not load {path}: {e}")

    log.warning(f"No custom font found for '{family}', using default")
    font = ImageFont.load_default(size=size)
    FONT_CACHE[cache_key] = font
    return font


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Converts '#RRGGBB' to (R, G, B)."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Converts '#RRGGBB' to (R, G, B, A)."""
    r, g, b = _hex_to_rgb(hex_color)
    return (r, g, b, alpha)


def _create_decorative_svg(theme: dict, width: int, height: int, scene_num: int) -> str:
    """Creates a decorative SVG background based on the theme."""
    bg = theme["background"]
    acc1 = theme["accent_primary"]
    acc2 = theme["accent_secondary"]
    opacity = theme.get("decorative_opacity", 0.15)

    pattern = scene_num % 4
    svg_parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    svg_parts.append(f'<rect width="{width}" height="{height}" fill="{bg}"/>')

    if pattern == 0:
        svg_parts.append(
            f'<circle cx="{width}" cy="0" r="{int(width * 0.4)}" fill="{acc1}" opacity="{opacity}"/>'
        )
        svg_parts.append(
            f'<circle cx="0" cy="{height}" r="{int(width * 0.3)}" fill="{acc2}" opacity="{opacity}"/>'
        )
    elif pattern == 1:
        svg_parts.append(
            f'<rect x="{int(width * 0.7)}" y="0" width="{int(width * 0.4)}" height="{height}" fill="{acc1}" opacity="{opacity}"/>'
        )
        svg_parts.append(
            f'<circle cx="{int(width * 0.85)}" cy="{int(height * 0.5)}" r="{int(width * 0.2)}" fill="{acc2}" opacity="{opacity}"/>'
        )
    elif pattern == 2:
        svg_parts.append(
            f'<polygon points="0,0 {width},0 {width},{int(height * 0.6)}" fill="{acc1}" opacity="{opacity}"/>'
        )
        svg_parts.append(
            f'<circle cx="{int(width * 0.2)}" cy="{int(height * 0.8)}" r="{int(width * 0.15)}" fill="{acc2}" opacity="{opacity}"/>'
        )
    else:
        svg_parts.append(
            f'<rect x="0" y="0" width="{int(width * 0.15)}" height="{height}" fill="{acc1}" opacity="{opacity * 1.5}"/>'
        )
        svg_parts.append(
            f'<circle cx="{int(width * 0.5)}" cy="{int(height * 0.5)}" r="{int(width * 0.35)}" fill="none" stroke="{acc2}" stroke-width="3" opacity="{opacity}"/>'
        )

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def _render_svg_to_image(svg_string: str, width: int, height: int) -> Image.Image:
    """Renders an SVG string to a Pillow Image using cairosvg."""
    w, h = int(width), int(height)
    try:
        import cairosvg
        png_data = cairosvg.svg2png(
            bytestring=svg_string.encode("utf-8"),
            output_width=w,
            output_height=h,
        )
        return Image.open(io.BytesIO(png_data)).convert("RGBA")
    except ImportError:
        log.warning("cairosvg not available, using plain background")
        r, g, b = _hex_to_rgb("#1a1b26")
        return Image.new("RGBA", (w, h), (r, g, b, 255))
    except Exception as e:
        log.warning(f"SVG rendering failed: {e}, using plain background")
        r, g, b = _hex_to_rgb("#1a1b26")
        return Image.new("RGBA", (w, h), (r, g, b, 255))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """Wraps text to fit within max_width pixels."""
    if not text:
        return []
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))

    return lines


class SlideRenderer:
    """Renders individual slide images for video generation."""

    def __init__(self, theme: dict, output_dir: str, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT):
        self.theme = theme
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.width = width
        self.height = height
        self.is_portrait = self.height > self.width

    def _render_icon(self, img: Image.Image, icon_svg: str, x: float, size: float):
        """Render an icon SVG centered in the right portion of the slide."""
        try:
            import cairosvg
            svg_bytes = icon_svg.encode("utf-8")
            icon_png = cairosvg.svg2png(
                bytestring=svg_bytes,
                output_width=int(size),
                output_height=int(size),
            )
            icon_img = Image.open(io.BytesIO(icon_png)).convert("RGBA")

            icon_x = int(x + (size - icon_img.width) // 2)
            icon_y = int((self.height - icon_img.height) // 2)

            img.paste(icon_img, (icon_x, icon_y), icon_img)
        except Exception as e:
            log.warning(f"Failed to render icon: {e}")

    def _render_svg_illustration(self, img: Image.Image, svg: str, x: float, size: float):
        """Render an abstract SVG illustration in the right portion of the slide."""
        try:
            import cairosvg
            svg_bytes = svg.encode("utf-8")
            ill_png = cairosvg.svg2png(
                bytestring=svg_bytes,
                output_width=int(size),
                output_height=int(size),
            )
            ill_img = Image.open(io.BytesIO(ill_png)).convert("RGBA")

            ill_x = int(x + (size - ill_img.width) // 2)
            ill_y = int((self.height - ill_img.height) // 2)

            img.paste(ill_img, (ill_x, ill_y), ill_img)
        except Exception as e:
            log.warning(f"Failed to render illustration SVG: {e}")

    def render_slide(
        self,
        scene_num: int,
        title: str,
        content: list[str],
        svg_illustration: Optional[str] = None,
        icon_svg: Optional[str] = None,
    ) -> str:
        """
        Renders a single slide as a PNG file.

        Args:
            scene_num: Scene number for decorative variation.
            title: Slide title (short, max ~6 words).
            content: List of bullet points (1-3 items, each max ~10 words).
            svg_illustration: Optional abstract SVG string for right-side illustration.
            icon_svg: Optional icon SVG string (from icon library).

        Returns:
            Path to the rendered PNG file.
        """
        img = _render_svg_to_image(
            _create_decorative_svg(self.theme, self.width, self.height, scene_num),
            self.width,
            self.height,
        )
        draw = ImageDraw.Draw(img)

        scale = self.width / 1920
        title_font_size = max(int(72 * scale), 36)
        bullet_font_size = max(int(32 * scale), 18)

        title_font = _get_system_font("sans-serif", title_font_size)
        bullet_font = _get_system_font("sans-serif", bullet_font_size)

        text_primary = _hex_to_rgb(self.theme["text_primary"])
        accent = _hex_to_rgb(self.theme["accent_primary"])

        padding_x = int(120 * scale)
        padding_y = int(100 * scale)
        content_start_y = int(280 * scale)

        if self.is_portrait:
            svg_x = 0
            svg_size = int(self.width * 0.4)
            content_width = self.width - (padding_x * 2)
        else:
            has_right_content = svg_illustration or icon_svg
            if has_right_content:
                content_width = self.width * 0.5
                svg_x = self.width * 0.55
                svg_size = int(self.width * 0.35)
            else:
                content_width = self.width - (padding_x * 2)
                svg_x = 0
                svg_size = 0

        bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = bbox[2] - bbox[0]
        title_x = padding_x
        title_y = padding_y

        draw.text(
            (title_x, title_y),
            title,
            font=title_font,
            fill=text_primary,
        )
        draw.line(
            [(title_x, title_y + title_font_size + 10), (title_x + 200, title_y + title_font_size + 10)],
            fill=accent,
            width=4,
        )

        y_offset = content_start_y
        line_spacing = 10

        for i, bullet in enumerate(content[:3]):
            bullet_text = f"• {bullet}"
            wrapped_lines = _wrap_text(bullet_text, bullet_font, content_width - 60, draw)

            for line in wrapped_lines:
                bbox = draw.textbbox((0, 0), line, font=bullet_font)
                line_height = bbox[3] - bbox[1]

                draw.text(
                    (padding_x + 30, y_offset),
                    line,
                    font=bullet_font,
                    fill=text_primary,
                )
                y_offset += line_height + line_spacing

            y_offset += 20

        if icon_svg:
            self._render_icon(img, icon_svg, svg_x, svg_size)
        elif svg_illustration:
            self._render_svg_illustration(img, svg_illustration, svg_x, svg_size)

        output_path = self.output_dir / f"slide_{scene_num:02d}.png"
        img_rgb = Image.new("RGB", img.size, (0, 0, 0))
        img_rgb.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img_rgb.save(output_path, "PNG")
        log.debug(f"Slide rendered: {output_path}")

        return str(output_path)

    def render_title_slide(self, title: str, subtitle: str = "") -> str:
        """Renders a title slide (used for intro)."""
        img = _render_svg_to_image(
            _create_decorative_svg(self.theme, self.width, self.height, 0),
            self.width,
            self.height,
        )
        draw = ImageDraw.Draw(img)

        scale = self.width / 1920
        title_font_size = max(int(96 * scale), 48)
        subtitle_font_size = max(int(48 * scale), 24)

        title_font = _get_system_font("sans-serif", title_font_size)
        subtitle_font = _get_system_font("sans-serif", subtitle_font_size)

        text_primary = _hex_to_rgb(self.theme["text_primary"])
        accent = _hex_to_rgb(self.theme["accent_primary"])

        bbox = draw.textbbox((0, 0), title, font=title_font)
        title_x = (self.width - (bbox[2] - bbox[0])) // 2
        title_y = self.height // 2 - int(80 * scale)

        draw.text(
            (title_x, title_y),
            title,
            font=title_font,
            fill=text_primary,
        )

        if subtitle:
            bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
            sub_x = (self.width - (bbox[2] - bbox[0])) // 2
            sub_y = title_y + int(140 * scale)
            draw.text(
                (sub_x, sub_y),
                subtitle,
                font=subtitle_font,
                fill=accent,
            )

        output_path = self.output_dir / "slide_title.png"
        img_rgb = Image.new("RGB", img.size, (0, 0, 0))
        img_rgb.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img_rgb.save(output_path, "PNG")

        return str(output_path)
