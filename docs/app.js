(function () {
  'use strict';

  const SCHEMA = 'net-positive-tweets/1';
  const RUBRIC_VERSION = '1';
  const MAX_PEOPLE = 8;
  const MAX_FILE_BYTES = 1000000;
  const OFFSET_WARNING = 0.3;
  const BATCH_OFFSET_WARNING = 0.5;
  const WITHIN_ONE_WARNING = 0.85;
  const THIN_YEAR = 50;
  const COLORS = {
    light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
    dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767']
  };

  const people = [];
  const charts = [];
  let view = 'points';

  const byId = (id) => document.getElementById(id);
  const isDark = () => matchMedia('(prefers-color-scheme: dark)').matches;
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const int = (v) => Math.round(v).toLocaleString('en-US');
  const signedInt = (v) => (v > 0 ? '+' : v < 0 ? '-' : '') + int(Math.abs(v));
  const signed2 = (v) => (v > 0 ? '+' : v < 0 ? '-' : '') + Math.abs(v).toFixed(2);
  const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0) + '%';

  // Every value from a file goes through textContent or a canvas, never through HTML.
  function h(tag, options, children) {
    const e = document.createElement(tag);
    const o = options || {};
    if (o.class) e.className = o.class;
    if (o.text !== undefined) e.textContent = o.text;
    if (o.title) e.title = o.title;
    if (o.color) e.style.background = o.color;
    if (o.ring) e.style.borderColor = o.ring;
    if (o.width) e.style.width = o.width;
    Object.entries(o.attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
    (children || []).forEach((c) => e.appendChild(c));
    return e;
  }

  function validate(raw) {
    if (!raw || typeof raw !== 'object' || raw.schema !== SCHEMA) {
      throw new Error('this is not a summary.json from this tool.');
    }
    const count = (v, what) => {
      if (!Number.isInteger(v) || v < 0 || v > 1e9) throw new Error('bad number for ' + what + '.');
      return v;
    };
    if (!Array.isArray(raw.years) || raw.years.length === 0 || raw.years.length > 60) {
      throw new Error('it has no usable list of years.');
    }
    const seen = new Set();
    const years = raw.years.map((y) => {
      if (!y || !Number.isInteger(y.year) || y.year < 2006 || y.year > 2100 || seen.has(y.year)) {
        throw new Error('it has a bad year.');
      }
      seen.add(y.year);
      const row = {
        year: y.year,
        tweets: count(y.tweets, 'tweets'),
        archive: count(y.tweets_in_archive, 'tweets in archive'),
        pos: count(y.positive_tweets, 'positive tweets'),
        neu: count(y.neutral_tweets, 'neutral tweets'),
        neg: count(y.negative_tweets, 'negative tweets'),
        posPts: count(y.positive_points, 'positive points'),
        negPts: -count(-y.negative_points, 'negative points')
      };
      if (row.tweets === 0 || row.pos + row.neu + row.neg !== row.tweets || row.archive < row.tweets ||
          row.posPts > row.pos * 5 || row.posPts < row.pos || -row.negPts > row.neg * 5 || -row.negPts < row.neg) {
        throw new Error('the numbers for ' + y.year + ' do not add up.');
      }
      // A sampled run scores part of each year, so scale up to estimate the full year.
      row.scale = row.archive / row.tweets;
      return row;
    }).sort((a, b) => a.year - b.year);

    let calibration = null;
    if (raw.calibration && Number.isFinite(raw.calibration.mean_offset)) {
      const finite = (v, fallback) => (Number.isFinite(v) ? v : fallback);
      calibration = {
        offset: raw.calibration.mean_offset,
        batch: finite(raw.calibration.largest_batch_offset, 0),
        within: finite(raw.calibration.within_one, 1)
      };
    }
    return {
      name: String(raw.name || 'Unnamed').slice(0, 60),
      model: String(raw.model || 'unknown').slice(0, 60),
      rubric: String(raw.rubric_version || '').slice(0, 10),
      through: /^\d{4}-\d{2}-\d{2}$/.test(raw.through) ? raw.through : '',
      sampled: raw.sampled === true,
      calibration: calibration,
      years: years
    };
  }

  function totals(p) {
    const t = { tweets: 0, pos: 0, neu: 0, neg: 0, posPts: 0, negPts: 0 };
    p.years.forEach((y) => {
      t.tweets += y.archive;
      t.pos += y.pos * y.scale;
      t.neu += y.neu * y.scale;
      t.neg += y.neg * y.scale;
      t.posPts += y.posPts * y.scale;
      t.negPts += y.negPts * y.scale;
    });
    t.net = t.posPts + t.negPts;
    t.average = t.net / t.tweets;
    return t;
  }

  function addPerson(raw) {
    const data = validate(raw);
    const same = people.find((p) => p.data.name === data.name);
    if (same) {
      same.data = data;
      return;
    }
    if (people.length >= MAX_PEOPLE) throw new Error('this page compares up to ' + MAX_PEOPLE + ' people at once.');
    const used = new Set(people.map((p) => p.slot));
    let slot = 0;
    while (used.has(slot)) slot += 1;
    people.push({ data: data, slot: slot });
  }

  const personColor = (p) => COLORS[isDark() ? 'dark' : 'light'][p.slot];

  function renderTable() {
    const table = byId('totals');
    table.textContent = '';
    if (!people.length) return;
    const heads = ['Person', 'Tweets', 'Average', 'Positive points', 'Negative points', 'Net points', 'Positive tweets', 'Neutral tweets', 'Negative tweets', 'Reference offset'];
    table.appendChild(h('thead', {}, [h('tr', {}, heads.map((t) => h('th', { text: t, attrs: { scope: 'col' } })))]));
    const body = h('tbody');
    people.forEach((p) => {
      const t = totals(p.data);
      const remove = h('button', { class: 'remove', text: '×', attrs: { type: 'button', 'aria-label': 'Remove ' + p.data.name } });
      remove.addEventListener('click', () => {
        people.splice(people.indexOf(p), 1);
        render();
      });
      const est = p.data.sampled ? ' (est.)' : '';
      body.appendChild(h('tr', {}, [
        h('td', {}, [h('span', { class: 'person' }, [remove, h('span', { class: 'swatch', color: personColor(p) }), h('span', { class: 'person-name', text: p.data.name, title: p.data.name })])]),
        h('td', { text: int(t.tweets) }),
        h('td', { text: signed2(t.average) }),
        h('td', { text: signedInt(t.posPts) + est }),
        h('td', { text: signedInt(t.negPts) + est }),
        h('td', { text: signedInt(t.net) + est }),
        h('td', { text: int(t.pos) + ' (' + pct(t.pos, t.tweets) + ')' }),
        h('td', { text: int(t.neu) + ' (' + pct(t.neu, t.tweets) + ')' }),
        h('td', { text: int(t.neg) + ' (' + pct(t.neg, t.tweets) + ')' }),
        h('td', { text: p.data.calibration ? signed2(p.data.calibration.offset) : 'none' })
      ]));
    });
    table.appendChild(body);

    const warnings = byId('warnings');
    warnings.textContent = '';
    const add = (text) => warnings.appendChild(h('li', { text: text }));
    people.forEach((p) => {
      const n = p.data.name;
      const c = p.data.calibration;
      if (!c) add(n + ' has no reference check. The run did not score the reference tweets, so its scale is unconfirmed.');
      else {
        if (Math.abs(c.offset) > OFFSET_WARNING) add(n + ' scored the reference tweets ' + signed2(c.offset) + ' points away from their fixed scores on average, so compare with care.');
        if (Math.abs(c.batch) > BATCH_OFFSET_WARNING) add('One batch in the run for ' + n + ' landed ' + signed2(c.batch) + ' points from the reference scores, so its scale may have drifted.');
        if (c.within < WITHIN_ONE_WARNING) add('Only ' + Math.round(c.within * 100) + '% of reference scores for ' + n + ' landed within 1 point, so compare with care.');
      }
      if (p.data.sampled) add(n + ' scored a sample. Totals are estimates. Averages and shares are comparable.');
      if (p.data.rubric !== RUBRIC_VERSION) add(n + ' used a different rubric version (' + (p.data.rubric || 'unknown') + ', current is ' + RUBRIC_VERSION + '), so scores are not directly comparable.');
    });
    // "Claude Opus 5.5" and "claude-opus-5-5" count as the same model.
    const modelKey = (m) => m.toLowerCase().replace(/[^a-z0-9]/g, '');
    const models = new Map(people.map((p) => [modelKey(p.data.model), p.data.model]));
    if (models.size > 1) add('These runs used different models (' + Array.from(models.values()).join(', ') + '), so differences may come from the models.');
  }

  function bandLegend(el, bands) {
    el.textContent = '';
    bands.forEach(([cls, text]) => {
      el.appendChild(h('span', {}, [h('span', { class: 'swatch ' + cls }), h('span', { text: text })]));
    });
  }

  function renderShare() {
    bandLegend(byId('share-legend'), [['band-positive', 'Positive'], ['band-neutral', 'Neutral'], ['band-negative', 'Negative']]);
    const box = byId('share');
    box.textContent = '';
    people.forEach((p) => {
      const t = totals(p.data);
      const seg = (cls, value, label) => h('span', { class: cls, width: (value / t.tweets) * 100 + '%', title: label + ': ' + int(value) + ' tweets (' + pct(value, t.tweets) + ')' });
      box.appendChild(h('div', { class: 'share-row' }, [
        h('div', { class: 'share-name', text: p.data.name, title: p.data.name }),
        h('div', { class: 'share-bar', attrs: { role: 'img', 'aria-label': p.data.name + ': ' + pct(t.pos, t.tweets) + ' positive, ' + pct(t.neu, t.tweets) + ' neutral, ' + pct(t.neg, t.tweets) + ' negative' } }, [
          seg('band-positive', t.pos, 'Positive'), seg('band-neutral', t.neu, 'Neutral'), seg('band-negative', t.neg, 'Negative')
        ])
      ]));
    });
  }

  function chartTheme() {
    const dark = isDark();
    return {
      ink: token('--ink'),
      muted: token('--muted'),
      surface: token('--surface'),
      grid: dark ? '#2c2c2a' : '#e1e0d9',
      base: dark ? '#55544f' : '#c3c2b7',
      tipBg: dark ? '#2c2c2a' : '#0b0b0b'
    };
  }

  function yearRange() {
    const all = people.flatMap((p) => p.data.years.map((y) => y.year));
    const out = [];
    for (let y = Math.min.apply(null, all); y <= Math.max.apply(null, all); y += 1) out.push(y);
    return out;
  }

  function niceStep(x) {
    if (x <= 0) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(x)));
    const m = [1, 2, 2.5, 5, 10].find((f) => f * power >= x);
    return Math.max(1, m * power);
  }

  const baseScales = (theme) => ({
    x: { border: { display: false }, grid: { display: false }, offset: true, ticks: { autoSkip: false, maxRotation: 0, color: theme.muted } },
    y: { border: { display: false }, afterFit: (s) => { s.width = 52; }, grid: { drawTicks: false, color: (c) => (c.tick.value === 0 ? theme.base : theme.grid) } }
  });
  const tooltipBase = (theme) => ({ backgroundColor: theme.tipBg, titleColor: '#f0efec', bodyColor: '#f0efec', footerColor: '#f0efec', padding: 10, cornerRadius: 8 });

  function renderAverage(theme) {
    const legend = byId('average-legend');
    legend.textContent = '';
    people.forEach((p) => legend.appendChild(h('span', {}, [h('span', { class: 'swatch', color: personColor(p) }), h('span', { text: p.data.name })])));
    legend.appendChild(h('span', {}, [h('span', { class: 'swatch ring', ring: theme.muted }), h('span', { text: 'Hollow point: under ' + THIN_YEAR + ' tweets scored that year, less reliable' })]));

    const years = yearRange();
    const averages = people.flatMap((p) => p.data.years.map((y) => (y.posPts + y.negPts) / y.tweets));
    const step = 0.4;
    const max = Math.max(1.6, Math.ceil(Math.max.apply(null, averages) / step) * step);
    const min = Math.min(0, Math.floor(Math.min.apply(null, averages) / step) * step);
    const label = people.length <= 4;

    const endLabels = {
      id: 'endLabels',
      afterDatasetsDraw(chart) {
        if (!label) return;
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = '500 12px ' + getComputedStyle(document.body).fontFamily;
        ctx.fillStyle = theme.ink;
        ctx.textBaseline = 'middle';
        const placed = [];
        chart.data.datasets.forEach((d, i) => {
          const points = chart.getDatasetMeta(i).data;
          let last = d.data.length - 1;
          while (last >= 0 && d.data[last] === null) last -= 1;
          if (last < 0) return;
          const text = d.label.length > 14 ? d.label.slice(0, 13) + '…' : d.label;
          placed.push({ text: text, x: points[last].x + 9, y: points[last].y });
        });
        placed.sort((a, b) => a.y - b.y);
        placed.forEach((p, i) => {
          if (i > 0 && p.y - placed[i - 1].y < 14) p.y = placed[i - 1].y + 14;
          ctx.fillText(p.text, p.x, p.y);
        });
        ctx.restore();
      }
    };

    charts.push(new Chart(byId('average'), {
      type: 'line',
      plugins: [endLabels],
      data: {
        labels: years.map(String),
        datasets: people.map((p) => {
          const lookup = new Map(p.data.years.map((y) => [y.year, y]));
          const color = personColor(p);
          const rows = years.map((y) => lookup.get(y));
          return {
            label: p.data.name,
            data: rows.map((y) => (y ? (y.posPts + y.negPts) / y.tweets : null)),
            borderColor: color,
            borderWidth: 2,
            borderCapStyle: 'round',
            borderJoinStyle: 'round',
            tension: 0,
            spanGaps: true,
            pointRadius: 5,
            pointHoverRadius: 6,
            pointBorderWidth: 2,
            pointBackgroundColor: rows.map((y) => (y && y.tweets < THIN_YEAR ? theme.surface : color)),
            pointBorderColor: rows.map((y) => (y && y.tweets < THIN_YEAR ? color : theme.surface)),
            npRows: rows
          };
        })
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        layout: { padding: { right: label ? 96 : 8 } },
        scales: Object.assign(baseScales(theme), {
          y: Object.assign(baseScales(theme).y, { min: min, max: max, ticks: { stepSize: step, padding: 8, color: theme.muted, callback: (v) => (v === 0 ? '0' : (v > 0 ? '+' : '-') + Math.abs(v).toFixed(1)) } })
        }),
        plugins: {
          legend: { display: false },
          tooltip: Object.assign(tooltipBase(theme), {
            callbacks: {
              label: (item) => {
                const row = item.dataset.npRows[item.dataIndex];
                return item.dataset.label + ': ' + signed2(item.raw) + ' (' + int(row.tweets) + ' tweets)';
              }
            }
          })
        }
      }
    }));
  }

  function yearlySeries(row) {
    const s = row ? row.scale : 1;
    if (!row) return { pos: 0, neu: 0, neg: 0, total: 0 };
    if (view === 'points') return { pos: row.posPts * s, neu: 0, neg: row.negPts * s, total: (row.posPts + row.negPts) * s };
    if (view === 'tweets') return { pos: row.pos * s, neu: row.neu * s, neg: row.neg * s, total: row.archive };
    return { pos: (row.pos / row.tweets) * 100, neu: (row.neu / row.tweets) * 100, neg: (row.neg / row.tweets) * 100, total: 100 };
  }

  function renderYearly(theme) {
    const positive = token('--positive');
    const neutral = token('--neutral');
    const negative = token('--negative');
    const legendBands = view === 'points'
      ? [['band-positive', 'Points from positive tweets'], ['band-negative', 'Points from negative tweets']]
      : [['band-positive', 'Positive tweets'], ['band-neutral', 'Neutral tweets'], ['band-negative', 'Negative tweets']];
    bandLegend(byId('yearly-legend'), legendBands);
    if (view === 'points') byId('yearly-legend').appendChild(h('span', { text: 'Labels show the net total. All people share one scale.' }));
    else if (view === 'tweets') byId('yearly-legend').appendChild(h('span', { text: 'All people share one scale.' }));

    const box = byId('yearly');
    box.textContent = '';
    const years = yearRange();
    let top = 0;
    let bottom = 0;
    people.forEach((p) => p.data.years.forEach((y) => {
      const s = yearlySeries(y);
      top = Math.max(top, view === 'points' ? s.pos : s.pos + s.neu + s.neg);
      bottom = Math.min(bottom, view === 'points' ? s.neg : 0);
    }));
    const step = view === 'share' ? 25 : niceStep(Math.max(top, -bottom) / 5);
    const max = view === 'share' ? 100 : Math.ceil((top * 1.1) / step) * step;
    const min = bottom < 0 ? -Math.ceil(-bottom / step) * step : 0;

    people.forEach((p) => {
      const canvas = h('canvas', { attrs: { role: 'img', 'aria-label': 'Column chart of ' + view + ' per year for ' + p.data.name + '. The totals table above has the summary numbers.' } });
      const through = p.data.through ? ' (data through ' + p.data.through + ')' : '';
      box.appendChild(h('div', { class: 'person-chart' }, [h('h3', { text: p.data.name + through }), h('div', { class: 'chart' }, [canvas])]));

      const lookup = new Map(p.data.years.map((y) => [y.year, y]));
      const series = years.map((y) => yearlySeries(lookup.get(y)));
      const highest = series.reduce((best, s, i) => (s.total > series[best].total ? i : best), 0);
      let latest = -1;
      years.forEach((y, i) => { if (lookup.has(y)) latest = i; });
      const labelled = view === 'share' ? [] : Array.from(new Set([highest, latest]));

      const labels = {
        id: 'barLabels',
        afterDatasetsDraw(chart) {
          const ctx = chart.ctx;
          const meta = chart.getDatasetMeta(0).data;
          const yScale = chart.scales.y;
          ctx.save();
          ctx.font = '500 12px ' + getComputedStyle(document.body).fontFamily;
          ctx.fillStyle = theme.ink;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'bottom';
          labelled.forEach((i) => {
            const s = series[i];
            const height = view === 'points' ? s.pos : s.pos + s.neu + s.neg;
            const text = view === 'points' ? signedInt(s.total) : int(s.total);
            ctx.fillText(text, meta[i].x, yScale.getPixelForValue(height) - 5);
          });
          ctx.restore();
        }
      };

      const seg = (label, key, color, roundTop, roundBottom, gap) => ({
        label: label,
        data: series.map((s) => s[key]),
        backgroundColor: color,
        maxBarThickness: 24,
        borderSkipped: false,
        borderColor: theme.surface,
        borderWidth: gap ? { top: 2, right: 0, bottom: 0, left: 0 } : 0,
        borderRadius: { topLeft: roundTop ? 4 : 0, topRight: roundTop ? 4 : 0, bottomLeft: roundBottom ? 4 : 0, bottomRight: roundBottom ? 4 : 0 }
      });
      const datasets = view === 'points'
        ? [seg('Positive points', 'pos', positive, true, false, false), seg('Negative points', 'neg', negative, false, true, false)]
        : [seg('Positive', 'pos', positive, false, false, false), seg('Neutral', 'neu', neutral, false, false, true), seg('Negative', 'neg', negative, true, false, true)];

      const format = (v) => (view === 'share' ? Math.round(v) + '%' : view === 'points' ? signedInt(v) : int(v));
      charts.push(new Chart(canvas, {
        type: 'bar',
        plugins: [labels],
        data: { labels: years.map(String), datasets: datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { mode: 'index', intersect: false },
          scales: Object.assign(baseScales(theme), {
            x: Object.assign(baseScales(theme).x, { stacked: true }),
            y: Object.assign(baseScales(theme).y, { stacked: true, min: min, max: max, ticks: { stepSize: step, padding: 8, color: theme.muted, callback: format } })
          }),
          plugins: {
            legend: { display: false },
            tooltip: Object.assign(tooltipBase(theme), {
              displayColors: false,
              callbacks: {
                label: (item) => item.dataset.label + ': ' + format(item.raw),
                footer: (items) => {
                  const row = lookup.get(years[items[0].dataIndex]);
                  if (!row) return 'No tweets';
                  const net = (row.posPts + row.negPts) * row.scale;
                  return 'Net ' + signedInt(net) + ' points, ' + int(row.archive) + ' tweets';
                }
              }
            })
          }
        }
      }));
    });
  }

  function render() {
    charts.splice(0).forEach((c) => c.destroy());
    renderTable();
    renderShare();
    const canvasReady = typeof window.Chart !== 'undefined';
    const hasPeople = people.length > 0;
    document.querySelectorAll('section').forEach((s, i) => { s.hidden = !hasPeople && i > 0; });
    if (!hasPeople) {
      byId('totals').appendChild(h('tbody', {}, [h('tr', {}, [h('td', { class: 'empty', text: 'Add a summary.json file to begin.' })])]));
      byId('warnings').textContent = '';
      return;
    }
    if (!canvasReady) {
      showErrors(['The chart library could not load, so only the tables are showing. Check your internet connection.']);
      return;
    }
    const theme = chartTheme();
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 11;
    renderAverage(theme);
    renderYearly(theme);
  }

  function showErrors(lines) {
    byId('errors').textContent = lines.join('\n');
  }

  async function addFiles(files) {
    const errors = [];
    for (const file of Array.from(files)) {
      try {
        if (file.size > MAX_FILE_BYTES) throw new Error('it is too large to be a summary file.');
        let parsed;
        try {
          parsed = JSON.parse(await file.text());
        } catch (e) {
          throw new Error('it is not valid JSON.');
        }
        addPerson(parsed);
      } catch (e) {
        errors.push(file.name + ': ' + e.message);
      }
    }
    showErrors(errors);
    render();
    document.body.dataset.filesHandled = String(Number(document.body.dataset.filesHandled || 0) + 1);
  }

  byId('file').addEventListener('change', (e) => {
    addFiles(e.target.files);
    e.target.value = '';
  });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.addEventListener('click', () => {
      view = button.dataset.view;
      document.querySelectorAll('[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
      render();
    });
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);

  (window.NPT_EXAMPLES || []).forEach(addPerson);
  render();
})();
