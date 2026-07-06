(() => {
  const carousel = document.querySelector('.hero-carousel');
  if (!carousel) {
    return;
  }

  const track = carousel.querySelector('.hero-carousel-track');
  let images = Array.from(carousel.querySelectorAll('.carousel-image'));
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

  // Filename metadata format: order=<int>__river=<name>__city=<name>__state=<abbr>__bio=<0-1>__phys=<0-1>__chem=<0-1>.<ext>
  // order is optional; images without order are shown after ordered images.
  const parseImageFilename = (filename) => {
    const result = {
      order: null,
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
        } else if (key === 'order') {
          const orderValue = parseInt(value, 10);
          if (Number.isFinite(orderValue)) {
            result.order = orderValue;
          }
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
    const ecosystemCondition = hasScores ? clampScore((data.bio + data.phys + data.chem) / 3) : null;
    const location =
      data.river && data.city && data.state
        ? `${data.river}, ${data.city}, ${data.state}`
        : 'Unknown';

    const formatScore = (value) => ({
      value: Number.isFinite(value) ? value : null,
      text: Number.isFinite(value) ? value.toFixed(2) : 'N/A',
    });

    return {
      ecosystemCondition: formatScore(ecosystemCondition),
      physical: formatScore(data.phys),
      chemical: formatScore(data.chem),
      biological: formatScore(data.bio),
      location: safeDecode(location),
    };
  };

  const getFilenameFromImage = (img) => {
    if (!img) {
      return '';
    }
    const src = img.getAttribute('src') || '';
    return src.split('/').pop() || '';
  };

  const sortImagesByMetadataOrder = () => {
    if (images.length <= 1) {
      return;
    }

    const ranked = images.map((img, originalIndex) => {
      const parsed = parseImageFilename(getFilenameFromImage(img));
      const hasOrder = Number.isFinite(parsed.order);
      return {
        img,
        originalIndex,
        hasOrder,
        order: hasOrder ? parsed.order : Number.POSITIVE_INFINITY,
      };
    });

    ranked.sort((a, b) => {
      if (a.hasOrder && b.hasOrder) {
        if (a.order !== b.order) {
          return a.order - b.order;
        }
        return a.originalIndex - b.originalIndex;
      }
      if (a.hasOrder !== b.hasOrder) {
        return a.hasOrder ? -1 : 1;
      }
      return a.originalIndex - b.originalIndex;
    });

    const sorted = ranked.map((entry) => entry.img);
    sorted.forEach((img) => {
      track.appendChild(img);
    });
    images = sorted;
  };

  const summaryColorForValue = (value) => {
    if (!Number.isFinite(value)) {
      return '#c8d9f2';
    }
    if (value <= 0.39) {
      return '#f5b5b5';
    }
    if (value <= 0.69) {
      return '#f5e7a6';
    }
    return '#c8d9f2';
  };

  const getScoreRows = (formatted) => [
    {
      label: 'Ecosystem Condition',
      score: formatted.ecosystemCondition,
      ecosystem: true,
    },
    { label: 'Physical', score: formatted.physical, subIndex: true },
    { label: 'Chemical', score: formatted.chemical, subIndex: true },
    { label: 'Biological', score: formatted.biological, subIndex: true },
  ];

  const createMetricRow = (rowData, isSubIndex = false) => {
    const row = document.createElement('div');
    row.className = 'overlay-metric';
    if (rowData.ecosystem) {
      row.classList.add('is-ecosystem');
    }
    if (isSubIndex) {
      row.classList.add('is-subindex');
    }
    if (!Number.isFinite(rowData.score.value)) {
      row.classList.add('is-na');
    }

    const header = document.createElement('div');
    header.className = 'overlay-metric-header';

    const label = document.createElement('span');
    label.className = 'overlay-metric-label';
    label.textContent = rowData.label;

    const value = document.createElement('span');
    value.className = 'overlay-metric-value';
    value.textContent = rowData.score.text;

    header.appendChild(label);
    header.appendChild(value);

    const barRow = document.createElement('div');
    barRow.className = 'overlay-metric-bar-row';

    const bar = document.createElement('div');
    bar.className = 'overlay-bar';
    const barFill = document.createElement('span');
    barFill.className = 'overlay-bar-fill';
    barFill.style.width = Number.isFinite(rowData.score.value)
      ? `${(rowData.score.value * 100).toFixed(1)}%`
      : '0%';
    barFill.style.background = summaryColorForValue(rowData.score.value);
    bar.appendChild(barFill);

    barRow.appendChild(bar);
    row.appendChild(header);
    row.appendChild(barRow);

    return row;
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
    const filename = getFilenameFromImage(img);
    const parsed = parseImageFilename(filename);
    const formatted = formatOverlay(parsed);
    topLeftOverlay.innerHTML = '';

    const condition = document.createElement('div');
    condition.className = 'overlay-condition';
    condition.textContent = 'Index Scores';

    const rows = getScoreRows(formatted);
    const metrics = document.createElement('div');
    metrics.className = 'overlay-metrics';
    rows.forEach((rowData) => {
      metrics.appendChild(createMetricRow(rowData, Boolean(rowData.subIndex)));
    });

    topLeftOverlay.appendChild(condition);
    topLeftOverlay.appendChild(metrics);
    bottomRightOverlay.textContent = formatted.location;
  };

  sortImagesByMetadataOrder();

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
      'order=2__river=Poudre River__city=Windsor__state=CO__bio=0.87__phys=0.87__chem=0.88.jpg',
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
