'use strict';
const DIMENSIONS = {
  M1: 'Method and model', E1: 'Dataset and task coverage', E2: 'Baselines and comparison methods',
  E3: 'Evaluation protocol and experimental settings', E4: 'Ablations, robustness and supplementary analyses',
  E5: 'Statistics and uncertainty reporting', E6: 'Concrete results', T1: 'Theory, assumptions and proofs',
  C1: 'Core claims and contributions', C2: 'Interpretation and limitations',
  D1: 'Technical details and reproducibility information', R1: 'Related work and research positioning',
  P1: 'Language and document structure', P2: 'Figures, tables and layout',
};
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = value => typeof value === 'number' ? value.toLocaleString('en-US') : 'Not recorded';
const duration = value => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Not recorded';
  if (value < 60) return `${value.toFixed(1)} s`;
  if (value < 3600) return `${(value / 60).toFixed(1)} min`;
  return `${Math.floor(value / 3600)} h ${Math.round(value % 3600 / 60)} min`;
};
const date = (value, time = false) => {
  if (!value) return 'Timestamp unresolved';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  return parsed.toLocaleString('en-GB', {year:'numeric',month:'short',day:'2-digit',
    ...(time ? {hour:'2-digit',minute:'2-digit',second:'2-digit'} : {}),timeZone:'UTC'}) + (time ? ' UTC' : '');
};
const labelClass = label => ({'Major change':'major','minor change':'minor',maybe:'maybe',yes:'minor',no:'',
  uncertain:'maybe',comparable:'minor',partially_comparable:'maybe',unable_to_determine:'maybe',failed:'failed',
  completed:'minor',running:'maybe',stopped:'maybe'}[label] || '');
const badge = label => `<span class="badge ${labelClass(label)}">${escapeHTML(label ?? 'Not assessed')}</span>`;
let data, selected, activeTab = 'assessment';
let promptRequest = 0;

function sourceName(id) { return data.source_names[id] || id; }
function pairingScope(rule) {
  if (typeof rule === 'string') return rule;
  if (rule && typeof rule === 'object') {
    return rule.pair_scope || rule.description || 'Structured pairing rule; see Protocol for its full definition.';
  }
  return '';
}
function match(pair) {
  const query = $('search').value.toLowerCase().trim();
  if (query && !`${pair.title} ${pair.arxiv_id}`.toLowerCase().includes(query)) return false;
  if ($('source').value && !pair.source_ids.includes($('source').value)) return false;
  if ($('status').value && pair.status !== $('status').value) return false;
  const dim = $('dimension').value, label = $('label').value;
  if (label && (!pair.result || !(dim ? [dim] : data.dimensions).some(k => pair.result[k]?.change === label))) return false;
  if (dim && !pair.result?.[dim]) return false;
  return true;
}

