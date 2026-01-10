(() => {
  // Build the interactive functions table with search, filter, and details.
  const container = document.querySelector('.functions-explorer');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const dataUrl = `${baseUrl}/assets/data/functions.json`;
  const mappingUrl = `${baseUrl}/assets/data/cwa-mapping.json`;
  const fallback = container.querySelector('.functions-explorer-fallback');
  const ui = container.querySelector('.functions-explorer-ui');

  const buildOutcomeText = (mapping) => {
    if (!mapping) {
      return 'P:- C:- B:-';
    }
    return `P:${mapping.physical} C:${mapping.chemical} B:${mapping.biological}`;
  };

  const buildDetails = (fn) => {
    const wrap = document.createElement('div');
    wrap.className = 'details-panel';

    const desc = document.createElement('p');
    desc.textContent = fn.long_description;

    const metrics = document.createElement('div');
    metrics.className = 'details-metrics';

    const metricsTitle = document.createElement('strong');
    metricsTitle.textContent = 'Example metrics by tier';

    const lists = document.createElement('div');
    lists.className = 'metrics-lists';

    const buildList = (title, items) => {
      const block = document.createElement('div');
      const heading = document.createElement('div');
      heading.className = 'metrics-heading';
      heading.textContent = title;
      const list = document.createElement('ul');
      items.forEach((item) => {
        const li = document.createElement('li');
        li.textContent = item;
        list.appendChild(li);
      });
      block.appendChild(heading);
      block.appendChild(list);
      return block;
    };

    lists.appendChild(buildList('Screening', fn.example_metrics.screening || []));
    lists.appendChild(buildList('Rapid', fn.example_metrics.rapid || []));
    lists.appendChild(buildList('Detailed', fn.example_metrics.detailed || []));

    metrics.appendChild(metricsTitle);
    metrics.appendChild(lists);

    const links = document.createElement('div');
    links.className = 'details-links';
    links.innerHTML = `<a href="${baseUrl}/tiers/screening/">Screening</a> | <a href="${baseUrl}/tiers/rapid/">Rapid</a> | <a href="${baseUrl}/tiers/detailed/">Detailed</a> | <a href="${baseUrl}/scoring/">Scoring</a>`;

    wrap.appendChild(desc);
    wrap.appendChild(metrics);
    wrap.appendChild(links);

    return wrap;
  };

  const renderTable = (functionsList, mappingById, tableBody) => {
    tableBody.innerHTML = '';

    functionsList.forEach((fn, index) => {
      const row = document.createElement('tr');

      const categoryCell = document.createElement('td');
      categoryCell.textContent = fn.category;

      const nameCell = document.createElement('td');
      nameCell.textContent = fn.name;

      const shortCell = document.createElement('td');
      shortCell.textContent = fn.short_description;

      const outcomeCell = document.createElement('td');
      outcomeCell.textContent = buildOutcomeText(mappingById[fn.id]);

      const toggleCell = document.createElement('td');
      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'btn btn-small';
      toggleBtn.textContent = 'Details';
      const detailId = `details-${fn.id}`;
      toggleBtn.setAttribute('aria-expanded', 'false');
      toggleBtn.setAttribute('aria-controls', detailId);
      toggleCell.appendChild(toggleBtn);

      row.appendChild(categoryCell);
      row.appendChild(nameCell);
      row.appendChild(shortCell);
      row.appendChild(outcomeCell);
      row.appendChild(toggleCell);

      const detailsRow = document.createElement('tr');
      detailsRow.id = detailId;
      detailsRow.className = 'details-row';
      detailsRow.hidden = true;

      const detailsCell = document.createElement('td');
      detailsCell.colSpan = 5;
      detailsCell.appendChild(buildDetails(fn));
      detailsRow.appendChild(detailsCell);

      toggleBtn.addEventListener('click', () => {
        const isOpen = !detailsRow.hidden;
        detailsRow.hidden = isOpen;
        toggleBtn.setAttribute('aria-expanded', String(!isOpen));
      });

      tableBody.appendChild(row);
      tableBody.appendChild(detailsRow);
    });
  };

  const init = async () => {
    try {
      const [functionsList, mappingList] = await Promise.all([
        fetch(dataUrl).then((r) => r.json()),
        fetch(mappingUrl).then((r) => r.json())
      ]);

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

      const mappingById = mappingList.reduce((acc, item) => {
        acc[item.id] = item;
        return acc;
      }, {});

      const controls = document.createElement('div');
      controls.className = 'functions-controls';

      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search functions';
      search.setAttribute('aria-label', 'Search functions');

      const category = document.createElement('select');
      category.setAttribute('aria-label', 'Filter by category');
      const categories = Array.from(new Set(functionsList.map((fn) => fn.category)));
      const allOption = document.createElement('option');
      allOption.value = 'all';
      allOption.textContent = 'All categories';
      category.appendChild(allOption);
      categories.forEach((cat) => {
        const option = document.createElement('option');
        option.value = cat;
        option.textContent = cat;
        category.appendChild(option);
      });

      controls.appendChild(search);
      controls.appendChild(category);

      const table = document.createElement('table');
      table.className = 'functions-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr><th>Category</th><th>Function</th><th>Description</th><th>Outcomes</th><th></th></tr>';
      const tbody = document.createElement('tbody');
      table.appendChild(thead);
      table.appendChild(tbody);

      const filterAndRender = () => {
        const term = search.value.trim().toLowerCase();
        const cat = category.value;
        const filtered = functionsList.filter((fn) => {
          const matchesText =
            fn.name.toLowerCase().includes(term) ||
            fn.short_description.toLowerCase().includes(term) ||
            fn.long_description.toLowerCase().includes(term);
          const matchesCategory = cat === 'all' || fn.category === cat;
          return matchesText && matchesCategory;
        });
        renderTable(filtered, mappingById, tbody);
      };

      search.addEventListener('input', filterAndRender);
      category.addEventListener('change', filterAndRender);

      ui.appendChild(controls);
      ui.appendChild(table);
      filterAndRender();
    } catch (error) {
      if (ui) {
        ui.textContent = 'Functions explorer failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
