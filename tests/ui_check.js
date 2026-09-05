/* The workspace and the chat pane, exercised against a real engine payload.

   Run with:  node tests/ui_check.js
   Needs jsdom (`npm i jsdom`); it is the only dependency in the project and it
   is a test-only one, so the check skips rather than fails when it is absent.

   The fixture is genuine resolve_cover output. Regenerate it with:
     python -c "import json; from aircrew.tools import Tools,dispatch,renumber;        e=dispatch(Tools(),'resolve_cover',{'pairing_id':'P-2291','vacated_by':'C-1042'});        renumber([e]); json.dump(e,open('tests/fixture_resolve_cover.json','w'),indent=1,default=str)"

   The boundary fixtures come from a running server:
     curl -s localhost:8765/api/tools  -o tests/fixture_tools.json
     curl -s localhost:8765/api/prompt -o tests/fixture_prompt.json
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

setTimeout(async () => {
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
  // One row per person, the way v1 showed it: the controller argues about
  // people, not about rule groups.
  const exclFold = exclPanel.querySelector('details.more');
  check('exclusions fold into one list', !!exclFold);
  check('the list starts shut', exclFold && !exclFold.open);
  check('the summary says how many were ruled out',
        /19 ruled out/.test(exclFold.querySelector('summary').textContent),
        exclFold.querySelector('summary').textContent);
  check('the summary still shows the shape of the rejection',
        /rest/.test(exclFold.querySelector('summary').textContent),
        exclFold.querySelector('summary').textContent);
  const rows = exclFold.querySelectorAll('.excl-list li');
  check('every excluded candidate has its own row', rows.length === 19, rows.length + ' rows');
  check('each row names the person', /C-\d{4}/.test(rows[0].textContent), rows[0].textContent);
  check('each row carries its rule as a tag', !!rows[0].querySelector('.rule-tag'));

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


  // 7b. the workspace draws the decision, not the last thing that ran
  const lookupEnv = {summary:'1 crew', claims:[], data:{count:1, crew:[{crew_id:'C-3310'}]}};
  const checkEnv  = {summary:'legal', claims:[], data:{crew_id:'C-3310', pairing_id:'P-2291',
                     rules:{legal:true, findings:[], rules_checked:[]},
                     callable:{ok:true, reachability_minutes:45}}};
  const mixed = w.drawableSteps({
    tool_results: [payload, checkEnv, lookupEnv],
    tool_calls: [{name:'resolve_cover', arguments:{}},
                 {name:'check_assignment', arguments:{}},
                 {name:'lookup', arguments:{entity:'crew'}}]});
  check('all three steps are drawable', mixed.length === 3, mixed.length + ' drawable');
  // a payload a panel cannot render must cost the panel, never the turn
  const broken = w.drawableSteps({tool_results:[{summary:'x', data:{crew_id:'C-1'}}],
                                  tool_calls:[{name:'check_assignment', arguments:{}}]});
  check('a malformed payload is skipped, not thrown', broken.length === 0);
  const picked = w.mostDecisive(mixed);
  check('the plan wins over a later check and a later lookup',
        picked.call.name === 'resolve_cover', picked.call.name);
  const m3 = w.say('Advisor', w.renderAnswer('answer'));
  w.showSteps(mixed, 'q', m3);
  check('so the workspace shows the ranked cover',
        w.document.querySelector('#panels').textContent.includes('Ranked cover'));

  // 7c. a turn that computes nothing must not leave the last one's evidence up
  w.setPanels(w.panelsFor('resolve_cover', {}, payload), 'q1');
  w.clearPanels('No engine result for this question', 'came from the conversation');
  const after = w.document.querySelector('#panels').textContent;
  check('a panel-less turn clears the workspace', !after.includes('Ranked cover'), after.slice(0, 90));
  check('and says so rather than going blank', /No engine result/.test(after));
  check('clearing drops the back trail', w.document.querySelector('.backbar') === null);

  // 7d. exclusion wording
  check('negative rest is shown as an overlap',
        w.humanise('RULE-REST-04: only -6.75h rest before COVER on 2026-09-15 (rest conflict)')
          .includes('overlaps COVER by 6.75h'),
        w.humanise('RULE-REST-04: only -6.75h rest before COVER on 2026-09-15 (rest conflict)'));
  check('a real rest gap is left alone',
        w.humanise('RULE-REST-04: only 10.75h rest before P-2204 on 2026-09-17')
          .includes('only 10.75h rest'));

  // With one row per person the rule belongs on the row, since there is no
  // group heading above it to carry it.
  w.setPanels(w.panelsFor('resolve_cover', {}, payload), 'q1');
  const list = [...w.document.querySelectorAll('#panels .panel')]
    .find(p => /ruled out/i.test(p.querySelector('h3').textContent))
    .querySelector('details.more');
  const rows2 = [...list.querySelectorAll('.excl-list li')];
  const labelled = rows2.filter(li => li.querySelector('.rule-tag, .tag')).length;
  check('every row says what stopped that person', labelled === 19,
        labelled + ' of 19 labelled');
  // the on-call window is not a rule breach, so it must not wear a rule id
  const oncall = rows2.find(li => /on-call window/.test(li.textContent));
  check('the on-call window row is not badged as a rule breach',
        !!oncall && !oncall.querySelector('.rule-tag') && !!oncall.querySelector('.tag'),
        oncall && oncall.textContent.slice(0, 70));

  // 8. the boundary and the flow
  const tools = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixture_tools.json'), 'utf8'));
  const prompt = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixture_prompt.json'), 'utf8'));
  w.getJSON = async (p) => p === '/api/tools' ? tools : prompt;

  await w.showBoundary();
  const card = w.document.querySelector('.modal-card');
  check('boundary opens a modal', !!card);
  check('it names the model', /gpt-5.6-luna/.test(card.textContent));
  check('it shows the whole system prompt',
        card.querySelector('pre').textContent.length === prompt.system_prompt.length);
  check('it lists every tool', card.querySelectorAll('.toolist li').length === tools.length,
        card.querySelectorAll('.toolist li').length + ' listed');
  check('required args are starred', /pairing_id\*/.test(card.textContent));
  check('tools carry a tier', card.querySelectorAll('.toolist .tier').length === tools.length);
  card.querySelector('header .ghost').dispatchEvent(new w.MouseEvent('click', {bubbles:true}));
  check('close removes it', w.document.querySelector('.modal') === null);

  w.showFlow();
  const flow = w.document.querySelector('.modal-card');
  check('flow opens a modal', !!flow);
  const svg = flow.querySelector('svg');
  check('the flow is one inline svg, no library', !!svg);
  check('it has a labelled boundary', /THE MODEL DECIDES/.test(svg.textContent) &&
        /PYTHON COMPUTES/.test(svg.textContent));
  check('it shows the gate', /Claim gate/.test(svg.textContent));
  check('it shows the withheld path', /withheld/.test(svg.textContent));
  check('it names the loop bound', /up to 8 rounds/.test(svg.textContent));
  check('every box is drawn', svg.querySelectorAll('rect').length === 8,
        svg.querySelectorAll('rect').length + ' boxes');
  check('the legend explains the colours', flow.querySelectorAll('.flow-legend span').length === 4);
  // Escape closes, because a modal that traps you is worse than no modal.
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape'}));
  check('escape closes it', w.document.querySelector('.modal') === null);

  console.log(fails ? `\n${fails} FAILURE(S)` : '\nchat pane works');
  process.exit(fails ? 1 : 0);
}, 400);
