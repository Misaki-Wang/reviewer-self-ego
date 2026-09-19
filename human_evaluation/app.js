"use strict";
// No network requests or third-party libraries; paper links open separately.
const STUDY = JSON.parse(document.getElementById("study-data").textContent);
const UI = __UI__;
let language = (typeof navigator !== "undefined" && /^zh/i.test(navigator.language)) ? "zh" : "en";
try { const preference = localStorage.getItem("paper-trace-language"); if (["en", "zh"].includes(preference)) language = preference; } catch (_) {}
const tr = text => language === "zh" ? text : (UI[text] ?? text);
function localizeChrome() {
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.title = `Paper Trace · ${language === "zh" ? "人工评估" : "Human evaluation"} ${STUDY.evaluator_id}`;
  document.querySelectorAll("[data-translate]").forEach(el => {el.textContent = tr(el.dataset.translate);});
  document.getElementById("language").value = language;
  document.getElementById("language").setAttribute("aria-label", tr("语言"));
  document.getElementById("progress").setAttribute("aria-label", language === "zh" ? "已完成论文对" : "Completed paper pairs");
  document.getElementById("pair-nav").setAttribute("aria-label", language === "zh" ? "选择论文对" : "Select a paper pair");
}
const ACCURACY = [
  ["accurate", "属实", "关键事实与论文一致"],
  ["partly_accurate", "部分属实", "有实质错误或遗漏"],
  ["inaccurate", "不属实", "核心描述错误"],
  ["unable_to_determine", "无法判断", "未核实或证据不足"]
];
const QUALITY = [
  ["clearly_better", "明显改善"], ["slightly_better", "略有改善"],
  ["no_meaningful_difference", "无实质差别"], ["slightly_worse", "略有退步"],
  ["clearly_worse", "明显退步"], ["unable_to_determine", "无法判断"]
];
const EVIDENCE = [
  ["both_versions", "已查看两个版本", "核对了相关正文、图表或附录"],
  ["one_version", "只查看了一个版本"],
  ["descriptions_only", "只看了候选描述"],
  ["access_problem", "论文访问或材料有问题"]
];
const KEY = `${STUDY.study_id}:${STUDY.evaluator_id}:${STUDY.assignment_hash}`;
const ids = new Set(STUDY.pairs.map(p => p.id));
const dimIds = STUDY.dimensions.map(d => d.id);
const byId = Object.fromEntries(STUDY.pairs.map(p => [p.id, p]));
const freshState = () => ({study_id:STUDY.study_id, schema_version:STUDY.schema_version, evaluator_id:STUDY.evaluator_id, assignment_hash:STUDY.assignment_hash, current_pair_id:STUDY.pairs[0].id, answers:{}});
const emptyAnswer = () => ({criteria:{}, overall:null, evidence_scope:null, first_answered_at:null, updated_at:null});
let state = freshState();
let storageAvailable = true;
let lastExportRevision = "";
let revision = "";
let toastTimer;

