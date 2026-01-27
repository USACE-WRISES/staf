(() => {
  const carousel = document.querySelector('.hero-carousel');
  if (!carousel) {
    return;
  }

  const track = carousel.querySelector('.hero-carousel-track');
  const images = Array.from(carousel.querySelectorAll('.carousel-image'));
  const prevButton = carousel.querySelector('.carousel-nav.prev');
  const nextButton = carousel.querySelector('.carousel-nav.next');
  if (!track || images.length === 0) {
    return;
  }

  const DEBUG = false;
  const interval = parseInt(carousel.dataset.interval, 10) || 10000;
  let index = 0;
  let timerId = null;

  const clampScore = (value) => {
    if (!Number.isFinite(value)) {
      return null;
    }
    return Math.min(1, Math.max(0, value));
  };

  const safeDecode = (value) => {
    if (!value) {
      return value;
    }
    const normalized = value.replace(/\+/g, ' ');
    try {
      return decodeURIComponent(normalized);
    } catch (error) {
      return normalized;
    }
  };

  const parseImageFilename = (filename) => {
    const result = {
      river: null,
      city: null,
      state: null,
      bio: null,
      phys: null,
      chem: null,
    };

    if (!filename) {
      return result;
    }

    const clean = filename.split('?')[0].split('#')[0];
    const base = clean.replace(/\.(jpg|jpeg|png|webp)$/i, '');
    if (!base) {
      return result;
    }

    if (base.includes('__') && base.includes('=')) {
      const parts = base.split('__');
      parts.forEach((part) => {
        const [rawKey, ...rest] = part.split('=');
        if (!rawKey || rest.length === 0) {
          return;
        }
        const key = rawKey.toLowerCase();
        const rawValue = rest.join('=');
        const value = safeDecode(rawValue);
        const numeric = clampScore(parseFloat(value));

        if (key === 'river') {
          result.river = value;
        } else if (key === 'city') {
          result.city = value;
        } else if (key === 'state') {
          result.state = value;
        } else if (key === 'bio') {
          result.bio = numeric;
        } else if (key === 'phys') {
          result.phys = numeric;
        } else if (key === 'chem') {
          result.chem = numeric;
        }
      });
      return result;
    }

    const tokens = base.split('_').filter(Boolean).map(safeDecode);
    if (tokens.length >= 3) {
      result.river = tokens[0];
      result.city = tokens[1];
      result.state = tokens[2];
    }

    tokens.forEach((token) => {
      const match = token.match(/^(biology|bio|physical|phys|chemical|chem)(.+)$/i);
      if (!match) {
        return;
      }
      const key = match[1].toLowerCase();
      const numeric = clampScore(parseFloat(match[2]));
      if (key.startsWith('bio')) {
        result.bio = numeric;
      } else if (key.startsWith('phys')) {
        result.phys = numeric;
      } else if (key.startsWith('chem')) {
        result.chem = numeric;
      }
    });

    return result;
  };

  const formatOverlay = (data) => {
    const hasScores = [data.bio, data.phys, data.chem].every(Number.isFinite);
    const ecosystemCondition = hasScores
      ? ((data.bio + data.phys + data.chem) / 3).toFixed(2)
      : 'N/A';
    const location =
      data.river && data.city && data.state
        ? `${data.river}, ${data.city}, ${data.state}`
        : 'Unknown';

    const formatScore = (value) => (Number.isFinite(value) ? value.toFixed(2) : 'N/A');

    return {
      ecosystemCondition,
      physical: formatScore(data.phys),
      chemical: formatScore(data.chem),
      biological: formatScore(data.bio),
      location: safeDecode(location),
    };
  };

  const ensureOverlay = (selector, classes) => {
    let element = carousel.querySelector(selector);
    if (!element) {
      element = document.createElement('div');
      element.className = classes;
      carousel.appendChild(element);
    }
    return element;
  };

  const topLeftOverlay = ensureOverlay('.overlay.top-left', 'overlay top-left');
  const bottomRightOverlay = ensureOverlay('.overlay.bottom-right', 'overlay bottom-right');

  const updateOverlay = (img) => {
    const src = img.getAttribute('src') || '';
    const filename = src.split('/').pop() || '';
    const parsed = parseImageFilename(filename);
    const formatted = formatOverlay(parsed);
    topLeftOverlay.innerHTML = '';
    const condition = document.createElement('div');
    condition.className = 'overlay-condition';
    condition.textContent = `Ecosystem Condition: ${formatted.ecosystemCondition}`;
    const list = document.createElement('ul');
    list.className = 'overlay-list';
    const items = [
      `Physical: ${formatted.physical}`,
      `Chemical: ${formatted.chemical}`,
      `Biological: ${formatted.biological}`,
    ];
    items.forEach((text) => {
      const line = document.createElement('li');
      line.className = 'overlay-line';
      line.textContent = text;
      list.appendChild(line);
    });
    topLeftOverlay.appendChild(condition);
    topLeftOverlay.appendChild(list);
    bottomRightOverlay.textContent = formatted.location;
  };

  const showImage = (nextIndex) => {
    images.forEach((img, i) => {
      const isActive = i === nextIndex;
      img.classList.toggle('is-active', isActive);
    });
    track.style.transform = `translateX(-${nextIndex * 100}%)`;
    index = nextIndex;
    updateOverlay(images[nextIndex]);
  };

  showImage(0);

  const startAuto = () => {
    if (images.length <= 1) {
      return;
    }
    if (timerId) {
      clearInterval(timerId);
    }
    timerId = setInterval(() => {
      const nextIndex = (index + 1) % images.length;
      showImage(nextIndex);
    }, interval);
  };

  startAuto();

  if (images.length <= 1) {
    if (prevButton) {
      prevButton.hidden = true;
    }
    if (nextButton) {
      nextButton.hidden = true;
    }
  }

  const goTo = (nextIndex) => {
    if (images.length <= 1) {
      return;
    }
    showImage((nextIndex + images.length) % images.length);
    startAuto();
  };

  if (prevButton) {
    prevButton.addEventListener('click', () => {
      goTo(index - 1);
    });
  }

  if (nextButton) {
    nextButton.addEventListener('click', () => {
      goTo(index + 1);
    });
  }

  if (DEBUG) {
    const samples = [
      'river=StLucie__city=Stuart__state=FL__bio=0.72__phys=0.64__chem=0.58.jpg',
      'RiverX_CityY_ST_Biology0.7_Physical1_Chemical0.3.png',
      'bad_filename.jpg',
    ];
    samples.forEach((sample) => {
      const parsed = parseImageFilename(sample);
      const formatted = formatOverlay(parsed);
      // eslint-disable-next-line no-console
      console.log('[carousel debug]', sample, parsed, formatted);
    });
  }
})();
