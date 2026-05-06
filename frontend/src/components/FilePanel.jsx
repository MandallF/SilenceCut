import { useRef, useState } from 'react';
import { fmtSize, thresholdToDb } from '../utils/format.js';

function SuggestionCard({ suggestion, onApply, onDismiss }) {
  const v = suggestion.video;
  const m = suggestion.mic;
  const conf = (v?.confidence || m?.confidence || 'medium');
  const confLabel = { high: 'Güvenilir', medium: 'Orta', low: 'Belirsiz' }[conf] || 'Orta';
  const confColor = { high: '#34d399', medium: '#fbbf24', low: '#f87171' }[conf] || '#fbbf24';

  return (
    <div style={{
      marginTop: 12, padding: 10, borderRadius: 4,
      background: 'rgba(52,211,153,0.06)', border: '1px solid rgba(52,211,153,0.3)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: '#34d399' }}>📊 Önerilen Eşikler</span>
        <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, color: confColor, border: `1px solid ${confColor}66` }}>
          {confLabel}
        </span>
      </div>
      <div style={{ fontSize: 11, color: '#c9d1d9', lineHeight: 1.6 }}>
        {v && (
          <div>
            Video sesi: <b>{(v.threshold * 1000).toFixed(0)}</b>
            <span style={{ color: '#8b949e' }}> ({thresholdToDb(v.threshold).toFixed(0)} dB)</span>
          </div>
        )}
        {m && (
          <div>
            Mikrofon: <b>{(m.threshold * 1000).toFixed(0)}</b>
            <span style={{ color: '#8b949e' }}> ({thresholdToDb(m.threshold).toFixed(0)} dB)</span>
          </div>
        )}
      </div>
      {conf === 'low' && (
        <div style={{ fontSize: 10, color: '#fbbf24', marginTop: 6, lineHeight: 1.4 }}>
          ⚠ Sesli ve sessiz alanlar arasında belirgin fark yok; öneri güvenilir olmayabilir.
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button
          onClick={onApply}
          style={{
            flex: 1, background: '#34d399', color: '#0d1117',
            border: 'none', borderRadius: 3, padding: '5px 8px',
            fontSize: 11, fontWeight: 600, cursor: 'pointer',
          }}
        >
          Uygula
        </button>
        <button
          onClick={onDismiss}
          style={{
            background: 'transparent', color: '#8b949e',
            border: '1px solid #30363d', borderRadius: 3, padding: '5px 10px',
            fontSize: 11, cursor: 'pointer',
          }}
        >
          Yoksay
        </button>
      </div>
    </div>
  );
}

const ACCEPTED_VIDEO = '.mp4,.mov,.avi,.mkv,.webm,video/*';
const ACCEPTED_MIC = '.wav,.mp3,.m4a,.aac,.flac,.ogg,audio/*';

export default function FilePanel({
  file,
  uploadProgress,
  uploading,
  uploadFinalizing,
  analyzing,
  threshold,
  setThreshold,
  minSilence,
  setMinSilence,
  onUpload,
  onAnalyze,
  hasAnalysis,
  // mic
  mic,
  micUploading,
  micUploadProgress,
  micUploadFinalizing,
  micThreshold,
  setMicThreshold,
  micOffset,
  setMicOffset,
  onUploadMic,
  onRemoveMic,
  // suggestion
  suggestion,
  suggesting,
  onApplySuggestion,
  onDismissSuggestion,
}) {
  const inputRef = useRef(null);
  const micInputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [micDragging, setMicDragging] = useState(false);

  const pickFile = () => inputRef.current?.click();
  const pickMic = () => micInputRef.current?.click();

  const onFiles = (files) => {
    if (!files || files.length === 0) return;
    onUpload(files[0]);
  };
  const onMicFiles = (files) => {
    if (!files || files.length === 0) return;
    onUploadMic(files[0]);
  };

  return (
    <div className="file-panel">
      <h3>Video</h3>
      <div
        className={`dropzone ${dragging ? 'dragging' : ''}`}
        onClick={pickFile}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          onFiles(e.dataTransfer.files);
        }}
      >
        <div style={{ fontSize: 22, marginBottom: 6 }}>＋</div>
        <div>{file ? 'Yeni video yükle' : 'Video seç veya sürükle bırak'}</div>
        <div style={{ fontSize: 10, color: '#484f58', marginTop: 4 }}>MP4 · MOV · AVI · MKV · WebM</div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_VIDEO}
          style={{ display: 'none' }}
          onChange={(e) => onFiles(e.target.files)}
        />
      </div>

      {uploading && (
        <>
          <div style={{ fontSize: 11, color: '#8b949e', marginTop: 10 }}>
            {uploadFinalizing
              ? 'Sunucu dosyayı kaydediyor…'
              : `Yükleniyor… %${Math.floor(uploadProgress)}`}
          </div>
          <div className="progress">
            <div
              style={{
                width: `${uploadProgress}%`,
                background: uploadFinalizing ? '#fbbf24' : '#1f6feb',
              }}
            />
          </div>
        </>
      )}

      {file && !uploading && (
        <div className="file-info">
          <div className="name">{file.filename}</div>
          <div className="size">{fmtSize(file.size)}</div>
        </div>
      )}

      {/* Mikrofon (opsiyonel) */}
      {file && !uploading && (
        <>
          <h3 style={{ marginTop: 16 }}>Mikrofon Kaydı (Opsiyonel)</h3>
          {!mic && (
            <div
              className={`dropzone ${micDragging ? 'dragging' : ''}`}
              style={{ padding: 14 }}
              onClick={pickMic}
              onDragOver={(e) => { e.preventDefault(); setMicDragging(true); }}
              onDragLeave={() => setMicDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setMicDragging(false);
                onMicFiles(e.dataTransfer.files);
              }}
            >
              <div style={{ fontSize: 16, marginBottom: 4 }}>🎙</div>
              <div style={{ fontSize: 12 }}>Ses dosyası seç veya bırak</div>
              <div style={{ fontSize: 10, color: '#484f58', marginTop: 3 }}>WAV · MP3 · M4A · FLAC · OGG</div>
              <input
                ref={micInputRef}
                type="file"
                accept={ACCEPTED_MIC}
                style={{ display: 'none' }}
                onChange={(e) => onMicFiles(e.target.files)}
              />
            </div>
          )}

          {micUploading && (
            <>
              <div style={{ fontSize: 11, color: '#8b949e', marginTop: 10 }}>
                {micUploadFinalizing
                  ? 'Sunucu dosyayı kaydediyor…'
                  : `Mikrofon yükleniyor… %${Math.floor(micUploadProgress)}`}
              </div>
              <div className="progress">
                <div
                  style={{
                    width: `${micUploadProgress}%`,
                    background: micUploadFinalizing ? '#fbbf24' : '#1f6feb',
                  }}
                />
              </div>
            </>
          )}

          {mic && !micUploading && (
            <div className="file-info" style={{ position: 'relative' }}>
              <button
                onClick={onRemoveMic}
                title="Mikrofon kaydını kaldır"
                style={{
                  position: 'absolute', top: 6, right: 6,
                  background: 'transparent', border: 'none',
                  color: '#8b949e', fontSize: 14, padding: 0, lineHeight: 1,
                }}
              >×</button>
              <div className="name" style={{ paddingRight: 16 }}>🎙 {mic.filename}</div>
              <div className="size">{fmtSize(mic.size)}</div>
            </div>
          )}

          {mic && !micUploading && (
            <div style={{ marginTop: 10 }}>
              <div className="slider-row">
                <label>
                  Mikrofon Eşiği
                  <span className="value-tag">
                    [{(micThreshold * 1000).toFixed(0)}] → {thresholdToDb(micThreshold).toFixed(0)} dB
                  </span>
                </label>
                <input
                  type="range"
                  min="0.003"
                  max="0.05"
                  step="0.001"
                  value={micThreshold}
                  onChange={(e) => setMicThreshold(parseFloat(e.target.value))}
                />
              </div>
              <div className="slider-row">
                <label>
                  Mikrofon Ofseti
                  <span className="value-tag">
                    {micOffset >= 0 ? '+' : ''}{micOffset.toFixed(1)} sn
                  </span>
                </label>
                <input
                  type="range"
                  min="-30"
                  max="30"
                  step="0.1"
                  value={micOffset}
                  onChange={(e) => setMicOffset(parseFloat(e.target.value))}
                />
                <div style={{ fontSize: 10, color: '#484f58', marginTop: 2, lineHeight: 1.4 }}>
                  Mikrofon kaydının video zaman çizelgesindeki konumu.
                  Pozitif = mikrofon videodan sonra başlar.
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {/* Suggestion banner */}
      {suggesting && file && (
        <div style={{
          marginTop: 12, padding: 8, borderRadius: 4,
          background: 'rgba(31,111,235,0.08)', border: '1px solid rgba(31,111,235,0.2)',
          fontSize: 11, color: '#8b949e',
        }}>
          📊 Ses analizi yapılıyor, eşik önerisi hesaplanıyor…
        </div>
      )}
      {!suggesting && suggestion && (suggestion.video || suggestion.mic) && (
        <SuggestionCard
          suggestion={suggestion}
          onApply={onApplySuggestion}
          onDismiss={onDismissSuggestion}
        />
      )}

      <div className="settings">
        <h3 style={{ marginTop: 16 }}>Ayarlar</h3>
        <div className="slider-row">
          <label>
            Video Sesi Eşiği
            <span className="value-tag">
              [{(threshold * 1000).toFixed(0)}] → {thresholdToDb(threshold).toFixed(0)} dB
            </span>
          </label>
          <input
            type="range"
            min="0.003"
            max="0.05"
            step="0.001"
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
          />
        </div>
        <div className="slider-row">
          <label>
            Min. Sessizlik
            <span className="value-tag">{minSilence.toFixed(1)} s</span>
          </label>
          <input
            type="range"
            min="0.1"
            max="3"
            step="0.1"
            value={minSilence}
            onChange={(e) => setMinSilence(parseFloat(e.target.value))}
          />
        </div>

        {mic && (
          <div style={{ fontSize: 11, color: '#8b949e', padding: '6px 8px', background: 'rgba(31,111,235,0.08)', border: '1px solid rgba(31,111,235,0.2)', borderRadius: 4, marginBottom: 8 }}>
            ℹ Sessizlik ancak <b>her iki kanal da</b> eşiğin altındayken kesilir.
          </div>
        )}

        <button
          className="btn"
          disabled={!file || uploading || analyzing || micUploading}
          onClick={onAnalyze}
        >
          {analyzing ? 'Analiz ediliyor…' : hasAnalysis ? 'Yeniden Analiz Et' : 'Sessizlikleri Tespit Et'}
        </button>
      </div>
    </div>
  );
}
