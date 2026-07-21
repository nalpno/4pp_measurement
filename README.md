# 4PP Ölçüm — Keithley 2400 Dört Nokta Prob Arayüzü

Keithley 2400 SourceMeter + Lucas Labs S302-6 prob standı + Signatone SP4 prob kafası
ile **sheet resistance (tabaka direnci)** ölçümü için masaüstü arayüz.
Keithley KickStart I-V Characterizer'ın parametre düzeni örnek alınmıştır.
Windows, macOS ve Linux'ta çalışır.

## Özellikler

- **RS-232 (USB çevirici) bağlantısı**: port, baud, terminatör, akış kontrolü seçimi
- **KickStart benzeri parametreler**:
  - Kaynak: akım/gerilim, lineer–log–liste taraması veya sabit seviye, başlangıç/bitiş/nokta
    sayısı, çift yönlü tarama, compliance, kaynak aralığı, kaynak gecikmesi
  - Ölçüm: NPLC (hızlı/orta/yavaş/özel), ölçüm aralığı, auto zero, tekrar filtresi
    (ortalama), 4 telli ölçüm, ön/arka terminal
  - **Akım ters çevirme** (+I/−I): termal EMF ve ofset hatalarını giderir
- **Canlı I-V grafiği** + lineer fit (R, R², ofset)
- **Sheet resistance**: Rs = C × (V/I); sonsuz tabaka (C = π/ln2 = 4.5324),
  dairesel numune düzeltmesi veya manuel faktör
- **Özdirenç**: kalınlık girilirse ρ = Rs · t · K (kalın numune düzeltmesi K dahil)
- **CSV kaydı** (tüm parametreler başlıkta) + otomatik kayıt seçeneği
- **Simülasyon modu**: cihaz bağlı olmadan arayüzü deneme
- Son kullanılan tüm ayarlar otomatik hatırlanır

## Kurulum

