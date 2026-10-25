// Sets the saved theme before first paint, so a dark-mode reload does not flash light.
// Lives in its own file (not inline) because the Content-Security-Policy forbids inline scripts.
try {
  var theme = localStorage.getItem('recon.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
} catch {
  /* storage unavailable: follow the system setting */
}
