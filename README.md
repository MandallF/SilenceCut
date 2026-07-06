# SilenceCut

![platform](https://img.shields.io/badge/platform-Windows%2010%2B-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Tek bir `.exe` dosyasına çift tıklayınca açılan, masaüstü Windows uygulaması olarak paketlenmiş bir video düzenleme aracı. Yüklediğiniz videodaki sessiz bölgeleri otomatik tespit edip keser, geri kalan parçaları aynı kalitede tek bir dosya olarak verir. Ses seviyesi, hız, çözünürlük ve renge dokunmaz.

```
silencecut/
├── app.py                # Masaüstü launcher (FastAPI + Edge --app)
├── silencecut.spec       # PyInstaller paketleme yapılandırması
├── build.bat             # Tek tuşla derleme betiği
├── backend/              # FastAPI + numpy + FFmpeg (ses analizi & kesme)
├── frontend/             # React (Vite) — Canvas timeline + VU metre
└── dist/SilenceCut.exe   # Derlemeden sonra üretilen tek dosyalı uygulama
```

---

## Son kullanıcı için

`SilenceCut.exe` dosyasına **çift tıklayın**. Uygulama küçük bir gömülü web sunucusu başlatır ve Microsoft Edge'i app-window modunda açar (tarayıcı çubuğu olmadan, tıpkı bir masaüstü uygulaması gibi). Pencereyi kapattığınızda her şey otomatik temizlenir.

### Gereksinimler

- Windows 10/11
- Microsoft Edge (Windows ile birlikte gelir) veya Google Chrome
- Python, Node.js, FFmpeg yüklemenize **gerek yoktur** — hepsi `.exe` içinde.

### Tipik akış

1. **Video yükle** — sol panele MP4 / MOV / AVI / MKV / WebM sürükleyin
2. **(Opsiyonel) Mikrofon kaydı** — WAV / MP3 / M4A / FLAC / OGG. Ofset ayarıyla videoyla hizalayın
3. **Önerilen eşik** — backend ses dağılımına bakıp otomatik bir eşik önerir; "Uygula"ya basın
4. **Sessizlikleri Tespit Et** — bulunan bölgeler sağ panelde listelenir
5. **Düzenle** — kart kart inceleyin, "Koru ✓" ile kesimden çıkarın, sınırları saniye bazlı düzenleyin
6. **Çıktı kalitesi seçin** — Hızlı / Dengeli / Yüksek Kalite
7. **Videoyu Kes ve İndir** — Windows'un native "Farklı Kaydet..." penceresinden konum seçin

---

## Özellikler

### İki sesli analiz (oyun + mikrofon)

Oyun videosu ve ayrı mikrofon kaydı yüklediğinizde sessizlik **her iki kanal birden sessizken** kesilir. Yani:
- Oyun yüksek + siz susmuş → kesilmez
- Oyun sessiz + siz konuşuyorsunuz → kesilmez
- İkisi de sessiz → kesilir

Çıktı videosunda iki ses `amix` ile birleştirilir.

### Otomatik eşik önerisi

Upload sonrası backend her kanalın RMS dağılımına bakıp:
- En sessiz %20'lik dilimin medyanını **gürültü tabanı** olarak alır
- En yüksek %20'lik dilimin medyanını **sinyal seviyesi** olarak alır
- Eşik = `noise_floor × 4` (gürültünün üstünde, sinyalin altında)
- Sinyal/gürültü oranına göre güvenilirlik (yüksek/orta/düşük) gösterir

### Çıktı kalite presetleri

| Preset | x264 | Hız | Boyut | Kalite |
|---|---|---|---|---|
| **Hızlı** | `ultrafast / crf 23` | Çok hızlı | Büyük | İyi |
| **Dengeli** (varsayılan) | `fast / crf 20` | Orta | Orta | Yüksek |
| **Yüksek Kalite** | `slow / crf 18` | Yavaş | Küçük | Görsel-olarak-kayıpsız |

Çözünürlük, FPS ve ses örnekleme korunur; yalnızca sıkıştırma değişir.

### Otomatik Türkçe Altyazı (.srt)

Sessizlikleri tespit ettikten sonra **"📝 Türkçe Altyazı Oluştur"** butonu, konuşmayı [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper `small`, int8, CPU) ile transkript eder ve bir `.srt` dosyası üretir:

- Zaman damgaları **kesilmiş timeline'a göre** yeniden hesaplanır — altyazılar export edilen videoyla/Premiere sequence'iyle senkron olur
- Mikrofon kaydı yüklüyse transkript ondan yapılır (temiz konuşma); yoksa video sesinden
- Kesilen sessizliğe taşan cümleler otomatik kırpılır; kesintiye yayılan cümleler bölünür
- `vad_filter` sayesinde uzun sessiz/müzikli bölümlerde halüsinasyon metin üretilmez

**İlk kullanımda** ~460 MB'lık Whisper modeli internetten indirilir ve `.exe`'nin yanındaki `models/` klasörüne kaydedilir — sonraki kullanımlar tamamen offline'dır.

**Premiere'da kullanım:** `File → Import` ile `.srt`'yi açın → timeline'a sürükleyin → caption track oluşur → stilini (font, renk, konum, arka plan) **Essential Graphics** panelinden topluca düzenleyin.

### Streaming yükleme

Yüklemeler `multipart/form-data` yerine raw body olarak gönderilir → multipart parsing'in spool-then-copy çift I/O maliyeti olmadan doğrudan diske yazılır. Çok-GB videolarda kayda değer fark yaratır.

### Native "Farklı Kaydet" diyalogu

Export sırasında `window.showSaveFilePicker` ile native Windows save diyalogu açılır → dosya doğrudan seçtiğiniz konuma stream edilir (bellek kullanmaz).

### Klavye kısayolları

`?` veya `H` ile uygulama içinden açılabilir.

| Kısayol | Eylem |
|---|---|
| `Space` | Oynat / duraklat |
| `← / →` | 5 sn geri / ileri (`Shift` ile 1 sn) |
| `J / L` | 10 sn geri / ileri |
| `K` | Duraklat |
| `Home / End` | Başa / sona git |
| `Enter` | Sessizlikleri tespit et |
| `Escape` | Bölge seçimini kaldır |
| `Delete / Backspace` | Seçili bölgeyi koru |
| `Ctrl + Z` / `Ctrl + Shift + Z` | Geri al / yinele |

---

## .exe'yi derlemek (geliştirici)

İlk seferde tek komut yeterli:

```bat
build.bat
```

Bu betik:
1. `.venv` Python sanal ortamı kurar
2. `backend/requirements.txt` + `pyinstaller` yükler
3. `frontend/`'i Vite ile derler
4. PyInstaller'ı `silencecut.spec` ile çalıştırır → `dist/SilenceCut.exe` (~58 MB)

### Manuel adımlar

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt pyinstaller

cd frontend
npm install
npm run build
cd ..

pyinstaller silencecut.spec --noconfirm
```

### Derleme gereksinimleri

- Python 3.11+ (3.14 ile test edildi)
- Node.js 18+
- FFmpeg gerekmez — `imageio-ffmpeg` paketi gömülü statik FFmpeg ikilisi sağlar

---

## Geliştirme modu (hot reload)

Frontend Vite dev sunucusu + backend ayrı uvicorn instance:

```bat
.venv\Scripts\activate
python -m uvicorn main:app --reload --port 8000 --app-dir backend
```

Ayrı bir terminalde:
```bat
cd frontend
npm run dev
```

`vite.config.js` `/api` çağrılarını `http://localhost:8000`'a proxy eder.

---

## Mimari

### Backend ([backend/main.py](backend/main.py))

| Endpoint | Amaç |
|---|---|
| `POST /api/upload-raw` | Streaming video yükleme (X-Filename header'ında dosya adı) |
| `POST /api/upload-mic-raw` | Streaming mikrofon yükleme |
| `DELETE /api/upload-mic/{file_id}` | Mikrofonu kaldır |
| `POST /api/suggest-threshold` | Otomatik eşik önerisi |
| `POST /api/analyze` | Sessizlik tespiti, waveform üretimi |
| `POST /api/export` | FFmpeg ile kes + birleştir, dosyayı stream et |
| `GET /api/export-progress/{file_id}` | Canlı encode ilerlemesi (yüzde, ETA, hız) |
| `POST /api/export-premiere-xml` | FCP7 XML üret (Premiere'a import için) |
| `POST /api/export-srt` | Whisper ile Türkçe altyazı üret (kesilmiş timeline'a göre) |
| `GET /api/srt-progress/{file_id}` | Canlı transkripsiyon ilerlemesi |
| `GET /api/srt-status` | Whisper modeli indirilmiş mi |
| `GET /api/encoders` | Donanım encoder tespiti (NVENC/QSV/AMF) |
| `DELETE /api/cleanup/{file_id}` | Geçici dosyaları sil |

- `analyzer.py`: FFmpeg ile 22050 Hz mono PCM, 50 ms RMS pencereleri, otomatik eşik önerisi
- `exporter.py`: `filter_complex` ile `trim/atrim + amix + concat`, 3 kalite preseti, encode timeout duration'a göre ölçekli
- Frozen modda `frontend/dist` aynı sunucudan `/` üzerinde servis edilir
- Tüm dosya yolları `__` separator ile isimlendirilir → `cleanup` prefix collision yapmaz
- Pydantic ile region/threshold/offset validasyonu

### Frontend ([frontend/src/App.jsx](frontend/src/App.jsx))

- 4 sütunlu layout: FilePanel · Video · VU metre · ConfirmPanel; alta timeline (video + ses + mic waveform)
- `useAudioMeter` — Web Audio AnalyserNode ile gerçek zamanlı RMS/peak
- `AudioLevelMeter` — Canvas dikey VU bar, dB skalası, eşik çizgisi
- `Timeline` — ResizeObserver + ~30 fps rAF, video track + iki bantlı ses (yeşil=video, mavi=mic)
- `useHistory` ile undo/redo
- `useKeyboard` ref pattern ile listener her render'da yeniden bağlanmıyor

### Launcher ([app.py](app.py))

- Boş TCP port → uvicorn daemon thread → Edge'i `--app=URL --user-data-dir=...` ile aç
- `psutil` ile profile token'ını içeren browser süreçlerini sayar
- Tüm browser süreçleri kapanınca temizlenip çıkar
- Loglar: `%LOCALAPPDATA%\SilenceCut\launcher.log`

---

## Güvenlik / kararlılık notları

- Tüm input dosya adları `_safe_filename` ile temizlenir (path traversal, NTFS reserved chars, max 120 char)
- Region başlangıç/bitiş Pydantic ile validate edilir (negatif/inverted reddedilir)
- FFmpeg subprocess'lerinde timeout var:
  - Decode: 30 dk sabit
  - Encode: video süresi × preset-bağlı katsayı (8x–30x), min 60 sn
- Geçici dosyalar `%LOCALAPPDATA%\SilenceCut\temp\` altında — `_MEIPASS` (read-only PyInstaller) içinde değil
- CORS yalnızca `localhost:5173` (dev modu) için açık

---

## Uygulamanın ilk aşamasında uzun videolarda videoyu dışarıya aktarırken çok uzun süreler bekleme süresi mevcut. Şimdilik geliştirmede odaklanacağım kısım bu sorunu çözmek olacak.
-Düzeltme: Artık dışa aktarırken ekran kartını da kullanarak hızı arttırdım.
-Düzeltme: Ekstra dışa aktarma seçeneği ekledim. XML formatında projeyi dışa aktarıyoruz ve bu sayede çeşitli video edit programlarında bu dosyayı açtığımızda otomatik sekans açıyor. Böylelikle oriinal videonun üzerinde sanki tüm bu değişiklikler yapılmış gibi hem düzenleme yapabiliyoruz hem de çok daha kısa sürede dışa aktarım yapabiliyoruz.
