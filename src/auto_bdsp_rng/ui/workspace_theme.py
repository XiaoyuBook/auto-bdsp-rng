"""Small shared design tokens for native Qt workspaces (logical pixels)."""

from string import Template

SURFACE = "#FFFFFF"
SURFACE_MUTED = "#F6F8F7"
TEXT = "#24312D"
TEXT_SECONDARY = "#596C62"
BORDER = "#E2E8E4"
ACCENT = "#087C58"
ACCENT_SOFT = "#EDF7F1"
WARNING = "#906423"
WARNING_SOFT = "#FFF7E8"
ERROR = "#AC4B42"
ERROR_SOFT = "#FFF4F1"
FOCUS = "#176B97"
CONTROL_HEIGHT = 32
CONTROL_RADIUS = 5


def workspace_styles(template: str) -> str:
    """Resolve shared colors without interfering with QSS block braces."""
    return Template(template).substitute(
        surface=SURFACE, surface_muted=SURFACE_MUTED, text=TEXT,
        text_secondary=TEXT_SECONDARY, border=BORDER, accent=ACCENT,
        accent_soft=ACCENT_SOFT, warning=WARNING, warning_soft=WARNING_SOFT,
        error=ERROR, error_soft=ERROR_SOFT,
    )


def focus_styles(*selectors: str) -> str:
    """Keep input borders at one pixel and add an inset outline on buttons."""
    return ",\n".join(f"{selector}:focus" for selector in selectors) + (
        f" {{ border: 1px solid {FOCUS}; outline: 2px dotted {FOCUS}; outline-offset: -3px; }}"
    )
