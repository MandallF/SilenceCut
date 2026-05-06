import { forwardRef } from 'react';
import { fmt } from '../utils/format.js';

const VideoPlayer = forwardRef(function VideoPlayer(
  { src, regionsCount, savedSeconds, duration, onLoadedMetadata, onTimeUpdate },
  ref
) {
  if (!src) {
    return (
      <div className="video-area">
        <div className="empty-state">
          <div style={{ fontSize: 32, marginBottom: 8 }}>🎬</div>
          <div>Başlamak için video yükleyin</div>
        </div>
      </div>
    );
  }

  return (
    <div className="video-area">
      <video
        ref={ref}
        src={src}
        controls
        onLoadedMetadata={onLoadedMetadata}
        onTimeUpdate={onTimeUpdate}
      />
      {regionsCount > 0 && (
        <div className="video-info">
          <b>{regionsCount}</b> sessiz bölge ·{' '}
          <b>−{fmt(savedSeconds)}</b>
          {duration > 0 && (
            <span style={{ color: '#8b949e' }}> / {fmt(duration)}</span>
          )}
        </div>
      )}
    </div>
  );
});

export default VideoPlayer;
