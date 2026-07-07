"""Dört nokta prob analiz hesapları: lineer fit, sheet resistance, özdirenç.

Formüller:
  - Sonsuz ince tabaka:  Rs = (pi/ln2) * (V/I) = 4.5324 * R
  - Dairesel numune (çap D, uç aralığı s, problar merkezde):
        C(D/s) = pi / ( ln2 + ln[ ((D/s)^2 + 3) / ((D/s)^2 - 3) ] )
    D/s -> sonsuz limitinde C -> pi/ln2.
  - Kalınlık düzeltmesi (t: kalınlık, s: uç aralığı):
        K(t/s) = ln2 / ln( sinh(t/s) / sinh(t/(2s)) )
    t/s -> 0 limitinde K -> 1 (ince film).
  - Özdirenç: rho = Rs * t * K   (Ohm·cm için t cm cinsinden)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

PI_OVER_LN2 = math.pi / math.log(2.0)  # 4.53236...


@dataclass
class FitResult:
    resistance: float        # Ohm (V/I eğimi)
    offset: float            # V (termal EMF / ofset)
    r_squared: float
    n_points: int


@dataclass
class SheetResult:
    correction_factor: float           # geometrik faktör C
    sheet_resistance: float            # Ohm/sq
    thickness_factor: Optional[float]  # K (kalınlık düzeltmesi), yoksa None
    resistivity_ohm_cm: Optional[float]
    conductivity_s_cm: Optional[float]


def linear_fit(current_a: Sequence[float], voltage_v: Sequence[float]) -> FitResult:
    """V = R*I + V0 doğrusal fiti. R eğimden, R^2 uyum kalitesinden."""
    i = np.asarray(current_a, dtype=float)
    v = np.asarray(voltage_v, dtype=float)
    if i.size < 2:
        if i.size == 1 and i[0] != 0.0:
            return FitResult(resistance=float(v[0] / i[0]), offset=0.0,
                             r_squared=1.0, n_points=1)
        raise ValueError("Fit için en az 2 nokta gerekli")
    slope, intercept = np.polyfit(i, v, 1)
    v_pred = slope * i + intercept
    ss_res = float(np.sum((v - v_pred) ** 2))
    ss_tot = float(np.sum((v - np.mean(v)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return FitResult(resistance=float(slope), offset=float(intercept),
                     r_squared=r2, n_points=int(i.size))


def geometry_factor(geometry: str, diameter_mm: float = 0.0,
                    spacing_mm: float = 1.016, manual_factor: float = PI_OVER_LN2) -> float:
    """Geometrik düzeltme faktörü C: Rs = C * (V/I)."""
    if geometry == "infinite":
        return PI_OVER_LN2
    if geometry == "manual":
        if manual_factor <= 0:
            raise ValueError("Manuel düzeltme faktörü pozitif olmalı")
        return manual_factor
    if geometry == "circle":
        if spacing_mm <= 0 or diameter_mm <= 0:
            raise ValueError("Çap ve uç aralığı pozitif olmalı")
        d = diameter_mm / spacing_mm
        if d <= math.sqrt(3.0) + 1e-9:
            raise ValueError("Numune çapı çok küçük: D/s > sqrt(3) olmalı")
        return math.pi / (math.log(2.0) + math.log((d * d + 3.0) / (d * d - 3.0)))
    raise ValueError(f"Bilinmeyen geometri: {geometry}")


def thickness_factor(thickness_mm: float, spacing_mm: float) -> float:
    """Kalınlık düzeltme faktörü K. İnce filmde (t << s) K ~= 1."""
    if thickness_mm <= 0 or spacing_mm <= 0:
        raise ValueError("Kalınlık ve uç aralığı pozitif olmalı")
    x = thickness_mm / spacing_mm
    if x < 0.01:
        return 1.0  # sayısal olarak zaten 1'e çok yakın
    return math.log(2.0) / math.log(math.sinh(x) / math.sinh(x / 2.0))


def compute_sheet(resistance_ohm: float, geometry: str, diameter_mm: float,
                  spacing_mm: float, manual_factor: float,
                  thickness_mm: float, apply_thickness_corr: bool) -> SheetResult:
    """Fit edilen dirençten Rs, (varsa) özdirenç ve iletkenlik hesabı."""
    c = geometry_factor(geometry, diameter_mm, spacing_mm, manual_factor)
    rs = c * resistance_ohm

    k: Optional[float] = None
    rho: Optional[float] = None
    sigma: Optional[float] = None
    if thickness_mm > 0:
        k = thickness_factor(thickness_mm, spacing_mm) if apply_thickness_corr else 1.0
        thickness_cm = thickness_mm / 10.0
        rho = rs * thickness_cm * k
        if rho != 0:
            sigma = 1.0 / rho
    return SheetResult(correction_factor=c, sheet_resistance=rs,
                       thickness_factor=k, resistivity_ohm_cm=rho,
                       conductivity_s_cm=sigma)
