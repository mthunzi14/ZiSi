let _timeout = null;

export function showToast(message, type = 'info', duration = 4000) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  clearTimeout(_timeout);

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add('show'));

  _timeout = setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

export function dismissToast() {
  const toast = document.querySelector('.toast');
  if (toast) {
    toast.remove();
    clearTimeout(_timeout);
  }
}
