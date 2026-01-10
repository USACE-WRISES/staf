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
      thead.innerHTML = '<tr><th>Function</th><th>Score (0-15)</th><th>Physical weight</th><th>Chemical weight</th><th>Biological weight</th></tr>';
      const tbody = document.createElement('tbody');
      table.appendChild(thead);
      table.appendChild(tbody);

      const inputs = new Map();

      functionsList.forEach((fn) => {
        const mapping = mappingById[fn.id] || { physical: '-', chemical: '-', biological: '-' };
        const row = document.createElement('tr');

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

        const pWeight = weightFromCode(mapping.physical);
        const cWeight = weightFromCode(mapping.chemical);
        const bWeight = weightFromCode(mapping.biological);

        physicalCell.textContent = pWeight.toFixed(2);
        chemicalCell.textContent = cWeight.toFixed(2);
        biologicalCell.textContent = bWeight.toFixed(2);

        row.appendChild(nameCell);
        row.appendChild(scoreCell);
        row.appendChild(physicalCell);
        row.appendChild(chemicalCell);
        row.appendChild(biologicalCell);

        tbody.appendChild(row);
        inputs.set(fn.id, { input, value, weights: { p: pWeight, c: cWeight, b: bWeight } });
      });

      const summary = document.createElement('div');
      summary.className = 'outcome-summary';

      const physicalSummary = document.createElement('div');
      const chemicalSummary = document.createElement('div');
      const biologicalSummary = document.createElement('div');
      const overallSummary = document.createElement('div');

      summary.appendChild(physicalSummary);
      summary.appendChild(chemicalSummary);
      summary.appendChild(biologicalSummary);
      summary.appendChild(overallSummary);

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

        inputs.forEach(({ input, weights }) => {
          const score = parseFloat(input.value) || 0;
          pSum += weights.p;
          pWeighted += score * weights.p;
          cSum += weights.c;
          cWeighted += score * weights.c;
          bSum += weights.b;
          bWeighted += score * weights.b;
        });

        const physicalScore = pSum > 0 ? pWeighted / pSum : 0;
        const chemicalScore = cSum > 0 ? cWeighted / cSum : 0;
        const biologicalScore = bSum > 0 ? bWeighted / bSum : 0;

        const toIndex = (value) => Math.min(1, Math.max(0, value / 15));
        const physicalIndex = toIndex(physicalScore);
        const chemicalIndex = toIndex(chemicalScore);
        const biologicalIndex = toIndex(biologicalScore);
        const ecosystemCondition = (physicalIndex + chemicalIndex + biologicalIndex) / 3;

        physicalSummary.textContent = `Physical index: ${physicalIndex.toFixed(2)} (sum weights: ${pSum.toFixed(2)})`;
        chemicalSummary.textContent = `Chemical index: ${chemicalIndex.toFixed(2)} (sum weights: ${cSum.toFixed(2)})`;
        biologicalSummary.textContent = `Biological index: ${biologicalIndex.toFixed(2)} (sum weights: ${bSum.toFixed(2)})`;
        overallSummary.textContent = `Ecosystem condition index: ${ecosystemCondition.toFixed(2)}`;
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
