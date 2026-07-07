"""Ölçüm iş parçacığı: tarama noktalarını üretir, cihazı sürer, canlı veri yayar."""

from __future__ import annotations

import math
from typing import List

import numpy as np
from PySide6.QtCore import QThread, Signal

from .config import MeasurementConfig
from .instrument import InstrumentError


def build_levels(cfg: MeasurementConfig) -> List[float]:
    """Yapılandırmaya göre kaynak seviyeleri listesini üret."""
    s = cfg.source
    if s.mode == "bias":
        return [s.bias_level] * max(1, s.bias_readings)

    if s.mode == "list":
        if not s.list_values:
            raise ValueError("Liste modu için en az bir değer girin")
        levels = list(s.list_values)
    elif s.mode == "log":
        if s.start == 0 or s.stop == 0:
            raise ValueError("Log taramada başlangıç/bitiş sıfır olamaz")
        if math.copysign(1, s.start) != math.copysign(1, s.stop):
            raise ValueError("Log taramada başlangıç ve bitiş aynı işaretli olmalı")
        sign = math.copysign(1.0, s.start)
        levels = list(sign * np.logspace(math.log10(abs(s.start)),
                                         math.log10(abs(s.stop)), s.points))
    else:  # lin
        if s.points < 2:
            raise ValueError("Tarama için en az 2 nokta gerekli")
        levels = list(np.linspace(s.start, s.stop, s.points))

    if s.dual:
        levels = levels + levels[-2::-1]
    return [float(x) for x in levels]


class MeasurementWorker(QThread):
    """Noktaları tek tek sürer; her nokta için sinyal yayar (canlı grafik)."""

    # idx, kaynak seviyesi, V, I, compliance uyarısı
    point_measured = Signal(int, float, float, float, bool)
    finished_ok = Signal(list)   # [(level, v, i, tripped), ...]
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, instrument, cfg: MeasurementConfig, parent=None):
        super().__init__(parent)
        self._inst = instrument
        self._cfg = cfg
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        inst, cfg = self._inst, self._cfg
        rows = []
        try:
            levels = build_levels(cfg)
            self.status.emit("Cihaz yapılandırılıyor...")
            inst.configure(cfg)
            self.status.emit(f"Ölçüm başladı: {len(levels)} nokta")
            inst.output(True)
            try:
                for idx, level in enumerate(levels):
                    if self._stop:
                        self.status.emit("Ölçüm kullanıcı tarafından durduruldu")
                        break
                    if cfg.source.reversal and cfg.source.function == "current" and level != 0:
                        inst.set_level(+level)
                        v_plus, i_plus, t1 = inst.read_point()
                        inst.set_level(-level)
                        v_minus, _, t2 = inst.read_point()
                        v = (v_plus - v_minus) / 2.0
                        i, tripped = i_plus, (t1 or t2)
                    else:
                        inst.set_level(level)
                        v, i, tripped = inst.read_point()
                    v, i, tripped = float(v), float(i), bool(tripped)
                    rows.append((level, v, i, tripped))
                    self.point_measured.emit(idx, level, v, i, tripped)
            finally:
                inst.output(False)
            self.finished_ok.emit(rows)
        except (InstrumentError, ValueError) as e:
            self.failed.emit(str(e))
        except Exception as e:  # beklenmeyen hatada da arayüzü bilgilendir
            self.failed.emit(f"Beklenmeyen hata: {e!r}")
