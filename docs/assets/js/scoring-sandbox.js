(() => {
  // Compute outcome indices and overall condition from function scores.
  const container = document.querySelector('.scoring-sandbox');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const functionsUrl = `${baseUrl}/assets/data/functions.json`;
  const mappingUrl = `${baseUrl}/assets/data/cwa-mapping.json`;
  const exampleUrl = `${baseUrl}/assets/data/scoring-example.json`;
  const fallback = container.querySelector('.scoring-sandbox-fallback');
  const ui = container.querySelector('.scoring-sandbox-ui');

  const weightFromCode = (code) => {
    if (code === 'D') {
      return 1.0;
    }
    if (code === 'i') {
      return 0.1;
    }
    return 0.0;
  };

  const weightLabelFromCode = (code) => {
    if (code === 'D') {
      return 'D';
    }
    if (code === 'i') {
      return 'i';
    }
    return '-';
  };

  const slugCategory = (category) =>
    `category-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  const init = async () => {
    try {
      const [functionsList, mappingList, exampleList] = await Promise.all([
        fetch(functionsUrl).then((r) => r.json()),
        fetch(mappingUrl).then((r) => r.json()),
        fetch(exampleUrl).then((r) => r.json())
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

      const sampleScores = exampleList.reduce((acc, item) => {
        acc[item.function_id] = item.score;
        return acc;
      }, {});

      const table = document.createElement('table');
      table.className = 'scoring-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr><th>Discipline</th><th>Function</th><th>Score (0-15)</th><th>Physical</th><th>Chemical</th><th>Biological</th></tr>';
      const tbody = document.createElement('tbody');
      const tfoot = document.createElement('tfoot');
      table.appendChild(thead);
      table.appendChild(tbody);
      table.appendChild(tfoot);

      const inputs = new Map();
      const summaryRows = {
        physical: [],
        chemical: [],
        biological: []
      };

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
        const mapping = mappingById[fn.id] || { physical: '-', chemical: '-', biological: '-' };
        const row = document.createElement('tr');
        row.classList.add(slugCategory(fn.category));

        if (spans[index] > 0) {
          const disciplineCell = document.createElement('td');
          disciplineCell.textContent = fn.category;
          disciplineCell.rowSpan = spans[index];
          disciplineCell.className = 'discipline-cell';
          row.appendChild(disciplineCell);
        }

        const nameCell = document.createElement('td');
        nameCell.textContent = fn.name;

        const scoreCell = document.createElement('td');
        const scoreWrap = document.createElement('div');
        scoreWrap.className = 'score-input';
        const input = document.createElement('input');
        input.type = 'range';
        input.min = '0';
        input.max = '15';
        input.step = '1';
        input.value = sampleScores[fn.id] !== undefined ? sampleScores[fn.id] : 0;
        input.disabled = true;
        input.setAttribute('aria-label', `${fn.name} score`);
        const value = document.createElement('span');
        value.className = 'score-value';
        value.textContent = input.value;
        scoreWrap.appendChild(input);
        scoreWrap.appendChild(value);
        scoreCell.appendChild(scoreWrap);

        const physicalCell = document.createElement('td');
        const chemicalCell = document.createElement('td');
        const biologicalCell = document.createElement('td');
        physicalCell.className = 'weight-cell';
        chemicalCell.className = 'weight-cell';
        biologicalCell.className = 'weight-cell';

        const pWeight = weightFromCode(mapping.physical);
        const cWeight = weightFromCode(mapping.chemical);
        const bWeight = weightFromCode(mapping.biological);

        physicalCell.textContent = weightLabelFromCode(mapping.physical);
        chemicalCell.textContent = weightLabelFromCode(mapping.chemical);
        biologicalCell.textContent = weightLabelFromCode(mapping.biological);

        row.appendChild(nameCell);
        row.appendChild(scoreCell);
        row.appendChild(physicalCell);
        row.appendChild(chemicalCell);
        row.appendChild(biologicalCell);

        tbody.appendChild(row);
        inputs.set(fn.id, { input, value, weights: { p: pWeight, c: cWeight, b: bWeight } });
      });

      const labelItems = [
        'Weighted Score Total',
        'Direct Effect',
        'Indirect Effect',
        'Max Score',
        'Condition Sub-Index',
        'Ecosystem Condition Index'
      ];

      const buildSummaryStack = (store, count) => {
        const stack = document.createElement('div');
        stack.className = 'summary-stack';
        for (let i = 0; i < count; i += 1) {
          const div = document.createElement('div');
          div.textContent = '-';
          store.push(div);
          stack.appendChild(div);
        }
        return stack;
      };

      for (let i = 0; i < labelItems.length; i += 1) {
        const row = document.createElement('tr');
        const labelCell = document.createElement('td');
        labelCell.colSpan = 3;
        labelCell.className = 'summary-labels';
        labelCell.textContent = labelItems[i];
        row.appendChild(labelCell);

        if (labelItems[i] === 'Ecosystem Condition Index') {
          const mergedCell = document.createElement('td');
          mergedCell.colSpan = 3;
          mergedCell.className = 'summary-values summary-merged';
          const stack = buildSummaryStack(summaryRows.chemical, 1);
          mergedCell.appendChild(stack);
          row.appendChild(mergedCell);
        } else {
          const physicalCell = document.createElement('td');
          physicalCell.className = 'summary-values';
          const chemicalCell = document.createElement('td');
          chemicalCell.className = 'summary-values';
          const biologicalCell = document.createElement('td');
          biologicalCell.className = 'summary-values';

          const physicalStack = buildSummaryStack(summaryRows.physical, 1);
          const chemicalStack = buildSummaryStack(summaryRows.chemical, 1);
          const biologicalStack = buildSummaryStack(summaryRows.biological, 1);

          physicalCell.appendChild(physicalStack);
          chemicalCell.appendChild(chemicalStack);
          biologicalCell.appendChild(biologicalStack);

          row.appendChild(physicalCell);
          row.appendChild(chemicalCell);
          row.appendChild(biologicalCell);
        }

        tfoot.appendChild(row);
      }

      const summary = document.createElement('div');
      summary.className = 'outcome-summary';

      summary.textContent = 'Direct mapping (1.0), Indirect Mapping (0.1).';

      const controls = document.createElement('div');
      controls.className = 'button-row';

      const editBtn = document.createElement('button');
      editBtn.type = 'button';
      editBtn.className = 'btn';
      editBtn.textContent = 'Try it out / Enable editing';
      editBtn.setAttribute('aria-pressed', 'false');

      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.className = 'btn';
      resetBtn.textContent = 'Reset to sample';

      controls.appendChild(editBtn);
      controls.appendChild(resetBtn);

      const updateScores = () => {
        let pSum = 0;
        let pWeighted = 0;
        let cSum = 0;
        let cWeighted = 0;
        let bSum = 0;
        let bWeighted = 0;
        let pDirect = 0;
        let pIndirect = 0;
        let cDirect = 0;
        let cIndirect = 0;
        let bDirect = 0;
        let bIndirect = 0;

        inputs.forEach(({ input, weights }) => {
          const score = parseFloat(input.value) || 0;
          pSum += weights.p;
          pWeighted += score * weights.p;
          cSum += weights.c;
          cWeighted += score * weights.c;
          bSum += weights.b;
          bWeighted += score * weights.b;
          if (weights.p === 1.0) {
            pDirect += 1;
          } else if (weights.p === 0.1) {
            pIndirect += 1;
          }
          if (weights.c === 1.0) {
            cDirect += 1;
          } else if (weights.c === 0.1) {
            cIndirect += 1;
          }
          if (weights.b === 1.0) {
            bDirect += 1;
          } else if (weights.b === 0.1) {
            bIndirect += 1;
          }
        });

        const physicalScore = pSum > 0 ? pWeighted / pSum : 0;
        const chemicalScore = cSum > 0 ? cWeighted / cSum : 0;
        const biologicalScore = bSum > 0 ? bWeighted / bSum : 0;

        const toIndex = (value) => Math.min(1, Math.max(0, value / 15));
        const physicalIndex = toIndex(physicalScore);
        const chemicalIndex = toIndex(chemicalScore);
        const biologicalIndex = toIndex(biologicalScore);
        const ecosystemCondition = (physicalIndex + chemicalIndex + biologicalIndex) / 3;

        const formatNumber = (value) => value.toFixed(2);
        const formatCount = (value) => String(value);

        summaryRows.physical[0].textContent = formatNumber(pWeighted);
        summaryRows.physical[1].textContent = formatCount(pDirect);
        summaryRows.physical[2].textContent = formatCount(pIndirect);
        summaryRows.physical[3].textContent = formatNumber(pSum);
        summaryRows.physical[4].textContent = formatNumber(physicalIndex);

        summaryRows.chemical[0].textContent = formatNumber(cWeighted);
        summaryRows.chemical[1].textContent = formatCount(cDirect);
        summaryRows.chemical[2].textContent = formatCount(cIndirect);
        summaryRows.chemical[3].textContent = formatNumber(cSum);
        summaryRows.chemical[4].textContent = formatNumber(chemicalIndex);
        summaryRows.chemical[5].textContent = formatNumber(ecosystemCondition);

        summaryRows.biological[0].textContent = formatNumber(bWeighted);
        summaryRows.biological[1].textContent = formatCount(bDirect);
        summaryRows.biological[2].textContent = formatCount(bIndirect);
        summaryRows.biological[3].textContent = formatNumber(bSum);
        summaryRows.biological[4].textContent = formatNumber(biologicalIndex);
      };

      inputs.forEach(({ input, value }) => {
        input.addEventListener('input', () => {
          value.textContent = input.value;
          updateScores();
        });
      });

      editBtn.addEventListener('click', () => {
        const isEnabled = editBtn.getAttribute('aria-pressed') === 'true';
        const nextState = !isEnabled;
        editBtn.setAttribute('aria-pressed', String(nextState));
        editBtn.textContent = nextState ? 'Editing enabled' : 'Try it out / Enable editing';
        inputs.forEach(({ input }) => {
          input.disabled = !nextState;
        });
      });

      resetBtn.addEventListener('click', () => {
        inputs.forEach(({ input, value }, id) => {
          if (sampleScores[id] !== undefined) {
            input.value = sampleScores[id];
          } else {
            input.value = 0;
          }
          value.textContent = input.value;
        });
        updateScores();
      });

      ui.appendChild(controls);
      ui.appendChild(table);
      ui.appendChild(summary);

      updateScores();
    } catch (error) {
      if (ui) {
        ui.textContent = 'Scoring sandbox failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
