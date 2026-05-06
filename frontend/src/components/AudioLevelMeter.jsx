import { useEffect, useRef } from 'react';
import { thresholdToDb } from '../utils/format.js';

const MIN_DB = -60;
const MAX_DB = 0;

function dbToY(db, height) {
  const clamped = Math.max(MIN_DB, Math.min(MAX_DB, db));
  const t = (clamped - MIN_DB) / (MAX_DB - MIN_DB);
  return height - t * height;
}

export default function AudioLevelMeter({ rmsDb, peakDb, threshold, active }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.clearRect(0, 0, cssW, cssH);

    ctx.fillStyle = '#010409';
    ctx.fillRect(0, 0, cssW, cssH);

    const barX = 8;
    const barW = cssW - 16;
    const barTop = 12;
    const barBottom = cssH - 26;
    const barH = barBottom - barTop;

    ctx.fillStyle = '#1e293b';
    ctx.fillRect(barX, barTop, barW, barH);

    if (active) {
      const levelY = dbToY(rmsDb, barH) + barTop;

      const yMinus10 = dbToY(-10, barH) + barTop;
      const yMinus20 = dbToY(-20, barH) + barTop;
      const yMinus40 = dbToY(-40, barH) + barTop;

      if (levelY < yMinus10) {
        ctx.fillStyle = '#ef4444';
        ctx.fillRect(barX, levelY, barW, yMinus10 - levelY);
      }
      const yellowTop = Math.max(levelY, yMinus10);
      if (yellowTop < yMinus20) {
        ctx.fillStyle = '#f59e0b';
        ctx.fillRect(barX, yellowTop, barW, yMinus20 - yellowTop);
      }
      const greenTop = Math.max(levelY, yMinus20);
      if (greenTop < yMinus40) {
        ctx.fillStyle = '#22c55e';
        ctx.fillRect(barX, greenTop, barW, yMinus40 - greenTop);
      }
      const dimTop = Math.max(levelY, yMinus40);
      if (dimTop < barBottom) {
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(barX, dimTop, barW, barBottom - dimTop);
      }
    }

    ctx.strokeStyle = '#21262d';
    ctx.lineWidth = 1;
    ctx.font = '8px monospace';
    ctx.fillStyle = '#484f58';
    ctx.textAlign = 'right';
    [0, -10, -20, -30, -40, -60].forEach((db) => {
      const y = dbToY(db, barH) + barTop;
      ctx.beginPath();
      ctx.moveTo(barX - 2, y);
      ctx.lineTo(barX, y);
      ctx.stroke();
    });

    if (active) {
      const peakY = dbToY(peakDb, barH) + barTop;
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(barX, peakY);
      ctx.lineTo(barX + barW, peakY);
      ctx.stroke();
    }

    const thrDb = thresholdToDb(threshold);
    const thrY = dbToY(thrDb, barH) + barTop;
    ctx.strokeStyle = '#f97316';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 2]);
    ctx.beginPath();
    ctx.moveTo(barX - 4, thrY);
    ctx.lineTo(barX + barW + 4, thrY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = '#f97316';
    ctx.font = 'bold 7px sans-serif';
    ctx.textAlign = 'center';
    if (thrY > barTop + 4) {
      ctx.fillText('EŞİK', cssW / 2, thrY - 3);
    }

    ctx.fillStyle = active ? '#c9d1d9' : '#484f58';
    ctx.font = 'bold 9px monospace';
    ctx.textAlign = 'center';
    const label = active ? `${Math.round(rmsDb)}` : '—';
    ctx.fillText(label, cssW / 2, cssH - 12);
    if (active) {
      ctx.fillStyle = '#8b949e';
      ctx.font = '8px monospace';
      ctx.fillText('dB', cssW / 2, cssH - 3);
    }
  }, [rmsDb, peakDb, threshold, active]);

  return (
    <div style={{ width: '100%', height: '100%', background: '#010409' }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
    </div>
  );
}