function renderSummary() {
  const s = data.summary, run = data.run;
  const executions = data.pairs.map(p => p.execution);
  const models = [...new Set(executions.map(e => e.model).filter(Boolean))];
  const efforts = [...new Set(executions.map(e => e.reasoning_effort || e.effort).filter(Boolean))];
  const model = run.model || models.join(', ') || 'Model not recorded';
  const effort = run.reasoning_effort || run.effort || efforts.join(', ');
  const concurrency = run.concurrency ?? run.workers;
  $('run-tags').innerHTML = `<span class="tag model">${escapeHTML(model)}${effort ? ' · '+escapeHTML(effort) : ''}</span>` +
    `<span class="tag">${concurrency == null ? 'Concurrency not recorded' : 'Concurrency cap: '+escapeHTML(concurrency)}</span>`;
  $('metrics').innerHTML = [
    ['Pairs completed', `${fmt(s.completed)}<small>/ ${fmt(s.pairs)}</small>`, `${fmt(s.papers)} distinct papers`],
    ['Experiment wall time', duration(s.wall_time_seconds), 'Elapsed time for the run'],
    ['Median pair time', duration(s.p50_seconds), `${fmt(s.timed_pairs)} pairs with timing`],
    ['95th percentile', duration(s.p95_seconds), 'Pair duration · includes retries'],
    ['Longest pair', duration(s.max_seconds), 'Observed pair duration'],
  ].map(([title,value,sub]) => `<div class="metric"><div class="metric-label">${title}</div><div class="metric-value">${value}</div><div class="metric-sub">${sub}</div></div>`).join('');
  $('run-note').innerHTML = `<span><strong>${fmt(s.failed)}</strong> failed · <strong>${fmt(s.running)}</strong> running · <strong>${fmt(s.pending)}</strong> pending${s.stopped ? ` · <strong>${fmt(s.stopped)}</strong> stopped` : ''}${s.unavailable ? ` · <strong>${fmt(s.unavailable)}</strong> unavailable` : ''}</span>` +
    '<span>14 independent change dimensions. No aggregate substantive-change pass/fail label.</span>';
  if (data.input?.pairing_rule) {
    const scope = document.createElement('span');
    scope.textContent = `Pair scope: ${pairingScope(data.input.pairing_rule)}`;
    $('run-note').appendChild(scope);
  }
  const limited = data.pairs.filter(p => p.result && p.result.overall.A1.value !== 'comparable');
  if (limited.length) {
    const warning = document.createElement('span');
    warning.textContent = `${limited.length} comparison(s) report limited comparison material. Review A1 before interpreting their change labels.`;
    $('run-note').appendChild(warning);
  }
  $('build-date').textContent = `Snapshot built ${date(data.built_at_utc, true)}`;
}

function renderList() {
  const visible = data.pairs.filter(match);
  $('visible-count').textContent = `${visible.length} / ${data.pairs.length}`;
  if (!visible.length) {
    $('pair-list').innerHTML = '<div class="empty"><strong>No matching pairs</strong>Try another source, status, or change label.</div>';
    $('detail').innerHTML = '<div class="empty"><strong>No matching pairs</strong>Your active filters do not match any prepared pair.</div>';
    return;
  }
  if (!visible.some(p => p.id === selected)) selected = visible[0].id;
  $('pair-list').innerHTML = visible.map(p => `<button type="button" class="pair-button${p.id === selected ? ' active' : ''}" data-pair="${escapeHTML(p.id)}" aria-current="${p.id === selected ? 'true' : 'false'}">
    <div class="pair-top"><span class="pair-id">${escapeHTML(p.arxiv_id)}</span><span>v${p.before.version} → v${p.after.version}</span></div>
    <div class="pair-title">${escapeHTML(p.title)}</div>
    <div class="pair-bottom"><span><i class="status-dot ${p.status}" aria-hidden="true"></i>${escapeHTML(sourceName(p.assigned_source_id || p.selected_source || p.source_ids[0] || 'Unknown source'))}</span><span class="pair-time">${p.status === 'completed' ? duration(p.duration_seconds) : escapeHTML(p.status)}</span></div>
  </button>`).join('');
  $('pair-list').querySelectorAll('[data-pair]').forEach(button => button.addEventListener('click', () => {
    selected = button.dataset.pair;
    updateHash(); renderList();
  }));
  updateHash(); renderDetail();
}

function updateHash() {
  const params = new URLSearchParams();
  params.set('pair', selected);
  if (activeTab !== 'assessment') params.set('view', activeTab);
  ['source','status','dimension','label'].forEach(id => { if ($(id).value) params.set(id, $(id).value); });
  if ($('search').value) params.set('q', $('search').value);
  history.replaceState(null, '', '#'+params.toString());
}

function versionCard(v, side) {
  return `<div class="version-card ${side}"><div class="version-label"><strong>v${v.version}</strong>${side === 'before' ? 'Earlier version' : 'Later version'}</div>
    <div class="version-date" title="${escapeHTML(v.submitted_at || '')}">${escapeHTML(date(v.submitted_at))}</div>
    <div class="version-meta"><span>${v.pages == null ? 'Pages unresolved' : `${fmt(v.pages)} physical pages`}</span><a href="${escapeHTML(v.pdf_url)}" target="_blank" rel="noopener noreferrer">Original PDF ↗</a></div></div>`;
}

