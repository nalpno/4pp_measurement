"""Ölçüm sonuçlarını CSV olarak kaydetme."""

from __future__ import annotations

import csv
from datetime import datetime
from typing import List, Optional, Tuple

from .analysis import FitResult, SheetResult
from .config import MeasurementConfig

MODE_NAMES = {"lin": "Lineer tarama", "log": "Logaritmik tarama",
              "list": "Liste taraması", "bias": "Sabit seviye"}


def write_csv(path: str, rows: List[Tuple[float, float, float, bool]],
              cfg: MeasurementConfig, fit: Optional[FitResult],
              sheet: Optional[SheetResult], idn: str = "") -> None:
    """Başlıkta metadata (#...), ardından nokta verileri.

    rows: (kaynak_seviyesi, V, I, compliance) listesi.
    utf-8-sig: Excel'in Türkçe karakterleri doğru açması için.
    """
    s, m, smp = cfg.source, cfg.measure, cfg.sample
    src_unit = "A" if s.function == "current" else "V"

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        meta = [
            ("Tarih", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("Cihaz", idn or "Keithley 2400"),
            ("Numune", smp.name),
            ("Kaynak fonksiyonu", "Akım" if s.function == "current" else "Gerilim"),
            ("Mod", MODE_NAMES.get(s.mode, s.mode)),
            (f"Başlangıç ({src_unit})", f"{s.start:.6E}"),
            (f"Bitiş ({src_unit})", f"{s.stop:.6E}"),
            ("Nokta sayısı", s.points),
            ("Çift yönlü tarama", "Evet" if s.dual else "Hayır"),
            ("Akım ters çevirme", "Evet" if s.reversal else "Hayır"),
            ("Compliance", f"{s.compliance:g}"),
            ("Kaynak gecikmesi (s)", "Oto" if s.delay_auto else f"{s.delay:g}"),
            ("NPLC", f"{m.nplc:g}"),
            ("Auto zero", "Açık" if m.auto_zero else "Kapalı"),
            ("Filtre", f"{m.filter_count} okuma" if m.filter_on else "Kapalı"),
            ("4 telli ölçüm", "Evet" if m.four_wire else "Hayır"),
            ("Terminal", "Ön" if m.terminals == "front" else "Arka"),
            ("Uç aralığı (mm)", f"{smp.spacing_mm:g}"),
        ]
        if smp.thickness_mm > 0:
            meta.append(("Film kalınlığı (mm)", f"{smp.thickness_mm:g}"))
        if fit is not None:
            meta += [
                ("R - fit eğimi (Ohm)", f"{fit.resistance:.6E}"),
                ("Ofset (V)", f"{fit.offset:.6E}"),
                ("R^2", f"{fit.r_squared:.6f}"),
            ]
        if sheet is not None:
            meta += [
                ("Geometrik düzeltme faktörü", f"{sheet.correction_factor:.4f}"),
                ("Sheet resistance (Ohm/sq)", f"{sheet.sheet_resistance:.6E}"),
            ]
            if sheet.thickness_factor is not None:
                meta.append(("Kalınlık düzeltme faktörü", f"{sheet.thickness_factor:.4f}"))
            if sheet.resistivity_ohm_cm is not None:
                meta.append(("Özdirenç (Ohm.cm)", f"{sheet.resistivity_ohm_cm:.6E}"))
            if sheet.conductivity_s_cm is not None:
                meta.append(("İletkenlik (S/cm)", f"{sheet.conductivity_s_cm:.6E}"))

        for key, val in meta:
            w.writerow([f"# {key}", val])
        w.writerow([])
        w.writerow(["Nokta", f"Kaynak ({src_unit})", "V (V)", "I (A)",
                    "R = V/I (Ohm)", "Compliance"])
        for idx, (level, v, i, tripped) in enumerate(rows, start=1):
            r = v / i if i != 0 else ""
            w.writerow([idx, f"{level:.6E}", f"{v:.6E}", f"{i:.6E}",
                        f"{r:.6E}" if r != "" else "", "UYARI" if tripped else ""])
