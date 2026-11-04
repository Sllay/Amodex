"""Global adaptive color theme for Amodex web surfaces.

The theme is intentionally generated server-side from a small palette contract so
all current and future web surfaces can consume the same tonal system. A base
color is expanded along the white -> color -> black axis; multi-color themes can
assign colors by UI zone or softly mix all configured colors.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re

_DEFAULT_COLOR = "#8b5cf6"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_VALID_MODES = {"single", "split", "mix"}


@dataclass(frozen=True, slots=True)
class ThemeConfig:
    colors: tuple[str, ...] = (_DEFAULT_COLOR,)
    mode: str = "single"

    @classmethod
    def from_environment(cls) -> "ThemeConfig":
        raw = os.environ.get("AMODEX_THEME_COLORS", _DEFAULT_COLOR)
        colors = tuple(
            color.lower()
            for color in (part.strip() for part in raw.split(","))
            if _HEX.fullmatch(color)
        )[:4]
        if not colors:
            colors = (_DEFAULT_COLOR,)
        mode = os.environ.get("AMODEX_THEME_MODE", "single").strip().lower()
        if mode not in _VALID_MODES:
            mode = "single"
        if len(colors) == 1:
            mode = "single"
        return cls(colors=colors, mode=mode)


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, channel)):02x}" for channel in rgb)


def tone(color: str, amount: float) -> str:
    """Move ``color`` toward white (>0) or black (<0)."""

    amount = max(-1.0, min(1.0, amount))
    base = _rgb(color)
    target = (255, 255, 255) if amount >= 0 else (0, 0, 0)
    weight = abs(amount)
    return _hex(
        tuple(round(channel + (target_channel - channel) * weight) for channel, target_channel in zip(base, target))  # type: ignore[arg-type]
    )


def _zone(colors: tuple[str, ...], index: int) -> str:
    return colors[index % len(colors)]


def theme_css(config: ThemeConfig | None = None) -> str:
    config = config or ThemeConfig.from_environment()
    base = config.colors[0]
    colors = config.colors + (config.colors[-1],) * (4 - len(config.colors))
    c1, c2, c3, c4 = colors[:4]

    variables = {
        "--theme-base": base,
        "--theme-c1": c1,
        "--theme-c2": c2,
        "--theme-c3": c3,
        "--theme-c4": c4,
        "--theme-white": tone(base, 0.94),
        "--theme-pale": tone(base, 0.78),
        "--theme-light": tone(base, 0.46),
        "--theme-soft": tone(base, 0.20),
        "--theme-dark": tone(base, -0.30),
        "--theme-deep": tone(base, -0.62),
        "--theme-ink": tone(base, -0.84),
        "--theme-black": tone(base, -0.95),
        "--bg": tone(base, -0.955),
        "--panel": tone(base, -0.90),
        "--panel2": tone(base, -0.83),
        "--line": tone(base, -0.56),
        "--text": tone(base, 0.93),
        "--muted": tone(base, 0.48),
        "--accent": base,
    }
    root = ";".join(f"{key}:{value}" for key, value in variables.items())

    zone_colors = tuple(_zone(config.colors, idx) for idx in range(4))
    side, top, composer, context = zone_colors
    common = f"""
:root{{{root}}}
html{{background:var(--bg)}}
body{{background:var(--bg)!important;color:var(--text)!important}}
button,input,textarea,select{{border-color:color-mix(in srgb,var(--accent) 24%,var(--line))!important}}
button:hover{{border-color:var(--local-accent,var(--accent))!important}}
.primary{{background:linear-gradient(135deg,var(--theme-pale),var(--theme-base))!important;color:var(--theme-black)!important;border-color:transparent!important}}
.badge{{border-color:color-mix(in srgb,var(--local-accent,var(--accent)) 34%,var(--line))!important}}
.side{{--local-accent:{side}}}.topbar{{--local-accent:{top}}}.composer-wrap,.composer{{--local-accent:{composer}}}.context{{--local-accent:{context}}}
.message.owner .bubble{{background:linear-gradient(135deg,color-mix(in srgb,var(--theme-base) 16%,var(--panel2)),var(--panel2))!important}}
.dialog,.context-card,.composer{{box-shadow:0 16px 55px rgba(0,0,0,.20)}}
::selection{{background:color-mix(in srgb,var(--theme-base) 60%,transparent);color:white}}
"""

    if config.mode == "split":
        return common + f"""
.side{{background:linear-gradient(180deg,{tone(side,-0.88)},var(--panel))!important}}
.topbar{{background:linear-gradient(90deg,{tone(top,-0.90)},color-mix(in srgb,var(--bg) 94%,transparent))!important}}
.composer{{background:linear-gradient(145deg,{tone(composer,-0.88)},var(--panel))!important}}
.context{{background:linear-gradient(180deg,{tone(context,-0.88)},var(--panel))!important}}
"""

    if config.mode == "mix":
        stops = ",".join(
            f"radial-gradient(circle at {18 + idx * 24}% {16 + (idx % 2) * 56}%,color-mix(in srgb,{color} 16%,transparent),transparent 38%)"
            for idx, color in enumerate(config.colors)
        )
        surface = ",".join(
            f"color-mix(in srgb,{color} {8 + idx * 2}%,transparent)"
            for idx, color in enumerate(config.colors)
        )
        return common + f"""
body::before{{content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;background:{stops};filter:saturate(1.05);}}
.composer,.dialog,.context-card{{background-image:linear-gradient(135deg,{surface},transparent 74%)!important}}
"""

    return common + """
.side,.context{background:linear-gradient(180deg,var(--theme-black),var(--panel))!important}
.topbar{background:color-mix(in srgb,var(--theme-black) 90%,transparent)!important}
.composer{background:linear-gradient(145deg,var(--theme-ink),var(--panel))!important}
"""


def augment_theme_html(html: str, config: ThemeConfig | None = None) -> str:
    """Apply the global theme after each page's local CSS without owning its DOM."""

    config = config or ThemeConfig.from_environment()
    style = f'<style id="amodex-global-theme">{theme_css(config)}</style>'
    result = html.replace("</head>", style + "</head>", 1)
    marker = '<html lang="pt-BR"'
    if marker in result:
        result = result.replace(
            marker,
            f'<html lang="pt-BR" data-amodex-theme-mode="{config.mode}"',
            1,
        )
    return result