function unitTrajectory(pair) {
  const unit = pair.unit || {}, siblings = data.pairs.filter(p => p.arxiv_id === pair.arxiv_id);
  const counts = [
    unit.original_version_count == null ? 'Original version count not recorded' : `${fmt(unit.original_version_count)} original versions`,
    unit.selected_version_count == null ? 'Selected count not recorded' : `${fmt(unit.selected_version_count)} selected`,
    `${fmt(unit.pair_count ?? siblings.length)} comparisons`,
  ];
  const chain = unit.selected_version_ids?.length ? unit.selected_version_ids.map(v => `v${v}`).join(' → ') : 'Selected trajectory not recorded';
  return `<section class="unit-trajectory" aria-label="Selected manuscript trajectory"><div class="unit-heading"><span class="unit-label">SELECTED TRAJECTORY</span><span class="unit-counts">${counts.map(escapeHTML).join(' · ')}</span></div>
    <div class="unit-chain">${escapeHTML(chain)}</div>
    <div class="unit-pairs" aria-label="Other comparisons for this paper">${siblings.map(p => `<button type="button" class="unit-pair${p.id === pair.id ? ' active' : ''}" data-unit-pair="${escapeHTML(p.id)}" aria-current="${p.id === pair.id}" title="${escapeHTML(p.status)}"><i class="status-dot ${p.status}" aria-hidden="true"></i>v${p.before.version} → v${p.after.version}</button>`).join('')}</div>
  </section>`;
}

function renderDetail() {
  const pair = data.pairs.find(p => p.id === selected);
  if (!pair) return;
  const gap = pair.gap_seconds == null ? 'Unresolved' : `${(pair.gap_seconds / 86400).toFixed(1)} days`;
  $('detail').innerHTML = `<div class="paper-head"><div class="paper-kicker">${pair.source_ids.map(s => `<span class="source-pill">${escapeHTML(sourceName(s))}</span>`).join('')}
    <span>arXiv ${escapeHTML(pair.arxiv_id)}</span>${badge(pair.status)}</div><h2>${escapeHTML(pair.title)}</h2>
    <div class="paper-links"><a href="https://arxiv.org/abs/${escapeHTML(pair.arxiv_id)}" target="_blank" rel="noopener noreferrer">arXiv record ↗</a>
    <a href="${pair.before.arxiv_url}" target="_blank" rel="noopener noreferrer">v${pair.before.version} record ↗</a><a href="${pair.after.arxiv_url}" target="_blank" rel="noopener noreferrer">v${pair.after.version} record ↗</a>
    <a href="${escapeHTML(pair.raw_url)}" download>Download pair JSON ↓</a></div></div>
    ${unitTrajectory(pair)}
    <div class="version-compare">${versionCard(pair.before,'before')}<div class="gap"><div class="gap-arrow" aria-hidden="true">→</div><div class="gap-days" title="${pair.gap_seconds == null ? 'Timestamp unresolved' : pair.gap_seconds+' seconds'}">${gap}</div><div class="gap-note">selected gap</div></div>${versionCard(pair.after,'after')}</div>
    <div class="tabs" role="tablist" aria-label="Review views">${['assessment','protocol','execution'].map(tab => `<button type="button" class="tab${activeTab === tab ? ' active' : ''}" data-tab="${tab}" role="tab" aria-selected="${activeTab === tab}" aria-controls="tab-panel">${tab[0].toUpperCase()+tab.slice(1)}</button>`).join('')}</div>
    <div class="tab-content" id="tab-panel" role="tabpanel"></div>`;
  $('detail').querySelectorAll('[data-unit-pair]').forEach(button => button.addEventListener('click', () => {
    const next = data.pairs.find(p => p.id === button.dataset.unitPair);
    if (!next) return;
    if (!match(next)) ['status','dimension','label'].forEach(id => { $(id).value = ''; });
    if (!match(next)) { $('source').value = ''; $('search').value = ''; }
    selected = next.id; updateHash(); renderList();
  }));
  $('detail').querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
    activeTab = button.dataset.tab; updateHash(); renderDetail();
  }));
  if (activeTab === 'protocol') renderProtocol(pair);
  else if (activeTab === 'execution') renderExecution(pair);
  else renderAssessment(pair);
}

