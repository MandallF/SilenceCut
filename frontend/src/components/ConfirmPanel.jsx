import { fmt } from '../utils/format.js';

const QUALITY_OPTIONS = [
  { value: 'fast',     label: 'Hızlı',         hint: 'En hızlı encode · büyük dosya · iyi kalite' },
  { value: 'balanced', label: 'Dengeli',       hint: 'Orta hız · orta dosya · yüksek kalite (önerilen)' },
  { value: 'high',     label: 'Yüksek Kalite', hint: 'Yavaş encode · küçük dosya · görsel-olarak-kayıpsız' },
];

export default function ConfirmPanel({
  regions,
  duration,
  selectedId,
  onSelect,
  onKeep,
  onEdit,
  onExport,
  onReset,
  exporting,
  exportProgress,
  exportEta,
  exportSpeed,
  exportPhase,
  quality,
  setQuality,
}) {
  // Format an ETA in seconds as a human-friendly string ("3:45", "1s 12dk").
  const fmtEta = (s) => {
    if (s == null || !isFinite(s) || s < 0) return null;
    if (s < 60) return `${Math.ceil(s)} sn`;
    if (s < 3600) return `${Math.floor(s / 60)} dk ${Math.round(s % 60)} sn`;
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return `${h} sa ${m} dk`;
  };
  const totalRemoved = regions.reduce((sum, r) => sum + (r.end - r.start), 0);
  const pct = duration > 0 ? (totalRemoved / duration) * 100 : 0;

  return (
    <div className="confirm-panel">
      <h3 style={{
        margin: '0 0 8px',
        fontSize: 11,
        textTransform: 'uppercase',
        letterSpacing: 1,
        color: '#8b949e',
      }}>
        Onay
      </h3>

      <div className="confirm-summary">
        <span className="tag tag-danger">{regions.length} bölge</span>
        <span className="tag tag-success">−{fmt(totalRemoved)} ({pct.toFixed(1)}%)</span>
      </div>

      <p className="help-text">
        Bölgeye tıkla → videoya atla · Koru → o anı kesme
      </p>

      <div className="region-list">
        {regions.length === 0 && (
          <div style={{ color: '#484f58', fontSize: 12, textAlign: 'center', padding: 16 }}>
            Kesilecek bölge yok
          </div>
        )}
        {regions.map((r, idx) => (
          <div
            key={r.id}
            className={`region-card ${r.id === selectedId ? 'selected' : ''}`}
            onClick={() => onSelect(r)}
          >
            <div className="region-card-head">
              <span className="region-title">Bölge {idx + 1}</span>
              <button
                className="btn-keep"
                onClick={(e) => { e.stopPropagation(); onKeep(r); }}
              >
                Koru ✓
              </button>
            </div>
            <div className="region-times">
              {fmt(r.start)} → {fmt(r.end)}{' '}
              <span className="dur">({fmt(r.end - r.start)})</span>
            </div>
            <div className="region-edit" onClick={(e) => e.stopPropagation()}>
              <div>
                <label>BAŞ (sn)</label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={r.start.toFixed(2)}
                  onChange={(e) => onEdit(r.id, { start: parseFloat(e.target.value) || 0 })}
                />
              </div>
              <div>
                <label>BİT (sn)</label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={r.end.toFixed(2)}
                  onChange={(e) => onEdit(r.id, { end: parseFloat(e.target.value) || 0 })}
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="confirm-actions">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 4 }}>
          <label style={{ fontSize: 11, color: '#8b949e' }}>
            Çıktı Kalitesi
          </label>
          <select
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
            disabled={exporting}
            style={{
              background: '#0d1117', color: '#c9d1d9',
              border: '1px solid #30363d', padding: '5px 8px',
              borderRadius: 4, fontSize: 12,
            }}
          >
            {QUALITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <div style={{ fontSize: 10, color: '#484f58', lineHeight: 1.4 }}>
            {QUALITY_OPTIONS.find((o) => o.value === quality)?.hint}
          </div>
        </div>
        <button
          className="btn btn-success"
          disabled={exporting || regions.length === 0}
          onClick={onExport}
        >
          {exporting
            ? (exportPhase === 'starting'
                ? 'Başlatılıyor…'
                : exportPhase === 'streaming'
                  ? 'Dosya kaydediliyor…'
                  : exportPhase === 'finalizing'
                    ? 'Sonlandırılıyor…'
                    : `İşleniyor… %${exportProgress.toFixed(1)}`)
            : '⬇ Videoyu Kes ve İndir'}
        </button>
        {exporting && (
          <>
            <div className="progress">
              <div style={{ width: `${Math.max(2, exportProgress)}%` }} />
            </div>
            {exportPhase === 'encoding' && (
              <div style={{ fontSize: 10, color: '#8b949e', marginTop: 4, lineHeight: 1.5 }}>
                {fmtEta(exportEta) && <>Kalan: <b>{fmtEta(exportEta)}</b> · </>}
                {exportSpeed > 0 && <>Hız: <b>{exportSpeed.toFixed(2)}×</b> realtime</>}
              </div>
            )}
          </>
        )}
        <button className="btn btn-secondary" onClick={onReset} disabled={exporting}>
          ↩ Orijinale Sıfırla
        </button>
      </div>
    </div>
  );
}
