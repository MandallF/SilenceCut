import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FilePanel from './components/FilePanel.jsx';
import VideoPlayer from './components/VideoPlayer.jsx';
import Timeline from './components/Timeline.jsx';
import ConfirmPanel from './components/ConfirmPanel.jsx';
import AudioLevelMeter from './components/AudioLevelMeter.jsx';
import KeyboardShortcuts from './components/KeyboardShortcuts.jsx';
import ToastContainer from './components/Toast.jsx';
import { useAudioMeter } from './hooks/useAudioMeter.js';
import { useKeyboard } from './hooks/useKeyboard.js';
import { useHistory } from './hooks/useHistory.js';
import { fmt, fmtSize } from './utils/format.js';

const LARGE_FILE_BYTES = 500 * 1024 * 1024;

export default function App() {
  const videoRef = useRef(null);

  const [file, setFile] = useState(null);          // { file_id, filename, size }
  const [videoUrl, setVideoUrl] = useState(null);  // local objectURL for preview

  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadFinalizing, setUploadFinalizing] = useState(false);

  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState(null);  // { duration, silent_regions, waveform }

  const [threshold, setThreshold] = useState(0.015);
  const [minSilence, setMinSilence] = useState(0.4);

  const [mic, setMic] = useState(null);              // { filename, size }
  const [micUploading, setMicUploading] = useState(false);
  const [micUploadProgress, setMicUploadProgress] = useState(0);
  const [micUploadFinalizing, setMicUploadFinalizing] = useState(false);

  const [suggestion, setSuggestion] = useState(null);  // { video: {...}, mic: {...} | null }
  const [suggesting, setSuggesting] = useState(false);
  const [micThreshold, setMicThreshold] = useState(0.015);
  const [micOffset, setMicOffset] = useState(0.0);

  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);

  const [selectedId, setSelectedId] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportEta, setExportEta] = useState(null); // seconds remaining, or null
  const [exportSpeed, setExportSpeed] = useState(0); // FFmpeg "speed" multiplier (e.g. 0.3x)
  const [exportPhase, setExportPhase] = useState('idle'); // 'idle'|'starting'|'encoding'|'finalizing'|'streaming'
  const [quality, setQuality] = useState('balanced');

  const [srtGenerating, setSrtGenerating] = useState(false);
  const [srtProgress, setSrtProgress] = useState(0);
  const [srtPhase, setSrtPhase] = useState('idle'); // 'idle'|'starting'|'downloading'|'loading'|'decoding'|'transcribing'

  const [toasts, setToasts] = useState([]);
  const [helpOpen, setHelpOpen] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  // Hardware encoder info from /api/encoders, populated on mount.
  const [hwEncoder, setHwEncoder] = useState(null);       // 'h264_amf' | 'h264_nvenc' | etc | null
  const [hwEncoderLabel, setHwEncoderLabel] = useState(null);  // user-facing label

  const history = useHistory([]);
  const editable = history.current;

  const pushToast = useCallback((message, kind = 'info', ms = 3000) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message, kind }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, ms);
  }, []);

  // Probe hardware encoders once on mount. The backend caches the result so
  // subsequent requests are instant.
  useEffect(() => {
    let cancelled = false;
    fetch('/api/encoders').then(async (r) => {
      if (!r.ok) return;
      const data = await r.json();
      if (cancelled) return;
      if (data.hw_encoder) {
        setHwEncoder(data.hw_encoder);
        setHwEncoderLabel(data.hw_label);
      }
    }).catch(() => { /* probe is best-effort */ });
    return () => { cancelled = true; };
  }, []);

  /* ----- Backend reconnect ----- */
  const handleReconnect = useCallback(async () => {
    try {
      const resp = await fetch('/api/health', { signal: AbortSignal.timeout(4000) });
      if (resp.ok) {
        setBackendDown(false);
        pushToast('Sunucuya yeniden bağlanıldı', 'success');
      } else {
        pushToast('Sunucu henüz hazır değil, tekrar deneyin', 'warning');
      }
    } catch {
      pushToast('Sunucuya bağlanılamıyor — uygulamayı kapatıp yeniden açın', 'danger');
    }
  }, [pushToast]);

  /* ----- Threshold suggestion ----- */
  const requestSuggestion = useCallback(async (fileId) => {
    if (!fileId) return;
    setSuggesting(true);
    try {
      const resp = await fetch('/api/suggest-threshold', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: fileId }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setSuggestion(data);
      }
    } catch { /* ignore */ }
    finally { setSuggesting(false); }
  }, []);

  const dismissSuggestion = useCallback(() => setSuggestion(null), []);

  /* ----- Upload ----- */
  const handleUpload = useCallback((rawFile) => {
    if (rawFile.size > LARGE_FILE_BYTES) {
      pushToast(`Büyük dosya (${fmtSize(rawFile.size)}) — işleme devam ediliyor`, 'warning', 4000);
    }
    const url = URL.createObjectURL(rawFile);
    setVideoUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return url;
    });
    // If the user is replacing an already-uploaded video, clean up the
    // previous file_id's temp files so we don't leak GBs of disk space
    // across a long editing session.
    if (file?.file_id) {
      fetch(`/api/cleanup/${file.file_id}`, { method: 'DELETE' }).catch(() => { /* best-effort */ });
    }
    setFile(null);
    setAnalysis(null);
    history.reset([]);
    setSelectedId(null);
    setMic(null);
    setSuggestion(null);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload-raw');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(rawFile.name));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.timeout = 0;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = (e.loaded / e.total) * 100;
        setUploadProgress(pct);
        if (pct >= 100) setUploadFinalizing(true);
      }
    };
    xhr.upload.onload = () => {
      // Bytes sent — server is still writing & responding.
      setUploadProgress(100);
      setUploadFinalizing(true);
    };
    xhr.onload = () => {
      setUploading(false);
      setUploadFinalizing(false);
      setUploadProgress(0);
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          // Server returns the URL-encoded filename; decode it for display.
          try { data.filename = decodeURIComponent(data.filename); } catch { /* ignore */ }
          setFile(data);
          setBackendDown(false);
          pushToast(`Yüklendi: ${data.filename}`, 'success');
          requestSuggestion(data.file_id);
        } catch {
          pushToast('Sunucu yanıtı çözümlenemedi', 'danger');
        }
      } else {
        // Try to extract the server's error detail for a meaningful message.
        let detail = 'Yükleme başarısız';
        try {
          const err = JSON.parse(xhr.responseText);
          if (err.detail) detail = err.detail;
        } catch { /* ignore */ }
        if (xhr.status === 507) {
          pushToast(`💾 Disk dolu — ${detail}`, 'danger', 8000);
        } else {
          pushToast(detail, 'danger', 6000);
        }
      }
    };
    xhr.onerror = () => {
      setUploading(false);
      setUploadFinalizing(false);
      setUploadProgress(0);
      setBackendDown(true);
      pushToast('Sunucuyla bağlantı kesildi', 'danger', 6000);
    };
    xhr.onabort = () => {
      setUploading(false);
      setUploadFinalizing(false);
      setUploadProgress(0);
    };
    setUploading(true);
    setUploadFinalizing(false);
    setUploadProgress(0);
    xhr.send(rawFile);
  }, [file, history, pushToast, requestSuggestion]);

  const applySuggestion = useCallback(() => {
    if (!suggestion) return;
    // Build a "what changed" summary so the user can see the new values for
    // BOTH channels (a previous version only flashed a generic toast and the
    // video threshold change was easy to miss when the new value happened
    // to land near the existing slider position).
    const parts = [];
    if (suggestion.video?.threshold) {
      const oldV = Math.round(threshold * 1000);
      const newV = Math.round(suggestion.video.threshold * 1000);
      setThreshold(suggestion.video.threshold);
      parts.push(`Video: ${oldV} → ${newV}`);
    }
    if (suggestion.mic?.threshold) {
      const oldM = Math.round(micThreshold * 1000);
      const newM = Math.round(suggestion.mic.threshold * 1000);
      setMicThreshold(suggestion.mic.threshold);
      parts.push(`Mikrofon: ${oldM} → ${newM}`);
    }
    setSuggestion(null);
    pushToast(
      parts.length > 0 ? `Eşikler uygulandı — ${parts.join(' · ')}` : 'Önerilen eşikler uygulandı',
      'success',
      4000,
    );
  }, [suggestion, threshold, micThreshold, pushToast]);

  /* ----- Mic upload / remove ----- */
  const handleUploadMic = useCallback((rawFile) => {
    if (!file) {
      pushToast('Önce video yükleyin', 'warning');
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload-mic-raw');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(rawFile.name));
    xhr.setRequestHeader('X-File-Id', file.file_id);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.timeout = 0;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = (e.loaded / e.total) * 100;
        setMicUploadProgress(pct);
        if (pct >= 100) setMicUploadFinalizing(true);
      }
    };
    xhr.upload.onload = () => {
      setMicUploadProgress(100);
      setMicUploadFinalizing(true);
    };
    xhr.onload = () => {
      setMicUploading(false);
      setMicUploadFinalizing(false);
      setMicUploadProgress(0);
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          try { data.filename = decodeURIComponent(data.filename); } catch { /* ignore */ }
          setMic({ filename: data.filename, size: data.size });
          pushToast(`Mikrofon yüklendi: ${data.filename}`, 'success');
          requestSuggestion(file.file_id);
        } catch {
          pushToast('Sunucu yanıtı çözümlenemedi', 'danger');
        }
      } else {
        let detail = 'Mikrofon yükleme başarısız';
        try {
          const err = JSON.parse(xhr.responseText);
          if (err.detail) detail = err.detail;
        } catch { /* ignore */ }
        if (xhr.status === 507) {
          pushToast(`💾 Disk dolu — ${detail}`, 'danger', 8000);
        } else {
          pushToast(detail, 'danger', 6000);
        }
      }
    };
    xhr.onerror = () => {
      setMicUploading(false);
      setMicUploadFinalizing(false);
      setMicUploadProgress(0);
      // Match the video upload's behaviour so the "Yeniden Bağlan" banner
      // surfaces here too — otherwise the user gets a fleeting toast and
      // no obvious recovery path.
      setBackendDown(true);
      pushToast('Sunucuyla bağlantı kesildi', 'danger', 6000);
    };
    xhr.onabort = () => {
      setMicUploading(false);
      setMicUploadFinalizing(false);
      setMicUploadProgress(0);
    };
    setMicUploading(true);
    setMicUploadFinalizing(false);
    setMicUploadProgress(0);
    xhr.send(rawFile);
  }, [file, pushToast, requestSuggestion]);

  const handleRemoveMic = useCallback(async () => {
    if (!file) return;
    setMic(null);
    try {
      await fetch(`/api/upload-mic/${file.file_id}`, { method: 'DELETE' });
    } catch { /* ignore */ }
    pushToast('Mikrofon kaldırıldı', 'info', 1500);
  }, [file, pushToast]);

  /* ----- Analyze ----- */
  const handleAnalyze = useCallback(async () => {
    if (!file) {
      pushToast('Önce video yükleyin', 'warning');
      return;
    }
    setAnalyzing(true);
    try {
      const resp = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: file.file_id,
          threshold,
          min_silence: minSilence,
          mic_threshold: mic ? micThreshold : null,
          mic_offset: mic ? micOffset : 0.0,
        }),
      });
      if (!resp.ok) {
        const t = await resp.text();
        // eslint-disable-next-line no-console
        console.error('Analyze failed:', t);
        pushToast('Analiz başarısız', 'danger');
        return;
      }
      const data = await resp.json();
      setAnalysis(data);
      history.reset(data.silent_regions);
      setSelectedId(null);
      setBackendDown(false);
      if (data.silent_regions.length === 0) {
        pushToast('Sessiz bölge bulunamadı — Eşiği düşürmeyi deneyin', 'warning', 4000);
      } else {
        pushToast(`${data.silent_regions.length} bölge bulundu`, 'success');
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      setBackendDown(true);
      pushToast('Backend\'e bağlanılamadı', 'danger');
    } finally {
      setAnalyzing(false);
    }
  }, [file, threshold, minSilence, mic, micThreshold, micOffset, history, pushToast]);

  /* ----- Editable region ops ----- */
  const handleSelect = useCallback((r) => {
    setSelectedId(r.id);
    if (videoRef.current) videoRef.current.currentTime = r.start;
  }, []);

  const handleKeep = useCallback((r) => {
    history.push(editable.filter((x) => x.id !== r.id));
    if (selectedId === r.id) setSelectedId(null);
    pushToast('Bölge korundu', 'success', 1500);
  }, [editable, history, pushToast, selectedId]);

  const handleEdit = useCallback((id, patch) => {
    history.push(editable.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }, [editable, history]);

  /* ----- Premiere Pro XML export ----- */
  const handleExportPremiere = useCallback(async () => {
    if (!file || !analysis) return;
    if (editable.length === 0) {
      pushToast('Kesilecek bölge yok', 'warning');
      return;
    }

    const stem = file.filename.replace(/\.[^.]+$/, '');
    const suggestedName = `${stem}_silencecut.xml`;

    // Pick where to save the XML before hitting the backend.
    let fileHandle = null;
    if (typeof window.showSaveFilePicker === 'function') {
      try {
        fileHandle = await window.showSaveFilePicker({
          suggestedName,
          types: [{ description: 'Premiere XML', accept: { 'application/xml': ['.xml'] } }],
        });
      } catch (err) {
        if (err && err.name === 'AbortError') return;
        fileHandle = null;
      }
    }

    try {
      const resp = await fetch('/api/export-premiere-xml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: file.file_id,
          regions: editable,
          duration: analysis.duration,
          mic_offset: mic ? micOffset : 0.0,
          use_mic: !!mic,
        }),
      });
      if (!resp.ok) {
        let detail = 'XML üretimi başarısız';
        try { const e = await resp.json(); if (e.detail) detail = e.detail; } catch { /* ignore */ }
        pushToast(detail, 'danger', 8000);
        return;
      }
      const blob = await resp.blob();
      if (fileHandle) {
        const writable = await fileHandle.createWritable();
        await writable.write(blob);
        await writable.close();
        pushToast(`XML kaydedildi: ${fileHandle.name || suggestedName}`, 'success', 6000);
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = suggestedName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        pushToast('İndirilenler klasörüne kaydedildi', 'success', 4000);
      }
      pushToast(
        '⚠ Premiere\'de import etmeden önce SilenceCut\'ı kapatmayın — XML temp klasöründeki dosyalara referans veriyor.',
        'info', 9000,
      );
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      pushToast('XML indirilemedi', 'danger');
    }
  }, [file, analysis, editable, mic, micOffset, pushToast]);

  /* ----- Turkish subtitle (SRT) generation ----- */
  const handleExportSrt = useCallback(async () => {
    if (!file || !analysis) return;
    if (editable.length === 0) {
      pushToast('Kesilecek bölge yok — önce sessizlikleri tespit edin', 'warning');
      return;
    }

    // One-time model download warning. srt-status is cheap; if it fails we
    // just skip the warning and let the backend handle it.
    try {
      const st = await fetch('/api/srt-status?model_size=small');
      if (st.ok) {
        const info = await st.json();
        if (!info.model_downloaded) {
          const ok = window.confirm(
            'İlk altyazı üretiminde Whisper konuşma tanıma modeli indirilecek ' +
            '(~460 MB, tek seferlik — internet bağlantısı gerekir).\n\n' +
            `Model şuraya kaydedilecek:\n${info.model_dir}\n\n` +
            'Devam edilsin mi?'
          );
          if (!ok) return;
        }
      }
    } catch { /* offline check is best-effort */ }

    const stem = file.filename.replace(/\.[^.]+$/, '');
    const suggestedName = `${stem}_silencecut.srt`;

    let fileHandle = null;
    if (typeof window.showSaveFilePicker === 'function') {
      try {
        fileHandle = await window.showSaveFilePicker({
          suggestedName,
          types: [{ description: 'SRT altyazı', accept: { 'application/x-subrip': ['.srt'] } }],
        });
      } catch (err) {
        if (err && err.name === 'AbortError') return;
        fileHandle = null;
      }
    }

    setSrtGenerating(true);
    setSrtProgress(0);
    setSrtPhase('starting');

    const fileId = file.file_id;
    const progressTimer = setInterval(async () => {
      try {
        const r = await fetch(`/api/srt-progress/${fileId}`);
        if (!r.ok) return;
        const p = await r.json();
        if (p.active) {
          if (typeof p.percent === 'number') setSrtProgress(p.percent);
          if (p.phase) setSrtPhase(p.phase);
        }
      } catch { /* keep last value on network blip */ }
    }, 1200);

    try {
      const resp = await fetch('/api/export-srt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: fileId,
          regions: editable,
          duration: analysis.duration,
          mic_offset: mic ? micOffset : 0.0,
          use_mic: !!mic,
          model_size: 'small',
        }),
      });
      if (!resp.ok) {
        let detail = 'Altyazı üretimi başarısız';
        try { const e = await resp.json(); if (e.detail) detail = e.detail; } catch { /* ignore */ }
        pushToast(detail, 'danger', 10000);
        return;
      }
      const segmentCount = resp.headers.get('X-Segment-Count');
      const blob = await resp.blob();
      if (fileHandle) {
        const writable = await fileHandle.createWritable();
        await writable.write(blob);
        await writable.close();
        pushToast(`Altyazı kaydedildi: ${fileHandle.name || suggestedName}`, 'success', 5000);
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = suggestedName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        pushToast('İndirilenler klasörüne kaydedildi', 'success', 4000);
      }
      pushToast(
        `📝 ${segmentCount || '?'} altyazı satırı üretildi. Premiere'da File → Import ile açın; ` +
        'stilini Essential Graphics panelinden topluca düzenleyebilirsiniz.',
        'info', 9000,
      );
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      pushToast('Altyazı indirilemedi', 'danger');
    } finally {
      clearInterval(progressTimer);
      setSrtGenerating(false);
      setSrtProgress(0);
      setSrtPhase('idle');
    }
  }, [file, analysis, editable, mic, micOffset, pushToast]);

  const handleReset = useCallback(() => {
    if (analysis) {
      history.reset(analysis.silent_regions);
      setSelectedId(null);
      pushToast('Orijinale sıfırlandı', 'info', 1500);
    }
  }, [analysis, history, pushToast]);

  /* ----- Export ----- */
  const handleExport = useCallback(async () => {
    if (!file || !analysis) return;
    if (editable.length === 0) {
      pushToast('Kesilecek bölge yok', 'warning');
      return;
    }

    // Long-video warning for the slowest preset. The "high" preset uses x264
    // `slow` which can take many hours on multi-hour 1080p source. Give the
    // user a chance to drop down to "balanced" before committing.
    const keptSeconds = editable.reduce((acc, r) => {
      // editable contains silent regions to remove; total kept = duration - sum(silent)
      return acc + Math.max(0, r.end - r.start);
    }, 0);
    const totalSilent = keptSeconds;
    const totalKeep = Math.max(1, analysis.duration - totalSilent);
    // Local copy we can mutate if the user opts into the GPU suggestion below
    // — the closure-captured `quality` state won't update fast enough.
    let effectiveQuality = quality;

    // Long-video warnings — pick the most relevant one based on what's selected.
    if (quality === 'high' && totalKeep > 30 * 60) {
      const minH = (totalKeep / 60 / 60 / 0.3).toFixed(1);
      const maxH = (totalKeep / 60 / 60 / 0.1).toFixed(1);
      const gpuTip = hwEncoder
        ? `\n\n💡 GPU encode (${hwEncoderLabel}) bu sürenin ` +
          `1/10'undan kısa sürer ve kalite farkı oyun kayıtları için ` +
          `pratikte hissedilmez.`
        : '';
      const ok = window.confirm(
        `⚠ Uzun video uyarısı\n\n` +
        `Yüksek Kalite preset'i bu uzunlukta bir videoda ` +
        `tahminen ${minH}–${maxH} saat sürer (CPU'nuza göre).\n\n` +
        `"Dengeli" preset'i 5-10 kat daha hızlıdır ve görsel fark ` +
        `yok denecek kadar azdır.${gpuTip}\n\n` +
        `Yine de Yüksek Kalite ile devam etmek istiyor musunuz?`
      );
      if (!ok) return;
    } else if (
      (quality === 'balanced' || quality === 'fast') &&
      hwEncoder &&
      totalKeep > 60 * 60
    ) {
      // Software encode of a 1+ hour kept-content video on CPU is hours of
      // work. Let the user know one click can cut it down dramatically.
      const ok = window.confirm(
        `💡 GPU encode önerisi\n\n` +
        `1 saatten uzun bu videoyu CPU ile işlemek saatler alabilir. ` +
        `Bilgisayarınızda ${hwEncoderLabel} mevcut — onu kullanırsanız ` +
        `tahminen 5-30× daha hızlı biter.\n\n` +
        `[Tamam] = ${hwEncoderLabel} ile devam et\n` +
        `[İptal] = Mevcut CPU preset'i ile devam et`
      );
      if (ok) {
        effectiveQuality = 'gpu';
        setQuality('gpu');  // also update the dropdown so the user sees it
      }
    }

    const stem = file.filename.replace(/\.[^.]+$/, '');
    const suggestedName = `${stem}_silencecut.mp4`;

    // Step 1: Ask the user where to save BEFORE running the export.
    // If they cancel, we never start the long FFmpeg job.
    let fileHandle = null;
    const supportsPicker = typeof window.showSaveFilePicker === 'function';
    if (supportsPicker) {
      try {
        fileHandle = await window.showSaveFilePicker({
          suggestedName,
          types: [{
            description: 'MP4 video',
            accept: { 'video/mp4': ['.mp4'] },
          }],
        });
      } catch (err) {
        // User cancelled the dialog — abort silently.
        if (err && err.name === 'AbortError') return;
        // Some other error — fall back to download flow below.
        // eslint-disable-next-line no-console
        console.warn('save picker failed, falling back to download:', err);
        fileHandle = null;
      }
    }

    setExporting(true);
    setExportProgress(0);
    setExportEta(null);
    setExportSpeed(0);
    setExportPhase('starting');

    // Poll backend for real FFmpeg progress every 1.2s.
    const fileId = file.file_id;
    let progressTimer = setInterval(async () => {
      try {
        const r = await fetch(`/api/export-progress/${fileId}`);
        if (!r.ok) return;
        const p = await r.json();
        if (p.active) {
          if (typeof p.percent === 'number') setExportProgress(p.percent);
          if (typeof p.eta_seconds === 'number') setExportEta(p.eta_seconds);
          if (typeof p.speed === 'number') setExportSpeed(p.speed);
          if (p.phase) setExportPhase(p.phase);
        }
      } catch { /* network blip — keep last value */ }
    }, 1200);

    try {
      const resp = await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: file.file_id,
          regions: editable,
          duration: analysis.duration,
          mic_offset: mic ? micOffset : 0.0,
          video_gain_db: 0.0,
          mic_gain_db: 0.0,
          quality: effectiveQuality,
          // Explicitly tell the backend whether to mix the mic. Otherwise it
          // would mix any mic file still on disk even if the user removed it.
          use_mic: !!mic,
        }),
      });
      if (!resp.ok) {
        let detail = 'Export başarısız';
        try {
          const err = await resp.json();
          if (err.detail) detail = err.detail;
        } catch { /* response wasn't JSON */ }
        pushToast(detail, 'danger', 10000);
        return;
      }

      // Encode finished — switch UI to "downloading" phase. Stop polling
      // progress (the response is on the way and the backend cleared its state).
      clearInterval(progressTimer);
      progressTimer = null;
      setExportPhase('streaming');
      setExportProgress(99.5);
      setExportEta(null);

      if (fileHandle) {
        // Stream the response directly to the chosen file (no big in-memory blob).
        const writable = await fileHandle.createWritable();
        try {
          if (resp.body && typeof resp.body.pipeTo === 'function') {
            await resp.body.pipeTo(writable);
          } else {
            const blob = await resp.blob();
            await writable.write(blob);
            await writable.close();
          }
        } catch (err) {
          try { await writable.abort(); } catch { /* ignore */ }
          throw err;
        }
        setExportProgress(100);
        const savedName = fileHandle.name || suggestedName;
        pushToast(`Kaydedildi: ${savedName}`, 'success', 4000);
      } else {
        // Fallback: trigger a regular browser download (goes to Downloads folder).
        const blob = await resp.blob();
        setExportProgress(100);
        const dlUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = dlUrl;
        a.download = suggestedName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(dlUrl);
        pushToast('İndirilenler klasörüne kaydedildi', 'success', 4000);
      }

      try {
        await fetch(`/api/cleanup/${file.file_id}`, { method: 'DELETE' });
      } catch { /* ignore */ }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
      pushToast('Export hatası', 'danger');
    } finally {
      if (progressTimer) clearInterval(progressTimer);
      setExporting(false);
      setExportProgress(0);
      setExportEta(null);
      setExportSpeed(0);
      setExportPhase('idle');
    }
  }, [file, analysis, editable, mic, micOffset, quality, pushToast, hwEncoder, hwEncoderLabel, setQuality]);

  /* ----- Audio meter ----- */
  const audio = useAudioMeter(videoRef, !!videoUrl);

  /* ----- Keyboard ----- */
  const seekBy = useCallback((delta) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + delta));
  }, []);
  const seekTo = useCallback((t) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(v.duration || 0, t));
  }, []);
  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }, []);
  const pauseVideo = useCallback(() => { videoRef.current?.pause(); }, []);

  const keepSelected = useCallback(() => {
    if (!selectedId) return;
    const r = editable.find((x) => x.id === selectedId);
    if (r) handleKeep(r);
  }, [selectedId, editable, handleKeep]);

  useKeyboard({
    togglePlay,
    pause: pauseVideo,
    seek: seekBy,
    seekTo,
    seekToEnd: () => seekTo(duration),
    analyze: handleAnalyze,
    clearSelection: () => setSelectedId(null),
    keepSelected,
    undo: history.undo,
    redo: history.redo,
    toggleHelp: () => setHelpOpen((o) => !o),
  });

  useEffect(() => {
    const onEsc = (e) => { if (e.key === 'Escape') setHelpOpen(false); };
    if (helpOpen) {
      window.addEventListener('keydown', onEsc);
      return () => window.removeEventListener('keydown', onEsc);
    }
    return undefined;
  }, [helpOpen]);

  // Reflect the loaded video's name in the window title.
  useEffect(() => {
    document.title = file ? `${file.filename} — SilenceCut` : 'SilenceCut';
  }, [file]);

  // Free the preview blob URL when the app unmounts.
  useEffect(() => () => {
    if (videoUrl) URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  /* ----- Derived ----- */
  const totalRemoved = useMemo(
    () => editable.reduce((sum, r) => sum + (r.end - r.start), 0),
    [editable]
  );
  const realDuration = analysis?.duration ?? duration;

  return (
    <div className="app">
      <header className="app-header">
        <div className="logo">Silence<span>Cut</span></div>
        <div className="file-meta">
          {file ? `${file.filename} · ${fmtSize(file.size)}` : 'Dosya yüklenmedi'}
        </div>
        <div style={{ color: '#8b949e', fontSize: 12 }}>
          {realDuration > 0 ? `Süre: ${fmt(realDuration)}` : ''}
        </div>
        <button
          className="btn-secondary"
          style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid #30363d', background: 'transparent' }}
          onClick={() => setHelpOpen(true)}
          title="Klavye kısayolları (?)"
        >
          ?
        </button>
      </header>

      {backendDown && (
        <div className="banner danger" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span>⚠ Sunucuyla bağlantı kesildi.</span>
          <button
            onClick={handleReconnect}
            style={{
              padding: '3px 10px', borderRadius: 4, border: '1px solid #f87171',
              background: 'transparent', color: '#f87171', cursor: 'pointer',
              fontSize: 12, fontWeight: 600, flexShrink: 0,
            }}
          >
            Yeniden Bağlan
          </button>
          <span style={{ fontSize: 11, color: '#fca5a5' }}>
            ya da uygulamayı kapatıp tekrar açın.
          </span>
        </div>
      )}

      <div className="app-main">
        <div className="panel">
          <FilePanel
            file={file}
            uploadProgress={uploadProgress}
            uploading={uploading}
            uploadFinalizing={uploadFinalizing}
            analyzing={analyzing}
            threshold={threshold}
            setThreshold={setThreshold}
            minSilence={minSilence}
            setMinSilence={setMinSilence}
            onUpload={handleUpload}
            onAnalyze={handleAnalyze}
            hasAnalysis={!!analysis}
            mic={mic}
            micUploading={micUploading}
            micUploadProgress={micUploadProgress}
            micUploadFinalizing={micUploadFinalizing}
            suggestion={suggestion}
            suggesting={suggesting}
            onApplySuggestion={applySuggestion}
            onDismissSuggestion={dismissSuggestion}
            micThreshold={micThreshold}
            setMicThreshold={setMicThreshold}
            micOffset={micOffset}
            setMicOffset={setMicOffset}
            onUploadMic={handleUploadMic}
            onRemoveMic={handleRemoveMic}
          />
        </div>

        <VideoPlayer
          ref={videoRef}
          src={videoUrl}
          regionsCount={editable.length}
          savedSeconds={totalRemoved}
          duration={realDuration}
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)}
        />

        <AudioLevelMeter
          rmsDb={audio.rmsDb}
          peakDb={audio.peakDb}
          threshold={threshold}
          active={audio.active && !!videoUrl && audio.rms > 0.0001}
        />

        <div className="panel-right">
          {analysis ? (
            <ConfirmPanel
              regions={editable}
              duration={realDuration}
              selectedId={selectedId}
              onSelect={handleSelect}
              onKeep={handleKeep}
              onEdit={handleEdit}
              onExport={handleExport}
              onExportPremiere={handleExportPremiere}
              onExportSrt={handleExportSrt}
              onReset={handleReset}
              exporting={exporting}
              exportProgress={exportProgress}
              exportEta={exportEta}
              exportSpeed={exportSpeed}
              exportPhase={exportPhase}
              srtGenerating={srtGenerating}
              srtProgress={srtProgress}
              srtPhase={srtPhase}
              quality={quality}
              setQuality={setQuality}
              hwEncoderLabel={hwEncoderLabel}
            />
          ) : (
            <div style={{ padding: 24, color: '#484f58', textAlign: 'center', fontSize: 12 }}>
              Sessizlikleri tespit ettikten sonra onay paneli burada görünecek.
            </div>
          )}
        </div>
      </div>

      <Timeline
        duration={realDuration}
        currentTime={currentTime}
        regions={editable}
        waveform={analysis?.waveform}
        waveformMic={analysis?.waveform_mic}
        selectedId={selectedId}
        onSeek={seekTo}
      />

      <ToastContainer toasts={toasts} />
      <KeyboardShortcuts open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
