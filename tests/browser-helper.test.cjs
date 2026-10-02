const test=require('node:test');
const assert=require('node:assert/strict');
const {normalize,answerFor,matchOption}=require('../browser-helper/fill-form.js');
const bundle={job:{company:'Figma'},profile:{'personal.first_name':'Joga','personal.email':'example@example.com','employment.previous_employers':'Tesco','eligibility.right_to_work_uk':'Yes'},aliases:{'personal.first_name':['first name'],'personal.email':['email'],'eligibility.right_to_work_uk':['authorized to work']},answers:{},documents:[]};
const field=label=>({labels:[{textContent:label}],closest:()=>null,getAttribute:()=>null,placeholder:'',name:'',id:'',type:'text'});
test('known details fill while unknown questions are left alone',()=>{
 assert.equal(answerFor(field('First name'),bundle).value,'Joga');
 assert.equal(answerFor(field('Why do you want to join Figma?'),bundle),null);
});
test('UK eligibility does not automatically answer another country question',()=>{
 assert.equal(answerFor(field('Are you authorized to work?'),bundle),null);
});
test('explicit application answers and employer history are used',()=>{
 assert.equal(answerFor(field('Have you ever worked for Figma before?'),bundle).value,'No');
 assert.equal(answerFor(field('Have you ever worked for Tesco before?'),{...bundle,job:{company:'Tesco'}}).value,'Yes');
 assert.equal(answerFor(field('Why do you want to join Figma?'),{...bundle,answers:{'why do you want to join figma':'My own answer'}}).value,'My own answer');
});
test('ambiguous locations and unsupported graduation seasons stay unfilled',()=>{
 assert.equal(matchOption([{textContent:'Aylesbury, England, United Kingdom'},{textContent:'Aylesbury, Canada'}],'Aylesbury'),null);
 const precise={textContent:'Aylesbury, England, United Kingdom'};
 assert.equal(matchOption([precise,{textContent:'Aylesbury, Canada'}],precise.textContent),precise);
 assert.equal(matchOption([{textContent:'Spring 2028'},{textContent:'Fall 2028'}],'23/06/2028'),null);
});
test('labels are compatible with saved normalized answers',()=>assert.equal(normalize('Why Figma? *'),'why figma'));
const vm=require('node:vm');
const fs=require('node:fs');
const context={URL,Set,Date,browser:{runtime:{onMessage:{addListener(){}}},tabs:{onUpdated:{addListener(){}},onRemoved:{addListener(){}}}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require.resolve('../browser-helper/background.js'),'utf8'),context);
test('redirects retain application identity before private data is injected',()=>{
 const original='https://boards.greenhouse.io/figma/jobs/123?gh_jid=123';
 assert.equal(context.sameApplication(original,'https://job-boards.greenhouse.io/figma/jobs/123'),true);
 assert.equal(context.sameApplication(original,'https://job-boards.greenhouse.io/other/jobs/123'),false);
 assert.equal(context.sameApplication(original,'https://example.com/figma/jobs/123'),false);
 assert.equal(context.sameApplication(original,'http://boards.greenhouse.io/figma/jobs/123'),false);
 assert.equal(context.sameApplication('https://jobs.lever.co/company/role?token=abc','https://jobs.lever.co/company/role?token=xyz'),false);
});
test('other websites cannot request a profile or open an application',async()=>{
 await assert.rejects(context.openJob('123',{tab:{id:1},url:'https://example.com/'}),/Open jobs from ApplicationTrackr/);
});
test('controlled dropdowns use real available choices even while the menu is closed',async()=>{
 const select={props:{isSearchable:false},state:{selectValue:[]},buildFocusableOptions:()=>[{label:'Yes',value:'yes'},{label:'No',value:'no'}],getFocusableOptions:()=>[],getOptionLabel:o=>o.label,getOptionValue:o=>o.value,selectOption(o){this.state.selectValue=[o];}};
 const input={__reactFiber$test:{stateNode:select,return:null}};
 assert.equal(await require('../browser-helper/fill-form.js').setCombo(input,'Yes'),true);
 assert.equal(select.state.selectValue[0].value,'yes');
});
test('tracker bridge ignores other services on the same IP address',()=>{
 const code=fs.readFileSync(require.resolve('../browser-helper/tracker-bridge.js'),'utf8');
 const other={location:{origin:'http://192.168.0.136:9000'},document:{documentElement:{dataset:{}}}};
 vm.runInNewContext(code,other);
 assert.deepEqual(other.document.documentElement.dataset,{});
});