function validateState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(tr("答案文件格式不正确。"));
  for (const field of ["study_id", "schema_version", "evaluator_id", "assignment_hash"]) {
    if (value[field] !== STUDY[field]) throw new Error(tr("答案不属于本份评估，或页面版本不同。请使用对应的评估页面。"));
  }
  if (!value.answers || typeof value.answers !== "object" || Array.isArray(value.answers)) throw new Error(tr("答案字段缺失。"));
  const clean = freshState();
  if (value.current_pair_id != null && !ids.has(value.current_pair_id)) throw new Error(tr("论文编号不匹配。"));
  if (value.current_pair_id) clean.current_pair_id = value.current_pair_id;
  for (const [id, answer] of Object.entries(value.answers)) {
    if (!ids.has(id)) throw new Error(tr("文件中含有其他评估任务的论文。"));
    if (!answer || typeof answer !== "object" || !answer.criteria || typeof answer.criteria !== "object" || Array.isArray(answer.criteria)) throw new Error(tr("维度答案格式错误。"));
    const a = emptyAnswer();
    for (const [dim, selected] of Object.entries(answer.criteria)) {
      if (!dimIds.includes(dim) || !ACCURACY.some(o => o[0] === selected)) throw new Error(tr("包含无效的维度或选项。"));
      a.criteria[dim] = selected;
    }
    for (const [field, options] of [["overall", QUALITY], ["evidence_scope", EVIDENCE]]) {
      if (answer[field] != null && !options.some(o => o[0] === answer[field])) throw new Error(tr("包含无效的整体评价选项。"));
      a[field] = answer[field] ?? null;
    }
    for (const field of ["first_answered_at", "updated_at"]) {
      if (answer[field] != null && (typeof answer[field] !== "string" || !Number.isFinite(Date.parse(answer[field])))) throw new Error(tr("答案时间格式错误。"));
      a[field] = answer[field] ?? null;
    }
    if (["en", "zh"].includes(answer.last_answer_language)) a.last_answer_language = answer.last_answer_language;
    clean.answers[id] = a;
  }
  return clean;
}

function answeredCount(a = emptyAnswer()) { return Object.keys(a.criteria).length + Number(!!a.overall) + Number(!!a.evidence_scope); }
function isComplete(a) { return answeredCount(a) === dimIds.length + 2; }
function completeCount() { return STUDY.pairs.filter(p => isComplete(state.answers[p.id])).length; }
function esc(value) { return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c])); }
function warning(message) { const el = document.getElementById("storage-warning"); el.textContent = message; el.hidden = false; }
function toast(message) { clearTimeout(toastTimer); const el = document.getElementById("toast"); el.textContent = message; el.hidden = false; toastTimer = setTimeout(() => {el.hidden = true;}, 5000); }
function save() {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
    storageAvailable = true;
    document.getElementById("save-status").textContent = tr("已保存到此浏览器 · ") + new Date().toLocaleTimeString(language === "zh" ? "zh-CN" : "en-US", {hour:"2-digit", minute:"2-digit"});
  } catch (error) {
    storageAvailable = false;
    document.getElementById("save-status").textContent = tr("尚未保存，请导出备份");
    warning(tr("此浏览器无法保存本地进度。当前答案仍在页面中；关闭前请务必点击“导出答案 JSON”。"));
  }
}
try {
  const raw = localStorage.getItem(KEY);
  if (raw) {
    state = validateState(JSON.parse(raw));
    document.getElementById("save-status").textContent = tr("已恢复此浏览器的进度");
  } else document.getElementById("save-status").textContent = tr("点击选项后自动保存");
} catch (error) {
  storageAvailable = false;
  warning(tr("本地进度无法读取。请导入之前导出的答案文件；当前页仍可填写并导出。原有存储暂未改动。"));
}

function optionGroup(name, options, selected, field, dim) {
  return '<div class="options">' + options.map(([value, label, help]) => `<label class="option"><input type="radio" name="${esc(name)}" value="${value}" data-field="${field}"${dim ? ` data-dim="${dim}"` : ""}${selected === value ? " checked" : ""}><span>${esc(tr(label))}${help ? `<small>${esc(tr(help))}</small>` : ""}</span></label>`).join("") + '</div>';
}

function renderNavigation() {
  const n = completeCount();
  document.getElementById("completed").textContent = n;
  document.getElementById("progress").value = n;
  document.getElementById("pair-nav").innerHTML = STUDY.pairs.map((p, i) => {
    const a = state.answers[p.id];
    const status = isComplete(a) ? tr("已答完") : answeredCount(a) ? tr("已开始") : tr("未开始");
    return `<button type="button" data-pair="${p.id}" class="${isComplete(a) ? "done" : answeredCount(a) ? "partial" : ""} ${p.id === state.current_pair_id ? "current" : ""}" ${p.id === state.current_pair_id ? 'aria-current="step"' : ""} aria-label="${language === "zh" ? "第 " + (i + 1) + " 对" : "Pair " + (i + 1)}, ${status}" title="${esc(p.title)}">${String(i+1).padStart(2,"0")}</button>`;
  }).join("");
}

