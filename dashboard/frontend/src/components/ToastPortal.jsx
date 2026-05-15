import { createPortal } from 'react-dom';
import './ToastPortal.css';

export default function ToastPortal({ message, type = 'neutral', isVisible }) {
  if (!isVisible || !message) return null;

  const toastType = ['success', 'error', 'warning', 'neutral'].includes(type) ? type : 'neutral';

  return createPortal(
    <div
      className={`toast-portal toast-${toastType}`}
      role="alert"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="toast-inner">
        <span className="toast-content">{message}</span>
      </div>
    </div>,
    document.body,
  );
}
