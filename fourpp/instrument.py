"""Keithley 2400 SCPI haberleşmesi (RS-232 veya GPIB/VISA) ve simülatör.

RS-232 için cihaz ön paneli (MENU -> COMMUNICATION -> RS-232) programdaki
bağlantı ayarlarıyla aynı olmalıdır:
  BAUD: 9600 (önerilen), BITS: 8, PARITY: NONE, TERMINATOR: <CR+LF>, FLOW-CTRL: NONE

GPIB için cihazda MENU -> COMMUNICATION -> GPIB seçili olmalı ve adres
(varsayılan 24) programdaki adresle aynı olmalıdır. GPIB kullanımı için
bilgisayarda adaptörün sürücüsü + bir VISA kütüphanesi (NI-VISA veya
Keysight IO Libraries) ve 'pip install pyvisa' gerekir.
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Optional, Tuple

from .config import MeasurementConfig

TERMINATORS = {"CR": "\r", "LF": "\n", "CRLF": "\r\n"}
BAUD_RATES = [9600, 19200, 38400, 57600, 4800, 2400, 1200, 600, 300]
DEFAULT_GPIB_ADDRESS = 24  # Keithley 2400 fabrika varsayılanı


class InstrumentError(Exception):
    pass


class _Keithley2400Base:
    """SCPI komut mantığı — taşıma katmanından (seri/VISA) bağımsız.

    Alt sınıflar write(cmd) ve query(cmd, timeout=None) sağlamalıdır.
    """

    _cfg: Optional[MeasurementConfig] = None

    def write(self, cmd: str) -> None:  # pragma: no cover - soyut
        raise NotImplementedError

    def query(self, cmd: str, timeout: Optional[float] = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def drain_errors(self) -> List[str]:
        """Cihaz hata kuyruğunu boşalt; hata mesajlarını döndür."""
        errors: List[str] = []
        for _ in range(20):
            resp = self.query(":SYST:ERR?")
            if resp.startswith("0") or "No error" in resp:
                break
            errors.append(resp)
        return errors

    def configure(self, cfg: MeasurementConfig) -> None:
        self._cfg = cfg
        s, m = cfg.source, cfg.measure
        cmds: List[str] = ["*RST", ":SYST:BEEP:STAT OFF"]

        cmds.append(":ROUT:TERM FRON" if m.terminals == "front" else ":ROUT:TERM REAR")
        cmds.append(":SYST:RSEN ON" if m.four_wire else ":SYST:RSEN OFF")

        if s.function == "current":
            src, sen = "CURR", "VOLT"
        else:
            src, sen = "VOLT", "CURR"

        cmds.append(f":SOUR:FUNC {src}")
        cmds.append(f":SOUR:{src}:MODE FIX")
        if s.range_auto:
            cmds.append(f":SOUR:{src}:RANG:AUTO ON")
        else:
            cmds.append(f":SOUR:{src}:RANG {s.range_value:.6E}")

        cmds.append(f':SENS:FUNC "{sen}"')
        cmds.append(f":SENS:{sen}:PROT {s.compliance:.6E}")
        if m.range_auto:
            cmds.append(f":SENS:{sen}:RANG:AUTO ON")
        else:
            cmds.append(f":SENS:{sen}:RANG {m.range_value:.6E}")
        cmds.append(f":SENS:{sen}:NPLC {m.nplc:g}")

        cmds.append(":SYST:AZER:STAT ON" if m.auto_zero else ":SYST:AZER:STAT OFF")

        if m.filter_on:
            cmds += [":SENS:AVER:TCON REP",
                     f":SENS:AVER:COUN {m.filter_count}",
                     ":SENS:AVER:STAT ON"]
        else:
            cmds.append(":SENS:AVER:STAT OFF")

        if s.delay_auto:
            cmds.append(":SOUR:DEL:AUTO ON")
        else:
            cmds.append(f":SOUR:DEL {s.delay:g}")

        cmds += [":FORM:ELEM VOLT,CURR", ":TRIG:COUN 1"]

        for c in cmds:
            self.write(c)
            time.sleep(0.02)  # komutların işlenmesine zaman tanı

        errors = self.drain_errors()
        if errors:
            raise InstrumentError("Cihaz yapılandırma hatası: " + "; ".join(errors))

    def output(self, on: bool) -> None:
        self.write(":OUTP ON" if on else ":OUTP OFF")

    def set_level(self, level: float) -> None:
        assert self._cfg is not None
        src = "CURR" if self._cfg.source.function == "current" else "VOLT"
        self.write(f":SOUR:{src} {level:.6E}")

    def read_point(self) -> Tuple[float, float, bool]:
        """Bir okuma yap: (V, I, compliance_uyarısı) döndürür."""
        assert self._cfg is not None
        timeout = self._cfg.reading_time() * 2.0 + 3.0
        resp = self.query(":READ?", timeout=timeout)
        parts = resp.split(",")
        if len(parts) < 2:
            raise InstrumentError(f"Beklenmeyen ölçüm yanıtı: {resp!r}")
        v, i = float(parts[0]), float(parts[1])

        sen = "VOLT" if self._cfg.source.function == "current" else "CURR"
        tripped = False
        try:
            tripped = self.query(f":SENS:{sen}:PROT:TRIP?").strip().startswith("1")
        except Exception:
            pass
        return v, i, tripped


class Keithley2400(_Keithley2400Base):
    """RS-232 (USB-RS232 çevirici) üzerinden Keithley 2400 kontrolü."""

    def __init__(self, port: str, baud: int = 9600, terminator: str = "CRLF",
                 xonxoff: bool = False, timeout: float = 5.0):
        import serial  # pyserial

        self.timeout = timeout
        self.term = TERMINATORS.get(terminator, "\r\n")
        self._cfg = None
        try:
            self._ser = serial.Serial(
                port=port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                timeout=0.2, xonxoff=xonxoff, rtscts=False, dsrdtr=False,
            )
        except Exception as e:
            raise InstrumentError(f"Seri port açılamadı ({port}): {e}") from e

    def write(self, cmd: str) -> None:
        self._ser.write((cmd + self.term).encode("ascii"))

    def _read_line(self, timeout: Optional[float] = None) -> str:
        deadline = time.monotonic() + (timeout or self.timeout)
        buf = bytearray()
        while time.monotonic() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            if b in (b"\r", b"\n"):
                if buf:
                    return buf.decode("ascii", errors="replace")
                continue  # satır başındaki artık terminatörleri atla
            buf += b
        raise InstrumentError(
            "Cihazdan yanıt alınamadı (zaman aşımı). Kabloyu, portu, baud ve "
            "terminatör ayarlarını kontrol edin; cihazda RS-232 seçili olmalı."
        )

    def query(self, cmd: str, timeout: Optional[float] = None) -> str:
        self._ser.reset_input_buffer()
        self.write(cmd)
        return self._read_line(timeout)

    def open_session(self) -> str:
        """Cihazı tanı ve uzaktan kumanda moduna al. IDN dizesini döndürür."""
        idn = self.query("*IDN?")
        try:
            self.write(":SYST:REM")  # ön paneli kilitle (yalnızca RS-232)
        except Exception:
            pass
        return idn

    def close(self) -> None:
        try:
            self.write(":OUTP OFF")
            self.write(":SYST:LOC")
        except Exception:
            pass
        try:
            self._ser.close()
        except Exception:
            pass


class Keithley2400Visa(_Keithley2400Base):
    """GPIB (USB-GPIB çevirici) veya başka bir VISA kaynağı üzerinden kontrol.

    NI-VISA / Keysight IO Libraries gibi bir VISA kütüphanesi kurulu olmalı.
    """

    def __init__(self, resource: str, timeout: float = 5.0):
        try:
            import pyvisa
        except ImportError as e:
            raise InstrumentError(
                "GPIB için 'pyvisa' paketi gerekli: pip install pyvisa "
                "(ayrıca NI-VISA veya Keysight IO Libraries kurulu olmalı)"
            ) from e
        # Önce sistemdeki VISA kütüphanesini (NI-VISA/Keysight), olmazsa
        # pyvisa-py (@py) yedeğini dene. ADLINK gibi NI-488.2 uyumlu
        # gpib-32.dll sağlayan adaptörler @py + gpib-ctypes ile çalışır.
        errors: List[str] = []
        self._inst = None
        for backend in ("", "@py"):
            try:
                rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
                self._inst = rm.open_resource(resource)
                self._rm = rm
                break
            except Exception as e:
                errors.append(f"{backend or 'varsayılan VISA'}: {e}")
        if self._inst is None:
            raise InstrumentError(
                f"GPIB kaynağı açılamadı ({resource}).\n" + "\n".join(errors) +
                "\nAdaptör sürücüsünün (ADLINK için ADL-GPIB) ve "
                "'pip install pyvisa pyvisa-py gpib-ctypes' paketlerinin "
                "kurulu olduğundan emin olun."
            )
        self._inst.timeout = int(timeout * 1000)  # ms
        self._inst.write_termination = "\n"
        self._inst.read_termination = "\n"
        self._cfg = None

    def write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def query(self, cmd: str, timeout: Optional[float] = None) -> str:
        if timeout is not None:
            old = self._inst.timeout
            self._inst.timeout = int(timeout * 1000)
            try:
                return self._inst.query(cmd).strip()
            finally:
                self._inst.timeout = old
        return self._inst.query(cmd).strip()

    def open_session(self) -> str:
        return self.query("*IDN?")

    def close(self) -> None:
        try:
            self.write(":OUTP OFF")
        except Exception:
            pass
        try:
            self._inst.close()
        except Exception:
            pass


class SimulatedKeithley2400(_Keithley2400Base):
    """Cihaz olmadan arayüzü denemek için simülatör.

    ~200 Ohm/sq'lik bir ince film gibi davranır (R = Rs / 4.5324) ve gerçekçi
    olması için küçük gürültü + termal EMF ofseti ekler.
    """

    SHEET_RESISTANCE = 200.0

    def __init__(self, *args, **kwargs):
        self._cfg = None
        self._level = 0.0
        self._r = self.SHEET_RESISTANCE / (math.pi / math.log(2.0))
        self._emf = 12e-6  # 12 uV termal EMF

    def open_session(self) -> str:
        return "KEITHLEY INSTRUMENTS INC., MODEL 2400 (SIMULASYON), 0, C30"

    def close(self) -> None:
        pass

    def configure(self, cfg: MeasurementConfig) -> None:
        self._cfg = cfg

    def output(self, on: bool) -> None:
        pass

    def set_level(self, level: float) -> None:
        self._level = level

    def read_point(self) -> Tuple[float, float, bool]:
        assert self._cfg is not None
        time.sleep(min(self._cfg.reading_time(), 0.4))
        noise = 1.0 + random.gauss(0.0, 5e-4)
        if self._cfg.source.function == "current":
            i = self._level
            v = i * self._r * noise + self._emf
            tripped = abs(v) >= self._cfg.source.compliance
            if tripped:
                v = math.copysign(self._cfg.source.compliance, v)
            return float(v), float(i), bool(tripped)
        v = self._level
        i = v / self._r * noise
        tripped = abs(i) >= self._cfg.source.compliance
        if tripped:
            i = math.copysign(self._cfg.source.compliance, i)
        return float(v), float(i), bool(tripped)


def list_serial_ports() -> List[Tuple[str, str]]:
    """(port, açıklama) listesi döndürür."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [(p.device, p.description or "") for p in list_ports.comports()]


def list_visa_resources() -> List[str]:
    """Bulunan VISA kaynakları (GPIB0::24::INSTR gibi). VISA yoksa boş liste.

    Hem sistem VISA'sı hem pyvisa-py (@py) yedeği taranır, sonuçlar birleşir.
    """
    found: List[str] = []
    try:
        import pyvisa
    except ImportError:
        return []
    for backend in ("", "@py"):
        try:
            rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
            for r in rm.list_resources():
                if r not in found:
                    found.append(r)
        except Exception:
            continue
    # GPIB kaynaklarını başa al (ASRL/TCPIP gibi diğerleri sonda kalsın)
    return sorted(found, key=lambda r: (not r.upper().startswith("GPIB"), r))