function updatePairProgress() {
  const a = state.answers[state.current_pair_id] || emptyAnswer();
  const count = answeredCount(a);
  document.querySelectorAll(".pair-answer-count").forEach(el => {el.textContent = `${count} / ${dimIds.length+2} ${language === "zh" ? "项已答" : "answered"}`;});
  dimIds.forEach(id => {
    const el = document.getElementById(`jump-${id}`);
    el.classList.toggle("answered", !!a.criteria[id]);
    el.setAttribute("aria-label", `${id} ${a.criteria[id] ? tr("已答") : tr("未答")}`);
  });
  const note = document.getElementById("completion-note");
  note.textContent = isComplete(a) ? tr("本对已答完。可继续下一对，也可随时回来修改。") : (language === "zh" ? `本对还有 ${dimIds.length+2-count} 项未答；可以稍后继续。` : `${dimIds.length+2-count} items remain unanswered. You can return to this pair later.`);
  note.classList.toggle("blank", !isComplete(a));
}

function renderPaper() {
  const p = byId[state.current_pair_id];
  const a = state.answers[p.id] || emptyAnswer();
  const index = STUDY.pairs.findIndex(q => q.id === p.id);
  document.getElementById("paper").innerHTML = `
  <section class="panel pair-heading" id="pair-top">
    <div class="pairmeta"><span class="eyebrow">Pair ${String(index+1).padStart(2,"0")} / 40</span><span>${p.id} · arXiv:${p.arxiv_id}</span></div>
    <h1 tabindex="-1" id="paper-title">${esc(p.title)}</h1>
    <div class="version-links">${["before", "after"].map((side, i) => `<div class="version"><div class="kicker">${i ? tr("后一个版本") : tr("前一个版本")} · v${p[side].version}${p[side].pages ? ` · ${p[side].pages} ${language === "zh" ? "页" : "pages"}` : ""}</div><a class="button" href="${esc(p[side].pdf_url)}" target="_blank" rel="noopener noreferrer" data-paper-link="${side}">${tr("打开完整 PDF ↗")}</a><a class="secondary" href="${esc(p[side].abs_url)}" target="_blank" rel="noopener noreferrer">${tr("版本信息 ↗")}</a></div>`).join("")}</div>
    <p class="fine">${tr("两个链接固定到本次比较的具体版本，包含该版本自身的正文与附录。链接会在新标签页打开。")}</p>
    ${language === "zh" ? `<p class="translation-note">${tr("中文为参考译文；如有歧义，请对照英文和论文原文。")}</p>` : ""}<div class="jump-list">${dimIds.map(id => `<a id="jump-${id}" href="#dimension-${id}">${id}</a>`).join("")}<a href="#overall">${tr("整体判断 ↓")}</a></div>
  </section>
  <div class="section-heading"><h2>${tr("01 — 修改描述是否属实？")}</h2><span class="pair-answer-count"></span></div>
  ${STUDY.dimensions.map(d => `<section class="panel dimension" id="dimension-${d.id}"><h3><span class="dim-id">${d.id}</span>${esc(language === "zh" ? d.name : d.english)}${language === "zh" ? `<span class="dim-english">${esc(d.english)}</span>` : ""}</h3><p class="help">${esc(language === "zh" ? d.help : d.help_en)}</p><div class="claim-label">${tr("待核验描述 · 请以论文原文为准")}</div>${language === "zh" ? `<div class="claim-label">中文译文</div><blockquote class="claim claim-zh" lang="zh-CN">${esc(p.descriptions_zh[d.id])}</blockquote><div class="claim-label">英文说明</div>` : ""}<blockquote class="claim" lang="en">${esc(p.descriptions[d.id])}</blockquote><fieldset><legend>${d.id}: ${tr("以上描述与两个版本的实际内容一致吗？")}</legend>${optionGroup(d.id, ACCURACY, a.criteria[d.id], "criteria", d.id)}</fieldset></section>`).join("")}
  <section class="panel overall" id="overall"><div class="eyebrow">${tr("独立判断")}</div><h2>${tr("02 — 后一个版本整体是否更好？")}</h2><p>${tr("综合正确性、证据、论证、清晰度与可复现性判断；无需逐项相加。事实描述准确也不代表质量必然提高。")}</p><fieldset><legend>${tr("后一个版本相对前一个版本的整体质量")}</legend>${optionGroup("overall", QUALITY, a.overall, "overall")}</fieldset><fieldset><legend>${tr("本对论文的核验依据")}</legend>${optionGroup("evidence_scope", EVIDENCE, a.evidence_scope, "evidence_scope")}</fieldset><p>${tr("如果只阅读了候选描述，建议将无法独立核实的项目标为“无法判断”。")}</p></section>
  <div class="bottomnav"><button id="previous-pair" ${index===0 ? "disabled" : ""}>${tr("← 上一对")}</button><div class="tools"><button id="first-unanswered">${tr("定位未答项")}</button><button id="next-pair" class="primary">${index === STUDY.pairs.length-1 ? tr("检查完成情况") : tr("下一对 →")}</button></div></div><div class="footer-note"><span class="pair-answer-count"></span> · <span id="completion-note"></span></div>`;
  document.getElementById("previous-pair").addEventListener("click", () => navigate(STUDY.pairs[index-1].id));
  document.getElementById("next-pair").addEventListener("click", () => {
    if (index < STUDY.pairs.length-1) navigate(STUDY.pairs[index+1].id);
    else if (completeCount() < STUDY.pairs.length) nextUnfinished();
    else toast(tr("40 对均已答完。请点击顶部“导出答案 JSON”，将文件交回组织者。"));
  });
  document.getElementById("first-unanswered").addEventListener("click", () => {
    const answer = state.answers[p.id] || emptyAnswer();
    const dim = dimIds.find(d => !answer.criteria[d]);
    const target = dim ? document.getElementById(`dimension-${dim}`) : document.getElementById("overall");
    target.scrollIntoView({behavior:"smooth", block:"start"});
    const field = dim || (!answer.overall ? "overall" : "evidence_scope");
    target.querySelector(`input[name="${field}"]`)?.focus({preventScroll:true});
    if (isComplete(answer)) toast(tr("本对已经全部答完。"));
  });
  updatePairProgress();
  renderNavigation();
}

