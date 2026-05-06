import { useEffect, useRef } from 'react';
import { fmt } from '../utils/format.js';

const VIDEO_TRACK_H = 44;
const AUDIO_TRACK_H = 66;
const TIME_LABEL_H = 14;

function useCanvasSize(ref) {
  useEffect(() => {
    if (!ref.current) return undefined;
    const canvas = ref.current;
    const ro = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      canvas.width = Math.max(1, w * dpr);
      canvas.height = Math.max(1, h * dpr);
    });
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [ref]);
}

function timeToX(t, duration, width) {
  if (!duration) return 0;
  return (t / duration) * width;
}

function drawPlayhead(ctx, x, height) {
  ctx.strokeStyle = '#fbbf24';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, height);
  ctx.stroke();

  ctx.fillStyle = '#fbbf24';
  ctx.beginPath();
  ctx.moveTo(x - 5, 0);
  ctx.lineTo(x + 5, 0);
  ctx.lineTo(x, 6);
  ctx.closePath();
  ctx.fill();
}

function drawSilentRegions(ctx, regions, duration, width, height, selectedId) {
  for (const r of regions) {
    const x1 = timeToX(r.start, duration, width);
    const x2 = timeToX(r.end, duration, width);
    const w = Math.max(1, x2 - x1);
    ctx.fillStyle = 'rgba(239,68,68,0.5)';
    ctx.fillRect(x1, 0, w, height);
    ctx.strokeStyle = r.id === selectedId ? '#fff' : '#f87171';
    ctx.lineWidth = r.id === selectedId ? 2 : 1;
    ctx.strokeRect(x1 + 0.5, 0.5, w - 1, height - 1);
  }
}

function drawWaveform(ctx, waveform, width, height, color) {
  if (!waveform || waveform.length === 0) return;
  const barW = width / waveform.length;
  for (let i = 0; i < waveform.length; i++) {
    const v = waveform[i];
    const barH = Math.max(1, v * (height - 6));
    const opacity = 0.3 + v * 0.7;
    ctx.fillStyle = `${color}${Math.round(opacity * 255).toString(16).padStart(2, '0')}`;
    ctx.fillRect(i * barW, (height - barH) / 2, Math.max(1, barW - 0.3), barH);
  }
}

export default function Timeline({
  duration,
  currentTime,
  regions,
  waveform,
  waveformMic,
  selectedId,
  onSeek,
}) {
  const videoCanvasRef = useRef(null);
  const audioCanvasRef = useRef(null);
  useCanvasSize(videoCanvasRef);
  useCanvasSize(audioCanvasRef);

  useEffect(() => {
    let raf = 0;
    let lastDrawAt = 0;

    const draw = () => {
      // ~30 fps is more than enough for the playhead — saves battery.
      const now = performance.now();
      if (now - lastDrawAt < 32) {
        raf = requestAnimationFrame(draw);
        return;
      }
      lastDrawAt = now;

      const dpr = window.devicePixelRatio || 1;

      const vc = videoCanvasRef.current;
      if (vc) {
        const ctx = vc.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const w = vc.clientWidth;
        const h = vc.clientHeight;
        ctx.clearRect(0, 0, w, h);

        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        ctx.fillStyle = '#8b949e';
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';
        if (duration > 0) {
          const step = duration < 30 ? 5 : duration < 120 ? 10 : 30;
          for (let t = 0; t <= duration; t += step) {
            const x = timeToX(t, duration, w);
            ctx.fillText(fmt(t).slice(0, 5), x + 2, 10);
            ctx.strokeStyle = '#21262d';
            ctx.beginPath();
            ctx.moveTo(x, 12);
            ctx.lineTo(x, TIME_LABEL_H);
            ctx.stroke();
          }
        }

        const trackY = TIME_LABEL_H;
        const trackH = h - TIME_LABEL_H;
        ctx.fillStyle = '#1d4ed8';
        ctx.fillRect(0, trackY, w, trackH);

        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = 1;
        for (let x = 80; x < w; x += 80) {
          ctx.beginPath();
          ctx.moveTo(x, trackY);
          ctx.lineTo(x, trackY + trackH);
          ctx.stroke();
        }

        if (regions && regions.length > 0 && duration > 0) {
          ctx.save();
          ctx.translate(0, trackY);
          drawSilentRegions(ctx, regions, duration, w, trackH, selectedId);
          ctx.restore();
        }

        if (duration > 0) {
          const px = timeToX(currentTime, duration, w);
          drawPlayhead(ctx, px, h);
        }
      }

      const ac = audioCanvasRef.current;
      if (ac) {
        const ctx = ac.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const w = ac.clientWidth;
        const h = ac.clientHeight;
        ctx.clearRect(0, 0, w, h);

        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        const hasMic = !!(waveformMic && waveformMic.length > 0);
        if (hasMic) {
          // Split the audio strip into two halves: video on top, mic on bottom.
          const halfH = Math.floor(h / 2);

          ctx.save();
          ctx.translate(0, 0);
          ctx.strokeStyle = '#21262d';
          ctx.beginPath();
          ctx.moveTo(0, halfH / 2);
          ctx.lineTo(w, halfH / 2);
          ctx.stroke();
          drawWaveform(ctx, waveform, w, halfH, '#34d399');  // green = video audio
          ctx.restore();

          ctx.save();
          ctx.translate(0, halfH);
          ctx.strokeStyle = '#21262d';
          ctx.beginPath();
          ctx.moveTo(0, halfH / 2);
          ctx.lineTo(w, halfH / 2);
          ctx.stroke();
          drawWaveform(ctx, waveformMic, w, h - halfH, '#60a5fa');  // blue = mic
          ctx.restore();

          // tiny labels
          ctx.fillStyle = 'rgba(52,211,153,0.6)';
          ctx.font = 'bold 8px sans-serif';
          ctx.textAlign = 'left';
          ctx.fillText('VIDEO', 4, 10);
          ctx.fillStyle = 'rgba(96,165,250,0.7)';
          ctx.fillText('MIK', 4, halfH + 10);
        } else {
          ctx.strokeStyle = '#21262d';
          ctx.beginPath();
          ctx.moveTo(0, h / 2);
          ctx.lineTo(w, h / 2);
          ctx.stroke();
          drawWaveform(ctx, waveform, w, h, '#34d399');
        }

        if (regions && regions.length > 0 && duration > 0) {
          drawSilentRegions(ctx, regions, duration, w, h, selectedId);
        }

        if (duration > 0) {
          const px = timeToX(currentTime, duration, w);
          drawPlayhead(ctx, px, h);
        }
      }

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [duration, currentTime, regions, waveform, waveformMic, selectedId]);

  const handleClick = (e) => {
    if (!duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = (x / rect.width) * duration;
    onSeek(t);
  };

  return (
    <div className="timeline-area">
      <canvas
        ref={videoCanvasRef}
        onClick={handleClick}
        style={{ width: '100%', height: VIDEO_TRACK_H, display: 'block', cursor: 'pointer' }}
      />
      <div style={{ height: 4 }} />
      <canvas
        ref={audioCanvasRef}
        onClick={handleClick}
        style={{ width: '100%', height: AUDIO_TRACK_H, display: 'block', cursor: 'pointer' }}
      />
    </div>
  );
}
