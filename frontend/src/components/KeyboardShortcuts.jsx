const SHORTCUTS = [
  ['Space', 'Video oynat / duraklat'],
  ['← / →', '5 saniye geri / ileri'],
  ['Shift + ← / →', '1 saniye geri / ileri'],
  ['J / L', '10 saniye geri / ileri'],
  ['K', 'Videoyu durdur'],
  ['Home / End', 'Başa / sona git'],
  ['Enter', 'Sessizlikleri tespit et'],
  ['Escape', 'Bölge seçimini kaldır'],
  ['Delete / Backspace', 'Seçili bölgeyi koru'],
  ['Ctrl + Z', 'Geri al'],
  ['Ctrl + Shift + Z', 'Yinele'],
  ['? / H', 'Bu yardım modalını aç/kapat'],
];

export default function KeyboardShortcuts({ open, onClose }) {
  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Klavye Kısayolları</h2>
        <table>
          <tbody>
            {SHORTCUTS.map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="modal-close-hint">Escape ile kapat</div>
      </div>
    </div>
  );
}