function renderAssessment(pair) {
  const result = pair.result;
  if (!result) {
    $('tab-panel').innerHTML = `<div class="empty"><strong>${pair.result_availability === 'stale_or_unverified' ? 'An existing result is stale or unverified' : pair.status === 'failed' || pair.status === 'unavailable' ? 'No current judgment available' : pair.status === 'running' ? 'This pair is running' : pair.status === 'stopped' ? 'This pair was stopped' : 'This pair has not completed'}</strong>Only valid results with a matching completed execution appear here. Open Execution for the recorded status and timing.</div>` +
      (pair.warning ? `<div class="notice error">${escapeHTML(pair.warning)}</div>` : '');
    return;
  }
  const overall = result.overall;
  const assessments = [
    ['A1 · Comparability', overall.A1],
    [`A2 · Original empirical results · v${pair.before.version}`, overall.A2.before],
    [`A2 · Original empirical results · v${pair.after.version}`, overall.A2.after],
  ];
  $('tab-panel').innerHTML = `<div class="section-heading"><h3>Evidence readiness</h3><small>Pair comparability and per-version results</small></div>
    <div class="assessment-grid">${assessments.map(([title,a]) => `<div class="assessment-card"><div class="assessment-title">${title}</div>${badge(a.value)}<p>${escapeHTML(a.justification)}</p></div>`).join('')}</div>
    <div class="section-heading"><h3>Change dimensions <small>· 14 assessments</small></h3><button type="button" class="text-button" id="expand-all">Expand all</button></div>
    <div class="dimension-overview">${data.dimensions.map(d => `<button type="button" class="dimension-chip ${labelClass(result[d].change)}" data-jump="${d}" title="${escapeHTML(DIMENSIONS[d]+': '+result[d].change)}">${d}<span>${({'Major change':'major','minor change':'minor',maybe:'?', 'no change':'—'})[result[d].change]}</span></button>`).join('')}</div>
    <div class="legend">${['Major change','minor change','maybe','no change'].map(label => `<span class="legend-item"><i class="legend-swatch ${labelClass(label)}"></i>${label}</span>`).join('')}</div>
    <div class="dimensions">${data.dimensions.map(d => `<details class="dimension" id="dim-${d}"${result[d].change === 'Major change' || $('dimension').value === d ? ' open' : ''}><summary><span class="dim-code">${d}</span><span class="dim-name">${escapeHTML(DIMENSIONS[d])}</span>${badge(result[d].change)}</summary><p>${escapeHTML(result[d].justification)}</p></details>`).join('')}</div>
    <div class="notice">These labels describe changes within individual dimensions. A long submission gap does not establish a substantive revision, and this protocol defines no aggregate pass/fail judgment.</div>`;
  $('expand-all').addEventListener('click', () => {
    const details = [...$('tab-panel').querySelectorAll('details.dimension')];
    const expand = !details.every(item => item.open);
    details.forEach(item => { item.open = expand; });
    $('expand-all').textContent = expand ? 'Collapse all' : 'Expand all';
  });
  $('tab-panel').querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => {
    const item = $('dim-'+button.dataset.jump); item.open = true; item.scrollIntoView({behavior:'smooth',block:'start'});
  }));
}

