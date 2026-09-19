/* Behavioral tests in a minimal DOM harness. These are not visual/browser tests. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const ui = JSON.parse(fs.readFileSync(path.join(__dirname, 'ui_en.json'), 'utf8'));
const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8').replace('__UI__', JSON.stringify(ui));
const staticKeys = [...fs.readFileSync(path.join(__dirname, 'template.html'), 'utf8').matchAll(/data-translate="([^"]+)"/g)].map(m=>m[1]);
const readPayload = i => JSON.parse(fs.readFileSync(path.join(root, `html/human-evaluation/evaluator-0${i}.html`), 'utf8').match(/<script id="study-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);

function setup(payload, {stored=null, failStorage=false, confirm=true, locale='zh-CN', preference=null}={}) {
  const elements = new Map(), store = new Map(), blobs = [], downloads = [];
  const makeElement = id => {
    const element = {id, hidden:false, textContent:'', _html:'', value:'', dataset:{}, listeners:{}, classes:new Set(), attrs:{},
      addEventListener(type, handler) {this.listeners[type]=handler;},
      setAttribute(name, value) {this.attrs[name]=value;},
      scrollIntoView() {}, focus() {}, remove() {},
      click() {if (this.download) downloads.push(this.download); return this.listeners.click?.({target:this});},
      querySelector() {return {focus(){}};},
      appendChild() {},
      matches(selector) {return this.type === 'radio';},
      closest() {return this;}
    };
    element.classList = {toggle(name,on){on ? element.classes.add(name) : element.classes.delete(name);}};
    Object.defineProperty(element,'innerHTML',{get(){return this._html;},set(html){
      this._html=html;
      for (const match of html.matchAll(/\bid="([^"]+)"/g)) if (!elements.has(match[1])) elements.set(match[1],makeElement(match[1]));
    }});
    return element;
  };
  for (const id of ['study-data','storage-warning','toast','save-status','completed','progress','pair-nav','paper','next-unfinished','export-button','import-button','import-file','language']) elements.set(id,makeElement(id));
  elements.get('study-data').textContent=JSON.stringify(payload);
  const key=`${payload.study_id}:${payload.evaluator_id}:${payload.assignment_hash}`;
  if (stored !== null) store.set(key,stored);
  if (preference !== null) store.set('paper-trace-language',preference);
  const staticNodes=staticKeys.map(key=>({dataset:{translate:key},textContent:''}));
  const windowEvents={};
  const sandbox={console, Blob, Date, JSON, Set, Object, String, Number, Error, Array, navigator:{language:locale},
    document:{documentElement:{lang:''},title:'',getElementById(id){assert(elements.has(id), `Unknown element: ${id}`);return elements.get(id);},querySelectorAll(selector){return selector==='[data-translate]' ? staticNodes : [{textContent:''},{textContent:''}];},createElement(){return makeElement('download');},body:{appendChild(){}}},
    localStorage:{getItem(k){if(failStorage)throw Error('disabled');return store.get(k)??null;},setItem(k,v){if(failStorage)throw Error('disabled');store.set(k,v);}},
    URL:{createObjectURL(blob){blobs.push(blob);return 'blob:test';},revokeObjectURL(){}},
    setTimeout(){return 1;},clearTimeout(){},
    window:{confirm(){return confirm;},addEventListener(type,fn){windowEvents[type]=fn;}}
  };
  const context=vm.createContext(sandbox);
  vm.runInContext(app,context);
  const run = code => vm.runInContext(code,context);
  const state = () => JSON.parse(run('JSON.stringify(state)'));
  const choose = (field,value,dim) => elements.get('paper').listeners.change({target:{type:'radio',value,dataset:{field,dim},matches(){return true;}}});
  const importValue = async value => elements.get('import-file').listeners.change({target:{files:[{size:JSON.stringify(value).length,text:async()=>JSON.stringify(value)}],value:'selected'}});
  const switchLanguage = value => elements.get('language').listeners.change({target:{value}});
  return {elements,store,blobs,downloads,key,run,state,choose,importValue,windowEvents,staticNodes,switchLanguage};
}

(async()=>{
  let checks=0;
  for (let i=1;i<=5;i++) {
    const payload=readPayload(i), h=setup(payload);
    assert.equal(h.elements.get('pair-nav').innerHTML.match(/data-pair=/g).length,40);
    assert.equal(h.elements.get('paper').innerHTML.match(/type="radio"/g).length,66);
    assert(!h.elements.get('paper').innerHTML.includes(' checked'));
    assert.equal(h.elements.get('paper').innerHTML.match(/data-paper-link=/g).length,2);
    assert.equal(h.run('completeCount()'),0); checks++;
  }
  const payload=readPayload(1), h=setup(payload);
  for (const d of payload.dimensions) h.choose('criteria','accurate',d.id);
  h.choose('overall','slightly_better'); h.choose('evidence_scope','both_versions');
  assert.equal(h.run('completeCount()'),1);
  assert.equal(h.elements.get('completed').textContent,1);
  assert.equal(Object.keys(h.state().answers[payload.pairs[0].id].criteria).length,14);
  assert(h.state().answers[payload.pairs[0].id].first_answered_at); checks++;
  h.run(`navigate(${JSON.stringify(payload.pairs[1].id)})`);
  assert(!h.elements.get('paper').innerHTML.includes(' checked'));
  h.run(`navigate(${JSON.stringify(payload.pairs[0].id)})`);
  assert.equal(h.elements.get('paper').innerHTML.match(/ checked/g).length,16); checks++;
  const reload=setup(payload,{stored:h.store.get(h.key)});
  assert.equal(reload.run('completeCount()'),1);
  assert.deepEqual(reload.state(),h.state()); checks++;
  h.choose('criteria','unable_to_determine','M1');
  assert.equal(h.state().answers[payload.pairs[0].id].criteria.M1,'unable_to_determine');
  h.elements.get('export-button').click();
  const exported=JSON.parse(await h.blobs[0].text());
  assert.equal(exported.status,'in_progress');
  assert.equal(Object.keys(exported.answers).length,40);
  assert.equal(exported.completed_pairs,1);
  assert.equal(exported.answers[payload.pairs[1].id].overall,null);
  assert(h.downloads[0].includes('E01-in_progress')); checks++;
  const imported=setup(payload);
  await imported.importValue(exported);
  assert.equal(imported.run('completeCount()'),1);
  assert.deepEqual(imported.state().answers,exported.answers); checks++;
  const rejected=setup(readPayload(2));
  await rejected.importValue(exported);
  assert.equal(rejected.run('completeCount()'),0);
  assert(rejected.elements.get('toast').textContent.startsWith('导入失败')); checks++;
  for (const mutation of [x=>x.assignment_hash='wrong',x=>x.answers.unknown={},x=>x.answers[payload.pairs[0].id].criteria.M1='bad',x=>x.answers[payload.pairs[0].id].criteria.EXTRA='accurate',x=>x.answers[payload.pairs[0].id].overall='bad',x=>x.answers[payload.pairs[0].id].updated_at='bad']) {
    const bad=structuredClone(exported);mutation(bad);
    const before=imported.state();await imported.importValue(bad);
    assert.deepEqual(imported.state(),before);
    assert(imported.elements.get('toast').textContent.startsWith('导入失败')); checks++;
  }
  const declined=setup(payload,{stored:h.store.get(h.key),confirm:false});
  const before=declined.state();await declined.importValue(exported);assert.deepEqual(declined.state(),before);checks++;
  const broken=setup(payload,{failStorage:true});
  broken.choose('criteria','inaccurate','M1');
  assert.equal(broken.state().answers[payload.pairs[0].id].criteria.M1,'inaccurate');
  assert.equal(broken.elements.get('storage-warning').hidden,false);
  let prevented=false;broken.windowEvents.beforeunload({preventDefault(){prevented=true;}});assert(prevented);
  broken.elements.get('export-button').click();
  assert.equal(JSON.parse(await broken.blobs[0].text()).answers[payload.pairs[0].id].criteria.M1,'inaccurate');checks++;
  const corrupt=setup(payload,{stored:'broken json'});
  assert.equal(corrupt.elements.get('storage-warning').hidden,false);
  assert.equal(corrupt.store.get(corrupt.key),'broken json'); checks++;
  const full=setup(payload);
  for (const pair of payload.pairs) {
    full.run(`navigate(${JSON.stringify(pair.id)})`);
    for(const d of payload.dimensions) full.choose('criteria','partly_accurate',d.id);
    full.choose('overall','no_meaningful_difference');full.choose('evidence_scope','both_versions');
  }
  assert.equal(full.run('completeCount()'),40);
  assert.equal(full.run('makeExport().status'),'complete');
  assert.equal(full.run('makeExport().completed_pairs'),40);
  full.run('nextUnfinished()');assert(full.elements.get('toast').textContent.includes('所有论文对'));checks++;
  assert.equal(h.run('esc("<script>&\\\"\\\'")'),'&lt;script&gt;&amp;&quot;&#39;');checks++;
  // Every assigned paper must render fully in both languages, including
  // rubric explanations, option labels, navigation and accessibility labels.
  for (let i=1;i<=5;i++) {
    const p=readPayload(i), en=setup(p,{locale:'en-US'});
    assert.equal(en.run('document.documentElement.lang'),'en');
    assert(en.staticNodes.every(n=>n.textContent===ui[n.dataset.translate]));
    for (const pair of p.pairs) {
      en.run(`navigate(${JSON.stringify(pair.id)})`);
      assert(!/[\u4e00-\u9fff]/.test(en.elements.get('paper').innerHTML));
      assert(!/[\u4e00-\u9fff]/.test(en.elements.get('pair-nav').innerHTML));
      assert(!/[\u4e00-\u9fff]/.test(en.elements.get('completion-note').textContent));
      assert.equal(en.elements.get('paper').innerHTML.match(/<blockquote/g).length,14);
      en.switchLanguage('zh');
      assert.equal(en.elements.get('paper').innerHTML.match(/<blockquote/g).length,28);
      assert(en.elements.get('paper').innerHTML.includes(en.run(`esc(STUDY.pairs.find(p=>p.id===state.current_pair_id).descriptions_zh.M1)`)));
      assert.equal(en.run('document.documentElement.lang'),'zh-CN');
      assert(en.staticNodes.every(n=>n.textContent===n.dataset.translate));
      en.switchLanguage('en');
    }
    checks++;
  }
  const bilingual=setup(payload,{locale:'en-US'});
  for (const d of payload.dimensions) bilingual.choose('criteria','accurate',d.id);
  bilingual.choose('overall','slightly_better');bilingual.choose('evidence_scope','both_versions');
  const beforeSwitch=bilingual.state();
  assert.equal(beforeSwitch.answers[payload.pairs[0].id].last_answer_language,'en');
  bilingual.switchLanguage('zh');
  assert.deepEqual(bilingual.state(),beforeSwitch);
  assert.equal(bilingual.elements.get('paper').innerHTML.match(/ checked/g).length,16);
  assert.equal(bilingual.run('completeCount()'),1);
  assert.equal(bilingual.store.get('paper-trace-language'),'zh');
  assert.equal(bilingual.run('makeExport().interface_language'),'zh');
  bilingual.choose('overall','clearly_better');
  assert.equal(bilingual.state().answers[payload.pairs[0].id].last_answer_language,'zh');
  const bilingualExport=JSON.parse(bilingual.run('JSON.stringify(makeExport())'));
  assert.deepEqual(bilingualExport.response_options.accuracy.accurate,{en:'Accurate',zh:'属实'});
  assert.equal(bilingualExport.presentation_hash,payload.presentation_hash);
  const englishImport=setup(payload,{locale:'en-US'});
  await englishImport.importValue(bilingualExport);
  assert.deepEqual(englishImport.state().answers,bilingualExport.answers);
  assert(!/[\u4e00-\u9fff]/.test(englishImport.elements.get('toast').textContent));checks++;
  // Exports from the earlier form have no language or presentation fields.
  const legacy=structuredClone(bilingualExport);
  for(const k of ['presentation_hash','presentation_version','interface_language']) delete legacy[k];
  for(const a of Object.values(legacy.answers)) delete a.last_answer_language;
  const legacyImport=setup(payload,{locale:'en-US'});
  await legacyImport.importValue(legacy);
  assert.equal(legacyImport.run('completeCount()'),1);
  assert.deepEqual(legacyImport.state().answers,legacy.answers);checks++;
  assert.equal(setup(payload,{locale:'en-US',preference:'zh'}).run('language'),'zh');
  assert.equal(setup(payload,{locale:'zh-CN',preference:'en'}).run('language'),'en');
  const englishBroken=setup(payload,{locale:'en-US',failStorage:true});
  assert(!/[\u4e00-\u9fff]/.test(englishBroken.elements.get('storage-warning').textContent));
  await englishBroken.importValue({...legacy,assignment_hash:'wrong'});
  assert(englishBroken.elements.get('toast').textContent.startsWith('Import failed:'));checks++;
  console.log(`PASS: ${checks} behavioral checks (all 200 assignments in both languages, switching without answer loss, persistence, navigation, current/legacy import/export, invalid inputs, storage failures, full completion).`);
})().catch(error=>{console.error(error);process.exitCode=1;});
