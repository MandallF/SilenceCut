import { useEffect, useRef, useState } from 'react';

export function useAudioMeter(videoRef, enabled = true) {
  const [rms, setRms] = useState(0);
  const [peak, setPeak] = useState(0);
  const [active, setActive] = useState(false);

  const ctxRef = useRef(null);
  const sourceRef = useRef(null);
  const analyserRef = useRef(null);
  const rafRef = useRef(0);
  const peakHistoryRef = useRef([]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !enabled) return undefined;

    let cancelled = false;

    const ensureContext = async () => {
      if (ctxRef.current) return;
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const source = ctx.createMediaElementSource(video);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        source.connect(analyser);
        analyser.connect(ctx.destination);
        ctxRef.current = ctx;
        sourceRef.current = source;
        analyserRef.current = analyser;
        if (ctx.state === 'suspended') {
          try { await ctx.resume(); } catch { /* ignore */ }
        }
        if (!cancelled) setActive(true);
      } catch (err) {
        // createMediaElementSource throws if already attached on this element.
        // eslint-disable-next-line no-console
        console.warn('AudioMeter init failed:', err);
      }
    };

    const onPlay = () => { ensureContext(); };
    video.addEventListener('play', onPlay);

    const data = new Float32Array(1024);

    const tick = () => {
      const analyser = analyserRef.current;
      if (!analyser) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const r = Math.sqrt(sum / data.length);

      if (video.paused || video.ended) {
        setRms(0);
        setPeak(0);
      } else {
        setRms(r);
        const now = performance.now();
        const hist = peakHistoryRef.current;
        hist.push({ t: now, v: r });
        while (hist.length && now - hist[0].t > 2000) hist.shift();
        let p = 0;
        for (const item of hist) if (item.v > p) p = item.v;
        setPeak(p);
      }

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      cancelled = true;
      video.removeEventListener('play', onPlay);
      cancelAnimationFrame(rafRef.current);
      try {
        if (sourceRef.current) sourceRef.current.disconnect();
        if (analyserRef.current) analyserRef.current.disconnect();
        if (ctxRef.current) ctxRef.current.close();
      } catch { /* ignore */ }
      ctxRef.current = null;
      sourceRef.current = null;
      analyserRef.current = null;
      peakHistoryRef.current = [];
      setActive(false);
      setRms(0);
      setPeak(0);
    };
  }, [videoRef, enabled]);

  const rmsDb = 20 * Math.log10(Math.max(rms, 0.0001));
  const peakDb = 20 * Math.log10(Math.max(peak, 0.0001));

  return { rms, rmsDb, peak, peakDb, active };
}
