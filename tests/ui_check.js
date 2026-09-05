/* The workspace and the chat pane, exercised against a real engine payload.

   Run with:  node tests/ui_check.js
   Needs jsdom (`npm i jsdom`); it is the only dependency in the project and it
   is a test-only one, so the check skips rather than fails when it is absent.

   The fixture is genuine resolve_cover output. Regenerate it with:
     python -c "import json; from aircrew.tools import Tools,dispatch,renumber;        e=dispatch(Tools(),'resolve_cover',{'pairing_id':'P-2291','vacated_by':'C-1042'});        renumber([e]); json.dump(e,open('tests/fixture_resolve_cover.json','w'),indent=1,default=str)"
*/
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch { console.log('SKIP  jsdom is not installed (npm i jsdom)'); process.exit(0); }

const ROOT = path.join(__dirname, '..');
const HTML = path.join(ROOT, 'web/index.html');
const src = fs.readFileSync(HTML, 'utf8');
const dom = new JSDOM(src, { url: 'http://127.0.0.1:8768/', runScripts: 'dangerously' });
const { window } = dom;

let fails = 0;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) { fails++; if (detail) console.log('        ' + String(detail).slice(0, 200)); }
};

setTimeout(() => {
  const w = window;
  const payload = JSON.parse(fs.readFileSync(
    path.join(__dirname, 'fixture_resolve_cover.json'), 'utf8'));
  const step = { tool_results: [payload], tool_calls: [{ name: 'resolve_cover', arguments: {} }] };

  // 1. markdown
  const md = w.renderAnswer('## Recovery plan\nAssign **C-3310** at *once*.\n- cost `18,500`\n- delay 0h');
  check('bold renders as <strong>', md.querySelectorAll('strong').length === 1, md.innerHTML);
  check('heading renders as <h4>', md.querySelectorAll('h4').length === 1);
  check('list renders 2 items', md.querySelectorAll('li').length === 2);
  check('code renders', md.querySelectorAll('code').length === 1);
  check('no raw asterisks survive', !md.textContent.includes('**'), md.textContent);
  const evil = w.renderAnswer('<img src=x onerror=alert(1)> **safe**');
  check('html in the model reply is escaped', evil.querySelectorAll('img').length === 0, evil.innerHTML);

  // 2. the whole-line bold heading this model actually emits
  const h = w.renderAnswer('**Recovery recommendation:**\nAssign C-3310.');
  check('a whole-line bold becomes a heading', h.querySelector('h4') !== null, h.innerHTML);

  // 3. show workspace
  const steps = w.drawableSteps(step);
  check('resolve_cover counts as drawable', steps.length === 1);
  check('a bare lookup is not drawable',
        w.drawableSteps({ tool_results: [{ data: { x: 1 } }], tool_calls: [{ name: 'nope' }] }).length === 0);

  const m1 = w.say('Advisor', w.renderAnswer('**first** answer'));
  w.showSteps(steps, 'q1', m1);
  const b1 = w.evidenceButton(steps, 'q1', m1);
  check('button is offered', !!b1 && b1.textContent === 'Show in workspace');
  check('turn is marked as showing', m1.classList.contains('showing'));
  const panelsAfter1 = w.document.querySelector('#panels').textContent;
  check('workspace drew the ranked cover', panelsAfter1.includes('Ranked cover'), panelsAfter1.slice(0, 120));

  // a second turn takes the canvas
  const m2 = w.say('Advisor', w.renderAnswer('second answer, no panels'));
  w.markShown(m2);
  check('showing moves to turn 2', m2.classList.contains('showing') && !m1.classList.contains('showing'));

  // clicking turn 1's button brings it back
  b1.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  check('turn 1 workspace comes back', w.document.querySelector('#panels').textContent.includes('Ranked cover'));
  check('showing returns to turn 1', m1.classList.contains('showing') && !m2.classList.contains('showing'));

  // 4. back button
  check('no back bar on a top-level view', w.document.querySelector('.backbar') === null);
  w.pushPanels([w.panel('Why C-5837')], 'Why C-5837');
  const bar = w.document.querySelector('.backbar');
  check('drill-down shows a back bar', bar !== null);
  check('crumb names the drill-down', bar && bar.textContent.includes('Why C-5837'), bar && bar.textContent);
  bar.querySelector('.backbtn').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  check('back returns to the plan', w.document.querySelector('#panels').textContent.includes('Ranked cover'));
  check('back bar is gone at the root', w.document.querySelector('.backbar') === null);

  // 5. a new answer clears the trail
  w.pushPanels([w.panel('Why C-5837')], 'Why C-5837');
  w.setPanels([w.panel('Fresh answer')], 'q2');
  check('a new answer clears the back trail', w.document.querySelector('.backbar') === null);


  // 6. rule tags and tooltips
  const tag = w.ruleTag('RULE-DUTY-02', 'bad');
  check('rule tag shows the id', tag.textContent === 'RULE-DUTY-02');
  check('rule tag carries the gloss', /60 duty hours/.test(tag.dataset.tooltip), tag.dataset.tooltip);
  check('rule tag is reachable by keyboard', tag.tabIndex === 0);
  w.document.body.appendChild(tag);
  tag.dispatchEvent(new w.MouseEvent('mouseenter'));
  const tip = w.document.querySelector('#rule-tooltip');
  check('hovering opens one tooltip', !!tip && /60 duty hours/.test(tip.textContent));
  tag.dispatchEvent(new w.MouseEvent('mouseleave'));
  check('leaving closes it', w.document.querySelector('#rule-tooltip') === null);
  check('an unknown token gets no tooltip', !w.ruleTag('NOT-A-RULE').dataset.tooltip);

  const reason = w.reasonWithRules(w.document.createElement('span'), 'RULE-DUTY-02: 61.33h exceeds 60h; RULE-REST-04: 9h rest', 'bad');
  check('both rules in a reason become tags', reason.querySelectorAll('.rule-tag').length === 2);
  check('the numbers around them survive', /61.33h exceeds 60h/.test(reason.textContent), reason.textContent);
  check('two findings render on two lines', reason.querySelectorAll('.reason-line').length === 2);

  // 7. excess folds by default
  w.setPanels(w.panelsFor('resolve_cover', {}, payload), 'q');
  // scope to the exclusions panel: the plan panel folds its claims too
  const exclPanel = [...w.document.querySelectorAll('#panels .panel')]
    .find(p => /ruled out/i.test(p.querySelector('h3').textContent));
  check('there is an exclusions panel', !!exclPanel);
  const groups = exclPanel.querySelectorAll('details.more');
  check('exclusions are grouped into details', groups.length >= 3, `${groups.length} groups`);
  const open = [...groups].filter(d => d.open);
  check('only the largest group starts open', open.length === 1, `${open.length} open`);
  check('a shut group still says how many it holds',
        /\d+ crew/.test(groups[1].querySelector('summary').textContent),
        groups[1].querySelector('summary').textContent);
  check('the group summary carries a rule tag', !!groups[0].querySelector('.rule-tag'));
  const hidden = groups[1].querySelector('ul');
  check('the rows exist even while folded', !!hidden && hidden.children.length > 0);

  // a wide lookup folds past the first rows
  const many = {summary:'142 crew match', claims:[], data:{count:142,
    crew: Array.from({length: 142}, (_, i) => ({crew_id:'C-'+(1000+i), rank:'Captain', base:'BLR'}))}};
  const nodes = w.panelsFor('lookup', {entity:'crew'}, many);
  const host = w.document.createElement('div'); nodes.forEach(n => host.appendChild(n));
  check('a 142-row lookup shows only the first 8', host.querySelector('tbody').children.length === 8,
        host.querySelector('tbody').children.length + ' rows');
  const fold = host.querySelector('details.more');
  check('the rest is folded and counted', !!fold && /142 in total/.test(fold.textContent));
  check('the folded table is not open by default', fold && !fold.open);

  console.log(fails ? `\n${fails} FAILURE(S)` : '\nchat pane works');
  process.exit(fails ? 1 : 0);
}, 400);
