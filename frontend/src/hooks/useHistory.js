import { useCallback, useState } from 'react';

export function useHistory(initial) {
  const [state, setState] = useState({ stack: [initial], cursor: 0 });

  const push = useCallback((newValue) => {
    setState((prev) => {
      const trimmed = prev.stack.slice(0, prev.cursor + 1);
      trimmed.push(newValue);
      return { stack: trimmed, cursor: trimmed.length - 1 };
    });
  }, []);

  const reset = useCallback((newValue) => {
    setState({ stack: [newValue], cursor: 0 });
  }, []);

  const undo = useCallback(() => {
    setState((prev) => (prev.cursor > 0 ? { ...prev, cursor: prev.cursor - 1 } : prev));
  }, []);

  const redo = useCallback(() => {
    setState((prev) => (
      prev.cursor < prev.stack.length - 1 ? { ...prev, cursor: prev.cursor + 1 } : prev
    ));
  }, []);

  return {
    current: state.stack[state.cursor],
    push,
    reset,
    undo,
    redo,
    canUndo: state.cursor > 0,
    canRedo: state.cursor < state.stack.length - 1,
  };
}
