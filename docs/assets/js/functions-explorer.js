(() => {
  // Build the interactive functions table with search, filter, and details.
  const container = document.querySelector('.functions-explorer');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const dataUrl = `${baseUrl}/assets/data/functions.json`;
  const fallback = container.querySelector('.functions-explorer-fallback');
  const ui = container.querySelector('.functions-explorer-ui');

  const slugCategory = (category) =>
    `category-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

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

    wrap.appendChild(desc);
    wrap.appendChild(metrics);

    return wrap;
  };

  const updateCategoryRowSpans = (tableBody) => {
    const rows = Array.from(tableBody.querySelectorAll('tr'));
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      const row = rows[rowIndex];
      const categoryCell = row.querySelector('.category-cell');
      if (!categoryCell) {
        continue;
      }

      let span = 0;
      for (let i = rowIndex; i < rows.length; i += 1) {
        if (i > rowIndex && rows[i].querySelector('.category-cell')) {
          break;
        }
        if (!rows[i].hidden) {
          span += 1;
        }
      }

      categoryCell.rowSpan = Math.max(span, 1);
    }
  };

  const renderTable = (functionsList, tableBody) => {
    tableBody.innerHTML = '';

    const spans = new Array(functionsList.length).fill(0);
    let i = 0;
    while (i < functionsList.length) {
      const category = functionsList[i].category;
      let j = i + 1;
      while (j < functionsList.length && functionsList[j].category === category) {
        j += 1;
      }
      spans[i] = j - i;
      i = j;
    }

    functionsList.forEach((fn, index) => {
      const row = document.createElement('tr');
      const categoryClass = slugCategory(fn.category);
      row.classList.add(categoryClass);

      if (spans[index] > 0) {
        const categoryCell = document.createElement('td');
        categoryCell.textContent = fn.category;
        categoryCell.rowSpan = spans[index];
        categoryCell.classList.add('category-cell');
        row.appendChild(categoryCell);
      }

      const nameCell = document.createElement('td');
      nameCell.textContent = fn.name;

      const shortCell = document.createElement('td');
      const shortText = document.createElement('span');
      shortText.className = 'description-text';
      shortText.textContent = fn.short_description;

      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'details-caret';
      toggleBtn.textContent = '▾';
      const detailId = `details-${fn.id}`;
      toggleBtn.setAttribute('aria-expanded', 'false');
      toggleBtn.setAttribute('aria-controls', detailId);
      toggleBtn.setAttribute('aria-label', 'Toggle details');
      toggleBtn.setAttribute('title', 'Toggle details');

      shortCell.appendChild(shortText);
      shortCell.appendChild(toggleBtn);

      row.appendChild(nameCell);
      row.appendChild(shortCell);

      const detailsRow = document.createElement('tr');
      detailsRow.id = detailId;
      detailsRow.className = 'details-row';
      detailsRow.classList.add(categoryClass);
      detailsRow.hidden = true;

      const detailsCell = document.createElement('td');
      detailsCell.colSpan = 2;
      detailsCell.appendChild(buildDetails(fn));
      detailsRow.appendChild(detailsCell);

      toggleBtn.addEventListener('click', () => {
        const isOpen = !detailsRow.hidden;
        detailsRow.hidden = isOpen;
        toggleBtn.setAttribute('aria-expanded', String(!isOpen));
        toggleBtn.textContent = isOpen ? '▾' : '▴';
        updateCategoryRowSpans(tableBody);
      });

      tableBody.appendChild(row);
      tableBody.appendChild(detailsRow);
    });

    updateCategoryRowSpans(tableBody);
  };

  const init = async () => {
    try {
      const functionsList = await fetch(dataUrl).then((r) => r.json());

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

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
      thead.innerHTML = '<tr><th>Category</th><th>Function</th><th>Description</th></tr>';
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
        renderTable(filtered, tbody);
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
