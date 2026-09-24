"""Colour maths for the palette check (docs/design/visual-system.md#colour): sRGB →
OKLab distance, WCAG contrast, and colour-vision-deficiency simulation (Machado et al. 2009,
severity 1.0). Standard library only."""

import math

# Machado 2009, severity 1.0, applied to linear RGB.
CVD = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}  # fmt: skip


def rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def simulate(colour: tuple[float, float, float], kind: str) -> tuple[float, float, float]:
    lin = [_lin(c) for c in colour]
    return tuple(_gamma(sum(m * v for m, v in zip(row, lin, strict=True))) for row in CVD[kind])  # type: ignore[return-value]


def luminance(colour: tuple[float, float, float]) -> float:
    r, g, b = (_lin(c) for c in colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def oklab(colour: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (_lin(c) for c in colour)
    lms = (
        0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
        0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
        0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
    )
    l_, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in lms)
    return (
        0.2104542553 * l_ + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l_ - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l_ + 0.7827717662 * m - 0.8086757660 * s,
    )


def delta_e(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Euclidean distance in OKLab, × 100 (the scale visual-system.md quotes)."""
    return 100 * math.dist(oklab(a), oklab(b))
