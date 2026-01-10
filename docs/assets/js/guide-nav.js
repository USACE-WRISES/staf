(() => {
  const guideNav = document.querySelector('.guide-nav');
  if (!guideNav) {
    return;
  }

  const links = Array.from(guideNav.querySelectorAll('a[href^="#"]'));
  if (links.length === 0) {
    return;
  }

  const linkById = new Map();
  links.forEach((link) => {
    const id = decodeURIComponent(link.getAttribute('href').slice(1));
    linkById.set(id, link);
  });

  const headings = Array.from(document.querySelectorAll('.guide-content h2, .guide-content h3, .guide-content h4'))
    .filter((heading) => linkById.has(heading.id));

  if (headings.length === 0) {
    return;
  }

  const setActive = (id) => {
    links.forEach((link) => link.classList.remove('is-active'));
    const active = linkById.get(id);
    if (active) {
      active.classList.add('is-active');
    }
  };

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          setActive(entry.target.id);
        }
      });
    },
    { rootMargin: '0px 0px -70% 0px' }
  );

  headings.forEach((heading) => observer.observe(heading));

  setActive(headings[0].id);
})();
