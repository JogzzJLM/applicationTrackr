const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('integrations/google-sheets.gs', 'utf8');
function writer(initial, options={}) {
  const rows = structuredClone(initial), notes = new Map();
  let locked=false, releases=0;
  const range=(r,c,h=1,w=1)=>({
    setValue(v){assert.equal(locked,true); rows[r-1] ||= []; rows[r-1][c-1]=v;return this;},
    setValues(values){assert.equal(locked,true);values.forEach((row,i)=>{rows[r+i-1] ||= [];row.forEach((v,j)=>rows[r+i-1][c+j-1]=v);});return this;},
    setNote(v){notes.set(r+':'+c,v);return this;}
  });
  const sheet={getDataRange:()=>({getValues:()=>structuredClone(rows)}),getLastRow:()=>rows.length,
    getLastColumn:()=>Math.max(...rows.map(r=>r.length)),getMaxColumns:()=>26,
    insertColumnAfter:()=>{},getRange:range};
  const context=vm.createContext({SpreadsheetApp:{getActiveSpreadsheet:()=>({getSheetByName:n=>n==='Raw'?sheet:null}),flush(){}},
    LockService:{getScriptLock:()=>({waitLock(){if(options.busy)throw Error('Busy');assert.equal(locked,false);locked=true;},hasLock:()=>locked,releaseLock(){locked=false;releases++;}})},
    ContentService:{MimeType:{JSON:'json'},createTextOutput:value=>({setMimeType:()=>JSON.parse(value)})}});
  vm.runInContext(script,context);
  return {send:data=>context.doPost({postData:{contents:JSON.stringify(data)}}),rows,notes,releases:()=>releases};
}
const header=['Company','Role','Stage 1','Stage 2','Stage 3','Stage 4'];
test('repeated reminders and request retries append one event only',()=>{
  const w=writer([header,['Acme','Intern 2027','Applied']]);
  const event={company:'Acme',role:'Intern 2027',stage:'Online Assessment',automatic:true};
  assert.equal(w.send(event).status,'success');
  assert.equal(w.send(event).status,'duplicate');
  assert.equal(w.send({...event,stage:'Assessment 1'}).status,'duplicate');
  assert.deepEqual(w.rows[1],['Acme','Intern 2027','Applied','Online Assessment']);
  assert.equal(w.releases(),3);
});
test('same company roles and years have separate rows',()=>{
  const w=writer([header,['Acme','Intern 2027','Applied'],['Acme','Analyst 2027','Applied']]);
  w.send({company:'Acme',role:'Analyst 2027',stage:'Rejected'});
  w.send({company:'Acme',role:'Intern 2026',stage:'Applied'});
  assert.equal(w.rows[1].length,3);assert.equal(w.rows[2][3],'Rejected');assert.equal(w.rows[3][1],'Intern 2026');
});
test('HTML-encoded titles and canonical company aliases match the existing application',()=>{
  const w=writer([header,['Ttp1','Graduate &amp; Internship 2027','Applied']]);
  assert.equal(w.send({company:'TTP',role:'Graduate & Internship 2027',stage:'Online Assessment'}).status,'success');
  assert.equal(w.rows.length,2);assert.equal(w.rows[1][3],'Online Assessment');
});
test('a second round is explicit and reminders do not manufacture a third',()=>{
  const w=writer([header,['Acme','Intern','Applied','Online Assessment']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Online Assessment 2',automatic:true}).status,'success');
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Online Assessment',automatic:true}).status,'duplicate');
  assert.equal(w.rows[1][4],'Online Assessment 2');
});
test('automatic mail conflicts use the live row under the lock',()=>{
  for(const history of [['Applied','Rejected'],['Applied','Interview 2']]) {
    const w=writer([header,['Acme','Intern',...history]]);
    assert.equal(w.send({company:'Acme',role:'Intern',stage:'Group Assessment',automatic:true}).status,history.at(-1)==='Rejected'?'error':'success');
  }
  const w=writer([header,['Acme','Intern','Applied','Online Assessment 2']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Online Assessment 1',automatic:true}).status,'error');
});
test('full stage history extends named columns and preserves unrelated data',()=>{
  const w=writer([['Company','Role','Stage 1','Stage 2','Link'],['Acme','Intern','Applied','Interview','https://example.com']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Offer'}).status,'success');
  assert.equal(w.rows[0][5],'Stage 3');assert.equal(w.rows[1][4],'https://example.com');assert.equal(w.rows[1][5],'Offer');
});
test('a cleared early cell is not used to insert an event before a later round',()=>{
  const w=writer([header,['Acme','Intern','Applied','','Interview 2']]);
  w.send({company:'Acme',role:'Intern',stage:'Offer'});
  assert.equal(w.rows[1][3],'');assert.equal(w.rows[1][5],'Offer');
});
test('ambiguous duplicate rows fail rather than selecting the first one',()=>{
  const w=writer([header,['Acme','Intern','Applied'],['Acme','Intern','Applied']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Rejected'}).status,'error');
  assert.equal(w.rows[1].length,3);assert.equal(w.rows[2].length,3);
});
test('missing role, copied job titles and formula injection cannot write a row',()=>{
  const w=writer([header]);
  for(const data of [{company:'Acme',stage:'Applied'},{company:'Software Intern',role:'Software Intern',stage:'Applied'},{company:'Acme',role:'Intern',stage:'=IMPORTXML("url")'}]) assert.equal(w.send(data).status,'error');
  assert.equal(w.rows.length,1);
});
test('custom events are kept verbatim, while notes hold only opaque event hashes',()=>{
  const w=writer([header,['Acme','Intern','Applied']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Case Study Presentation',event_id:'a'.repeat(64)}).status,'success');
  assert.equal(w.rows[1][3],'Case Study Presentation');assert.equal(w.notes.get('2:4'),'ApplicationTrackr event: '+'a'.repeat(64));
});
test('a contended lock fails without changing the sheet',()=>{
  const w=writer([header,['Acme','Intern','Applied']],{busy:true});
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Rejected'}).status,'error');assert.equal(w.rows[1].length,3);
});
test('explicit reopening preserves history and allows a new outcome',()=>{
  const w=writer([header,['Acme','Intern','Applied','Rejected']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Reopened'}).status,'success');
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Rejected',automatic:true}).status,'success');
  assert.deepEqual(w.rows[1].slice(2),['Applied','Rejected','Reopened','Rejected']);
});
test('an unknown custom event cannot be silently overwritten by inferred progression',()=>{
  const w=writer([header,['Acme','Intern','Applied','Case Study Presentation']]);
  assert.equal(w.send({company:'Acme',role:'Intern',stage:'Online Assessment',automatic:true}).status,'error');
});