1. [Python 3.10+](https://www.python.org/downloads/) kurun
   (Windows'ta kurulumda *"Add Python to PATH"* kutusunu işaretleyin).
2. USB-RS232 çeviricinin sürücüsünü kurun (FTDI, Prolific PL2303 veya CH340 —
   çeviricinin markasına göre; macOS'ta çoğu zaman sürücü gerekmez, port
   `/dev/tty.usbserial-…` olarak görünür).
3. Bu depoyu indirin ve bağımlılıkları kurun:

```bash
git clone <repo-adresi>
cd 4pp_measurement
pip install -r requirements.txt
```

## Çalıştırma

```bash
python run.py
```

## GPIB (USB-GPIB çevirici) ile bağlantı

Bilgisayara USB-RS232 yerine **USB-GPIB adaptörü** ile bağlanıyorsanız:

1. Adaptörün sürücüsünü kurun (markasına göre):
   - **ADLINK USB-3488A**: [ADLINK USB-3488A sayfasından](https://www.adlinktech.com/Products/GPIB_Modular_Instruments/GPIB/USB-3488A)
     **ADL-GPIB** sürücüsünü (3.0+) indirip kurun. ADLINK sürücüsü NI-488.2
     uyumlu `gpib-32.dll` sağlar; program buna `pyvisa-py + gpib-ctypes`
     üzerinden erişir (requirements.txt ile birlikte kurulur).
   - **National Instruments GPIB-USB-HS/HS+** veya **Keithley KUSB-488B**:
     [NI-488.2 sürücüsü](https://www.ni.com/en/support/downloads/drivers/download.ni-488-2.html)
     + [NI-VISA](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)
   - **Keysight/Agilent 82357A/B**:
     [Keysight IO Libraries Suite](https://www.keysight.com/find/iosuite) (VISA dahildir)
2. Cihazda `MENU → COMMUNICATION → GPIB` seçin; adres varsayılan **24**'tür.
3. Programda *Bağlantı türü: GPIB* seçin; kaynak listesinde
   `GPIB0::24::INSTR` görünmelidir → **Bağlan**. Program bağlanırken önce
   `gpib-32.dll`'i doğrudan (ADLINK için en güvenilir yol), olmazsa sistem
   VISA'sını/pyvisa-py yedeğini dener.

**"dev() error: Errno 22" veya "GPIB cihazı açılamadı" hatası:** DLL yüklendi
ama cihaz açılamadı demektir. Kontrol listesi:

- Cihaz açık mı, GPIB kablosu takılı mı, Keithley `MENU → COMMUNICATION → GPIB`
  ve adres **24** mü?
- ADLINK sürücüsüyle gelen **GPIB Utility / Interactive Control** ile cihazı
  taratın; adres 24'te Keithley görünüyorsa donanım sağlamdır.
- Adaptör *GPIB0* değil başka bir kart numarasına atanmış olabilir; kaynak
  kutusuna elle `GPIB1::24::INSTR` yazıp deneyin (kutu düzenlenebilir).

Aygıt Yöneticisi'nde adaptör sarı ünlemle "Diğer aygıtlar" altında
görünüyorsa sürücü kurulmamış demektir (1. adım).

## Keithley 2400 ön panel ayarı (bir kez)

Cihazın RS-232 ayarları programdaki bağlantı ayarlarıyla aynı olmalıdır:

`MENU → COMMUNICATION → RS-232` altında:

| Ayar | Önerilen değer |
|---|---|
| BAUD | 9600 |
| BITS | 8 |
| PARITY | NONE |
| TERMINATOR | `<CR+LF>` (programda "CRLF") |
| FLOW-CTRL | NONE |

Ayar değiştirildiyse cihazı kapatıp açın. GPIB değil **RS-232** seçili olmalıdır.

## Kullanım

1. **Bağlantı**: seri portu seçin → **Bağlan**. Cihaz kimliği (IDN) görünmelidir.
   Cihaz yoksa **Simülasyon modu**nu işaretleyip arayüzü deneyebilirsiniz.
2. **Kaynak ayarları**: 4PP için önerilen mod *akım kaynağı + lineer tarama*.
   Akım aralığını numunenize göre seçin (ince filmler için tipik 10 µA – 1 mA;
   ısınmayı önlemek için V < ~10 mV kalacak akımlar idealdir).
   *Compliance* değeri cihazın uygulayacağı gerilimi sınırlar.
3. **Ölçüm ayarları**: gürültülü ortamda NPLC'yi *Yavaş (10)* yapın ve/veya
   tekrar filtresini açın. **4 telli ölçüm mutlaka açık olmalıdır.**
   S302-6 standının bağlı olduğu terminali (ön/arka) seçin.
4. **Numune**: SP4 kafanızın uç aralığını girin (modeline bakın: 40 mil = 1.016 mm,
   62.5 mil = 1.5875 mm — yalnızca küçük numune ve kalınlık düzeltmelerinde kullanılır).
   Film kalınlığını girerseniz özdirenç de hesaplanır.
5. **▶ Ölçümü Başlat**: grafik canlı çizilir; bitince R, R², Rs, ρ görünür.
6. **CSV olarak kaydet** ile veriyi alın (grafiğe sağ tıklayıp *Export* ile
   PNG de alınabilir).

## Hesaplamalar

- I-V noktalarına doğrusal fit: `V = R·I + V₀` → eğim **R**, `V₀` termal ofset.
- Sheet resistance: `Rs = C · R`
  - Sonsuz ince tabaka: `C = π/ln2 = 4.5324`
  - Dairesel numune (çap D, uç aralığı s):
    `C = π / ( ln2 + ln[((D/s)² + 3)/((D/s)² − 3)] )`
  - Dikdörtgen/özel numuneler: Smits (1958) tablolarından faktörü bulup
    *Manuel düzeltme faktörü* olarak girin.
- Kalınlık düzeltmesi: `K = ln2 / ln( sinh(t/s) / sinh(t/2s) )` (t ≪ s için K ≈ 1)
- Özdirenç: `ρ = Rs · t · K` (Ω·cm), iletkenlik `σ = 1/ρ`.
- R² < 0.999 ise program uyarır: kontak sorunu, compliance sınırı veya
  ohmik olmayan davranış işaretidir.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| "Seri port bulunamadı" | Çevirici sürücüsünü kurun; kabloyu değiştirip **Yenile**'ye basın |
| "Cihazdan yanıt alınamadı" | Baud/terminatör ayarlarını cihazla eşleştirin; cihazda RS-232 seçili mi bakın |
| Compliance uyarısı | Akımı düşürün veya compliance gerilimini yükseltin; kontakları kontrol edin |
| Negatif/saçma Rs | Prob kontaklarını ve 4 telli bağlantıyı kontrol edin |

## Tek dosya uygulama olarak paketleme (isteğe bağlı)

Başka bilgisayarlarda Python kurmadan çalıştırmak için:

```bash
pip install pyinstaller
pyinstaller --windowed --onefile --name 4pp-olcum run.py
```

Çıktı `dist/` klasöründedir (Windows'ta `.exe`, macOS'ta `.app`).
Paket, oluşturulduğu işletim sistemi için çalışır (Mac paketi Mac'te, exe Windows'ta).