async function renderProtocol(pair) {
  const token = ++promptRequest;
  $('tab-panel').innerHTML = `<div class="section-heading"><h3>Assessment protocol</h3><small>English · Independent dimensions</small></div>
    <div class="notice">The original PDFs are the comparison inputs. The selected temporal gap is metadata, not evidence that the research changed. Full paper history is kept outside the isolated pair assessment.</div>
    ${data.input?.pairing_rule ? `<div class="notice"><strong>Input pairing rule:</strong> ${escapeHTML(pairingScope(data.input.pairing_rule))}</div>${typeof data.input.pairing_rule === 'object' ? `<details class="protocol-section"><summary>Exact input pairing rule</summary><pre class="protocol-text">${escapeHTML(JSON.stringify(data.input.pairing_rule,null,2))}</pre></details>` : ''}` : ''}
    <div class="protocol-section"><div class="section-heading"><h3>Actual prompt for this pair</h3>${pair.prompt_url ? `<a class="quiet-link" href="${pair.prompt_url}" download>Download prompt ↓</a>` : ''}</div>
    <pre class="protocol-text" id="actual-prompt">${pair.prompt_url ? 'Loading saved execution prompt…' : 'No hash-verified execution prompt is available for this pair. The current comparison template below is reference material, not proof of an execution.'}</pre></div>
    ${pair.prompt_verification && pair.prompt_verification !== 'verified' ? `<div class="notice warning">${escapeHTML(pair.prompt_verification)}</div>` : ''}
    ${pair.saved_prompt_sha256 ? `<div class="hash">Saved prompt SHA-256: ${escapeHTML(pair.saved_prompt_sha256)}</div>` : ''}
    <details class="protocol-section"><summary>Current rubric reference · A1, A2 and 14 dimensions</summary><pre class="protocol-text">${escapeHTML(data.rubric)}</pre></details>
    <details class="protocol-section"><summary>Current comparison template</summary><pre class="protocol-text">${escapeHTML(data.comparison_template)}</pre></details>`;
  if (pair.prompt_url) {
    try {
      const response = await fetch(pair.prompt_url);
      if (!response.ok) throw new Error('The saved prompt could not be loaded.');
      const prompt = await response.text();
      if (token === promptRequest && selected === pair.id && activeTab === 'protocol') $('actual-prompt').textContent = prompt;
    } catch (error) {
      if (token === promptRequest && selected === pair.id && activeTab === 'protocol') $('actual-prompt').textContent = 'The saved prompt could not be loaded. Reload the site or use its download link.';
    }
  }
}

