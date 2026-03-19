"""
slide_svg_generator.py — Generates SVG illustrations using a local LLM
=====================================================================
Second-stage LLM call that creates abstract geometric illustrations based
on the scene's text content and visual theme.
"""

import logging
import random
import re

import ollama

log = logging.getLogger("vi.svg_gen")

SVG_SYSTEM_PROMPT = """\
You are a geometric art generator. You create SVG images with simple shapes.

IMPORTANT: Do NOT include a background rect. The SVG will be placed over an existing dark background.

RULES:
- Output ONLY this exact format, nothing else before or after:
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
[SHAPES using COLOR1 and COLOR2]
</svg>

- COLOR1 and COLOR2 will be provided by the user
- Use ONLY: circle, rect, ellipse
- Attributes allowed: x, y, width, height, r, cx, cy, fill, opacity, rx
- Do NOT use: path, text, gradient, polygon, polyline, line, transform, stroke
- Create 3-6 simple geometric shapes with varying opacity (0.3-0.7)
- Do NOT include <rect> that fills the entire background
- Example: <circle cx="400" cy="300" r="150" fill="COLOR1" opacity="0.6"/>
"""

SVG_EXAMPLE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
<circle cx="600" cy="200" r="200" fill="7aa2f7" opacity="0.4"/>
<circle cx="200" cy="600" r="150" fill="bb9af7" opacity="0.5"/>
<rect x="100" y="300" width="200" height="200" rx="20" fill="7aa2f7" opacity="0.3"/>
<ellipse cx="400" cy="400" rx="180" ry="120" fill="bb9af7" opacity="0.35"/>
</svg>
"""

DEFAULT_SVGS = [
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
<circle cx="600" cy="200" r="200" fill="COLOR1" opacity="0.4"/>
<circle cx="200" cy="600" r="150" fill="COLOR2" opacity="0.5"/>
<rect x="100" y="300" width="200" height="200" rx="20" fill="COLOR1" opacity="0.3"/>
<ellipse cx="400" cy="400" rx="180" ry="120" fill="COLOR2" opacity="0.35"/>
</svg>""",
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
<ellipse cx="400" cy="400" rx="300" ry="200" fill="COLOR1" opacity="0.3"/>
<circle cx="300" cy="300" r="100" fill="COLOR2" opacity="0.5"/>
<rect x="450" y="350" width="150" height="150" rx="75" fill="COLOR1" opacity="0.4"/>
<circle cx="550" cy="200" r="80" fill="COLOR2" opacity="0.6"/>
</svg>""",
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
<rect x="50" y="50" width="300" height="300" rx="30" fill="COLOR1" opacity="0.3"/>
<rect x="450" y="450" width="300" height="300" rx="30" fill="COLOR2" opacity="0.3"/>
<circle cx="400" cy="400" r="120" fill="COLOR1" opacity="0.5"/>
<circle cx="250" cy="250" r="60" fill="COLOR2" opacity="0.6"/>
</svg>""",
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
<circle cx="200" cy="200" r="180" fill="COLOR1" opacity="0.35"/>
<circle cx="600" cy="600" r="180" fill="COLOR2" opacity="0.35"/>
<circle cx="400" cy="400" r="120" fill="COLOR1" opacity="0.5"/>
<ellipse cx="300" cy="550" rx="100" ry="60" fill="COLOR2" opacity="0.4"/>
</svg>""",
]


def _apply_colors(svg_template: str, color1: str, color2: str) -> str:
    """Replace placeholder colors in SVG template."""
    svg = svg_template
    c1 = color1 if color1.startswith("#") else f"#{color1}"
    c2 = color2 if color2.startswith("#") else f"#{color2}"
    svg = svg.replace("COLOR1", c1)
    svg = svg.replace("COLOR2", c2)
    return svg


def _remove_background_rect(svg: str) -> str:
    """Remove any rect that fills the entire SVG background."""
    svg = re.sub(
        r'<rect[^>]*width="800"[^>]*height="800"[^>]*/>', 
        '', 
        svg
    )
    svg = re.sub(
        r'<rect[^>]*height="800"[^>]*width="800"[^>]*/>', 
        '', 
        svg
    )
    svg = re.sub(
        r'<rect[^>]*/>', 
        '', 
        svg,
        count=1
    )
    return svg


def _extract_svg(text: str) -> str | None:
    """Extracts SVG code from LLM response, with fallback for truncated responses."""
    text = text.strip()

    svg_match = re.search(r"<svg[\s\S]*?</svg>", text, re.IGNORECASE)
    if svg_match:
        return svg_match.group(0)

    if "<svg" in text:
        start = text.find("<svg")
        end = text.find("</svg>")
        if end == -1:
            end = text.rfind("/>") + 2
            if end == 1:
                end = len(text)
        else:
            end += 6

        svg = text[start:end]
        
        if not svg.strip().endswith("</svg>") and not svg.strip().endswith("/>"):
            svg = svg.rstrip() + "/>"
        
        if not svg.strip().endswith("</svg>") and "</svg>" not in svg:
            svg = svg + "</svg>"

        return svg

    return None


def _clean_svg(svg: str) -> str:
    """Clean and validate SVG code, removing problematic elements and background rects."""
    svg = re.sub(r'<path[^>]*/?>', '', svg)
    svg = re.sub(r'<text[^>]*/?>', '', svg)
    svg = re.sub(r'<[^>]*(?:stroke|gradient|transform)[^>]*/?>', '', svg)
    svg = re.sub(r' stroke="[^"]*"', '', svg)
    svg = re.sub(r' stroke-width="[^"]*"', '', svg)
    svg = re.sub(r' transform="[^"]*"', '', svg)
    
    svg = _remove_background_rect(svg)
    
    svg = re.sub(r'fill="([0-9a-fA-F]{6})"', r'fill="#\1"', svg)
    svg = re.sub(r"fill='([0-9a-fA-F]{6})'", r"fill='#\1'", svg)
    
    if not re.search(r'viewBox="[^"]*"', svg) and not re.search(r"viewBox='[^']*'", svg):
        svg = svg.replace("<svg", '<svg viewBox="0 0 800 800"', 1)

    return svg


def generate_svg_illustration(
    scene_text: str,
    llm_model: str,
    color_primary: str,
    color_secondary: str,
    color_accent: str,
) -> str:
    """
    Generates an SVG illustration for a scene using the local LLM.
    Falls back to a template with theme colors if LLM fails.
    """
    user_prompt = f"""Create a simple geometric SVG illustration using these colors:
- Primary color: {color_primary.lstrip("#")}
- Secondary color: {color_secondary.lstrip("#")}

Topic: {scene_text[:200]}

IMPORTANT RULES:
- Do NOT include a background rect that fills the entire SVG
- Create abstract geometric shapes (circles, rects, ellipses) scattered around
- Use opacity values between 0.3 and 0.7 for transparency
- The SVG will be placed over an existing dark background

{SVG_EXAMPLE}

Output ONLY the SVG code:"""

    for attempt in range(2):
        try:
            log.info(f"  [SVG] Generating illustration with {llm_model}...")
            response = ollama.generate(
                model=llm_model,
                prompt=user_prompt,
                system=SVG_SYSTEM_PROMPT,
                options={"temperature": 0.6, "num_predict": 200},
            )

            svg_code = _extract_svg(response["response"])

            log.info(f"  [SVG] Raw LLM response:\n{response['response'][:500]}")

            if svg_code:
                svg_code = _clean_svg(svg_code)
                log.info("  [SVG] Illustration generated successfully")
                return _apply_colors(svg_code, color_primary, color_secondary)

            log.warning(f"  [SVG] Attempt {attempt + 1}: No valid SVG found")
            log.warning(f"  [SVG] Full response was:\n{response['response'][:1000]}")

        except Exception as e:
            log.warning(f"  [SVG] Attempt {attempt + 1} failed: {e}")

    fallback = random.choice(DEFAULT_SVGS)
    log.info("  [SVG] Using fallback SVG with theme colors")
    return _apply_colors(fallback, color_primary, color_secondary)


def validate_svg(svg_code: str) -> bool:
    """Basic validation that the SVG has proper structure."""
    if not svg_code:
        return False
    return "<svg" in svg_code and "</svg>" in svg_code