function navigate(id) {
  if (!ids.has(id)) return;
  state.current_pair_id = id;
  save(); renderPaper();
  document.getElementById("pair-top").scrollIntoView({block:"start"});
  document.getElementById("paper-title").focus({preventScroll:true});
}
function nextUnfinished() {
  const current = STUDY.pairs.findIndex(p => p.id === state.current_pair_id);
  for (let offset=1; offset<=STUDY.pairs.length; offset++) {
    const p = STUDY.pairs[(current+offset) % STUDY.pairs.length];
    if (!isComplete(state.answers[p.id])) {navigate(p.id); return;}
  }
  toast(tr("所有论文对都已答完。请导出答案 JSON。"));
}

document.getElementById("paper").addEventListener("change", event => {
  const input = event.target;
  if (!input.matches('input[type="radio"][data-field]')) return;
  const id = state.current_pair_id;
  const a = state.answers[id] || emptyAnswer();
  const {field, dim} = input.dataset;
  const options = field === "criteria" ? ACCURACY : field === "overall" ? QUALITY : field === "evidence_scope" ? EVIDENCE : [];
  if (!options.some(o => o[0] === input.value) || (field === "criteria" && !dimIds.includes(dim))) return;
  if (field === "criteria") a.criteria[dim] = input.value;
  else a[field] = input.value;
  a.first_answered_at ||= new Date().toISOString();
  a.updated_at = new Date().toISOString();
  a.last_answer_language = language;
  state.answers[id] = a;
  revision = a.updated_at;
  save(); updatePairProgress(); renderNavigation();
});
document.getElementById("pair-nav").addEventListener("click", event => {
  const button = event.target.closest("button[data-pair]");
  if (button) navigate(button.dataset.pair);
});
document.getElementById("next-unfinished").addEventListener("click", nextUnfinished);

