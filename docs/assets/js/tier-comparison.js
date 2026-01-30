(() => {
  const tables = document.querySelectorAll('[data-tier-comparison="true"]');
  if (!tables.length) {
    return;
  }

  const path = window.location.pathname.toLowerCase();
  let focus = '';
  if (path.includes('/tiers/screening')) {
    focus = 'screening';
  } else if (path.includes('/tiers/rapid')) {
    focus = 'rapid';
  } else if (path.includes('/tiers/detailed')) {
    focus = 'detailed';
  }

  if (!focus) {
    return;
  }

  tables.forEach((table) => {
    table.classList.add(`tier-focus-${focus}`);
    table.dataset.tierFocus = focus;
  });
})();
