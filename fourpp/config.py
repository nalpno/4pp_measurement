"""Ölçüm yapılandırması: KickStart I-V Characterizer'daki ayarların karşılıkları."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List


@dataclass
class SourceCfg:
    """Kaynak (source) ayarları — KickStart 'Source Settings' paneli karşılığı."""

    function: str = "current"          # "current" | "voltage"
    mode: str = "lin"                  # "lin" | "log" | "list" | "bias"
    start: float = 1e-4                # tarama başlangıcı (A veya V)
    stop: float = 1e-3                 # tarama bitişi (A veya V)
    points: int = 11                   # adım sayısı
    list_values: List[float] = field(default_factory=list)
    bias_level: float = 1e-3           # sabit modda kaynak seviyesi
    bias_readings: int = 5             # sabit modda okuma sayısı
    dual: bool = False                 # ileri + geri tarama
    reversal: bool = True              # her noktada +I/−I (termal EMF eliminasyonu)
    range_auto: bool = True
    range_value: float = 1e-3
    compliance: float = 21.0           # akım kaynağı için V limiti, gerilim için A limiti
    delay_auto: bool = False
    delay: float = 0.1                 # kaynak gecikmesi (s)


@dataclass
class MeasureCfg:
    """Ölçüm (measure) ayarları — KickStart 'Measure Settings' paneli karşılığı."""

    nplc: float = 1.0                  # entegrasyon süresi (0.01–10 PLC)
    range_auto: bool = True
    range_value: float = 2.0
    auto_zero: bool = True
    filter_on: bool = False
    filter_count: int = 10
    four_wire: bool = True             # 4PP için her zaman önerilir
    terminals: str = "front"           # "front" | "rear"


@dataclass
class SampleCfg:
    """Numune geometrisi ve hesap ayarları."""

    name: str = ""
    geometry: str = "infinite"         # "infinite" | "circle" | "manual"
    diameter_mm: float = 100.0         # dairesel numune çapı
    spacing_mm: float = 1.016          # Signatone SP4 tipik uç aralığı (40 mil)
    manual_factor: float = 4.5324
    thickness_mm: float = 0.0          # 0 = bilinmiyor (özdirenç hesaplanmaz)
    thickness_corr: bool = True        # kalın numunede t/s düzeltmesi


@dataclass
class MeasurementConfig:
    source: SourceCfg = field(default_factory=SourceCfg)
    measure: MeasureCfg = field(default_factory=MeasureCfg)
    sample: SampleCfg = field(default_factory=SampleCfg)
    line_freq: float = 50.0            # şebeke frekansı (TR: 50 Hz) — zaman tahmini için

    def reading_time(self) -> float:
        """Tek bir okumanın tahmini süresi (s) — seri port zaman aşımı için."""
        t = self.measure.nplc / self.line_freq
        if self.measure.filter_on:
            t *= max(1, self.measure.filter_count)
        if self.measure.auto_zero:
            t *= 3.0  # autozero her okumada ek çevrimler yapar
        delay = 1.0 if self.source.delay_auto else self.source.delay
        return t + delay

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "MeasurementConfig":
        cfg = MeasurementConfig()
        for section, cls in (("source", SourceCfg), ("measure", MeasureCfg), ("sample", SampleCfg)):
            data = d.get(section, {})
            obj = getattr(cfg, section)
            for k, v in data.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
        cfg.line_freq = d.get("line_freq", 50.0)
        return cfg
