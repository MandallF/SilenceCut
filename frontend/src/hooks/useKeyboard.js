import { useEffect, useRef } from 'react';

/**
 * Bind a global keydown listener once and dispatch through the latest
 * `handlers` object via a ref. This avoids removing/re-adding the listener
 * on every render (which the parent triggers because the handlers object
 * literal is recreated each time).
 */
export function useKeyboard(handlers) {
  const handlersRef = useRef(handlers);
  // Keep the ref in sync with the latest props/callbacks on every render.
  handlersRef.current = handlers;

  useEffect(() => {
    const onKeyDown = (e) => {
      const tag = document.activeElement?.tagName;
      const isEditable =
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        document.activeElement?.isContentEditable;
      if (isEditable) return;

      const h = handlersRef.current || {};
      const key = e.key;
      const shift = e.shiftKey;
      const ctrl = e.ctrlKey || e.metaKey;

      if (ctrl && (key === 'z' || key === 'Z')) {
        e.preventDefault();
        if (shift) h.redo?.();
        else h.undo?.();
        return;
      }
      if (ctrl) return;

      switch (key) {
        case ' ':
        case 'Spacebar':
          e.preventDefault();
          h.togglePlay?.();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          h.seek?.(shift ? -1 : -5);
          break;
        case 'ArrowRight':
          e.preventDefault();
          h.seek?.(shift ? 1 : 5);
          break;
        case 'j':
        case 'J':
          h.seek?.(-10);
          break;
        case 'l':
        case 'L':
          h.seek?.(10);
          break;
        case 'k':
        case 'K':
          h.pause?.();
          break;
        case 'Home':
          e.preventDefault();
          h.seekTo?.(0);
          break;
        case 'End':
          e.preventDefault();
          h.seekToEnd?.();
          break;
        case 'Enter':
          h.analyze?.();
          break;
        case 'Escape':
          h.clearSelection?.();
          break;
        case 'Delete':
        case 'Backspace':
          e.preventDefault();
          h.keepSelected?.();
          break;
        case '?':
        case 'h':
        case 'H':
          h.toggleHelp?.();
          break;
        default:
          break;
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