function makeExport() {
  const output = JSON.parse(JSON.stringify(state));
  // Include every assigned pair, including explicitly empty answers.
  for (const p of STUDY.pairs) output.answers[p.id] ||= emptyAnswer();
  output.exported_at = new Date().toISOString();
  output.completed_pairs = completeCount();
  output.total_pairs = STUDY.pairs.length;
  output.status = output.completed_pairs === output.total_pairs ? "complete" : "in_progress";
  output.interface_language = language;
  output.presentation_version = STUDY.presentation_version;
  output.presentation_hash = STUDY.presentation_hash;
  output.response_options = {accuracy: Object.fromEntries(ACCURACY.map(x => [x[0], {en:UI[x[1]], zh:x[1]}])), overall: Object.fromEntries(QUALITY.map(x => [x[0], {en:UI[x[1]], zh:x[1]}])), evidence_scope: Object.fromEntries(EVIDENCE.map(x => [x[0], {en:UI[x[1]], zh:x[1]}]))};
  return output;
}
document.getElementById("export-button").addEventListener("click", () => {
  const output = makeExport();
  const blob = new Blob([JSON.stringify(output, null, 2)], {type:"application/json;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${STUDY.study_id}-${STUDY.evaluator_id}-${output.status}-${output.exported_at.replace(/[:.]/g,"-")}.json`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  lastExportRevision = revision;
  toast(language === "zh" ? `已生成答案文件（${output.completed_pairs} / 40 对答完）。请确认文件已下载。` : `Answer file created (${output.completed_pairs} / 40 pairs complete). Please check that it downloaded.`);
});
document.getElementById("import-button").addEventListener("click", () => document.getElementById("import-file").click());
document.getElementById("import-file").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5_000_000) throw new Error(tr("文件过大，请选择本页面导出的答案 JSON。"));
    const imported = validateState(JSON.parse(await file.text()));
    if (Object.values(state.answers).some(a => answeredCount(a)>0) && !window.confirm(tr("导入会替换当前页面的答案。若尚未备份，请取消并先导出。是否继续？"))) return;
    state = imported;
    revision = new Date().toISOString();
    save(); renderPaper(); toast(tr("答案已导入，可以继续评估。"));
  } catch (error) { toast(tr("导入失败：") + error.message); }
  finally {event.target.value = "";}
});
window.addEventListener("beforeunload", event => {
  if (!storageAvailable && revision !== lastExportRevision) {event.preventDefault(); event.returnValue = "";}
});
renderPaper();

function setLanguage(next) {
  if (!["en", "zh"].includes(next)) return;
  language = next;
  try {localStorage.setItem("paper-trace-language", language);} catch (_) {}
  document.getElementById("toast").hidden = true;
  localizeChrome(); renderPaper();
  document.getElementById("save-status").textContent = storageAvailable ? tr("点击选项后自动保存") : tr("尚未保存，请导出备份");
  if (!storageAvailable) warning(tr("此浏览器无法保存本地进度。当前答案仍在页面中；关闭前请务必点击“导出答案 JSON”。"));
}
document.getElementById("language").addEventListener("change", event => setLanguage(event.target.value));
localizeChrome();