function renderExecution(pair) {
  const e = pair.execution;
  const fields = [
    ['Result status', pair.status], ['Latest execution status', e.status || 'Not recorded'],
    ['Result availability', pair.result_availability || 'Not recorded'], ['Pair elapsed time', duration(pair.duration_seconds)],
    ['Requested model', e.model || data.run.model || 'Not recorded'],
    ['Requested reasoning effort', e.reasoning_effort || e.effort || 'Not recorded'],
    ['Document input', ({uploaded_original_pdf_files: 'Original PDFs uploaded through /files; referenced by file_id', original_pdf_files: 'Complete PDFs embedded as Base64', fulltext_plus_all_page_images: 'Extracted text and all rendered pages'})[e.input_mode] || 'Not recorded'],
    ['Returned model', e.returned_model || 'Not recorded'], ['Returned reasoning effort', e.returned_effort || 'Not recorded'],
    ['Started', date(e.started_at_utc, true)], ['Ended', date(e.ended_at_utc || e.finished_at_utc || e.completed_at_utc, true)],
    ['Backend', e.backend || 'Not recorded'], ['Attempts', e.attempts?.length || e.attempt_count || e.attempts_count || 'Not recorded'],
    ['Original v'+pair.before.version+' submitted', date(pair.before.submitted_at,true)],
    ['Original v'+pair.after.version+' submitted', date(pair.after.submitted_at,true)],
  ];
  $('tab-panel').innerHTML = `<div class="section-heading"><h3>Execution record</h3><a class="quiet-link" href="${pair.raw_url}" download>Download pair JSON ↓</a></div>
    <div class="execution-grid">${fields.map(([key,value]) => `<div class="execution-field"><div class="execution-key">${escapeHTML(key)}</div><div class="execution-value">${escapeHTML(value)}</div></div>`).join('')}</div>
    <div class="notice">Pair elapsed time includes the attempts and retry delays recorded by the runner. Experiment wall time is measured separately; concurrent pair durations must not be added to estimate it.</div>
    ${e.attempts?.length ? `<div class="protocol-section"><h3>Attempts</h3><div class="table-scroll"><table class="attempt-table"><thead><tr><th>Attempt</th><th>Status</th><th>Elapsed</th><th>HTTP status</th><th>Returned model</th></tr></thead><tbody>${e.attempts.map((a,i) => `<tr><td>${a.attempt || a.attempt_number || i+1}</td><td>${badge(a.status)}</td><td>${duration(a.elapsed_seconds ?? a.duration_seconds)}</td><td>${Number.isInteger(a.http_status) ? escapeHTML(a.http_status) : 'Not recorded'}</td><td>${escapeHTML(a.returned_model || 'Not recorded')}</td></tr>`).join('')}</tbody></table></div></div>` : ''}
    ${e.usage ? `<details class="protocol-section"><summary>Reported token usage</summary><pre class="protocol-text">${escapeHTML(JSON.stringify(e.usage,null,2))}</pre></details>` : ''}
    <details class="protocol-section"><summary>Input and execution hashes</summary><div class="execution-grid">${[
      [`v${pair.before.version} PDF SHA-256`,pair.before.sha256], [`v${pair.after.version} PDF SHA-256`,pair.after.sha256],
      ['Execution prompt SHA-256',e.prompt_sha256], ['Saved prompt SHA-256',pair.saved_prompt_sha256],
      ['Output schema SHA-256',e.schema_sha256], ['Result SHA-256',e.result_sha256],
    ].map(([key,value]) => `<div class="execution-field"><div class="execution-key">${escapeHTML(key)}</div><div class="hash">${escapeHTML(value || 'Not recorded')}</div></div>`).join('')}</div></details>
    ${pair.source_entries.length ? `<details class="protocol-section"><summary>Publication metadata</summary><pre class="protocol-text">${escapeHTML(JSON.stringify(pair.source_entries,null,2))}</pre></details>` : ''}
    <div class="notice">Raw API responses, hidden reasoning, credentials, and PDF file contents are excluded from this preview. Missing fields mean the value was not recorded.</div>`;
}

function restoreHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  selected = params.get('pair') || data.pairs[0]?.id;
  activeTab = ['assessment','protocol','execution'].includes(params.get('view')) ? params.get('view') : 'assessment';
  ['source','status','dimension','label'].forEach(id => { $(id).value = params.get(id) || ''; });
  $('search').value = params.get('q') || '';
}

async function init() {
  try {
    const response = await fetch('data.json');
    if (!response.ok) throw new Error('Preview data unavailable');
    data = await response.json();
    if (!Array.isArray(data.pairs)) throw new Error('Invalid preview data');
    const sources = [...new Set(data.pairs.flatMap(p => p.source_ids))].sort();
    sources.forEach(id => $('source').add(new Option(sourceName(id), id)));
    data.dimensions.forEach(id => $('dimension').add(new Option(`${id} · ${DIMENSIONS[id]}`,id)));
    if (data.pairs.some(p => p.status === 'stopped')) $('status').add(new Option('Stopped','stopped'));
    if (data.pairs.some(p => p.status === 'unavailable')) $('status').add(new Option('Unavailable','unavailable'));
    restoreHash(); renderSummary(); renderList();
    ['source','status','dimension','label'].forEach(id => $(id).addEventListener('change',renderList));
    $('search').addEventListener('input',renderList);
    window.addEventListener('hashchange',() => { restoreHash(); renderList(); });
  } catch (error) {
    $('detail').innerHTML = '<div class="empty"><strong>The preview could not load</strong>Serve the generated output directory over localhost, then reload this page.</div>';
    $('run-note').textContent = 'No experiment results have been displayed.';
  }
}
init();
