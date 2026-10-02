(() => {
  const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9+.# ]+/g,' ').replace(/\s+/g,' ').trim();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
  const labels = field => {
    const values = [...(field.labels || [])].map(label => label.textContent);
    const enclosing = field.closest('label');
    if (enclosing) values.push(enclosing.textContent);
    values.push(field.getAttribute('aria-label'), field.placeholder);
    if (field.type === 'file') values.push(field.name, field.id);
    const primary = values.map(normalize).find(Boolean);
    return primary ? [primary, ...[field.name, field.id].map(normalize).filter(Boolean)] : [normalize(field.name || field.id)];
  };
  function answerFor(field, bundle) {
    const candidates = labels(field);
    for (const label of candidates) {
      if (Object.hasOwn(bundle.answers, label)) return {value:bundle.answers[label], explicit:true};
    }
    const label = candidates[0] || '';
    const company = normalize(bundle.job.company);
    const employers = String(bundle.profile['employment.previous_employers'] || '').split(',').map(normalize);
    if (company && employers.some(Boolean) && label.includes(company) && /have you ever worked for|previously worked for|previously employed by/.test(label)) {
      return {value:employers.includes(company)?'Yes':'No', explicit:true};
    }
    const aliases = Object.entries(bundle.aliases).flatMap(([key, values]) => values.map(alias => ({key, alias:normalize(alias)}))).sort((a,b)=>b.alias.length-a.alias.length);
    for (const candidate of candidates) {
      for (const {key, alias} of aliases) {
        if (key.startsWith('eligibility.')) continue;
        if (!(' '+candidate+' ').includes(' '+alias+' ')) continue;
        const value = bundle.profile[key];
        if (key.startsWith('documents.')) return {document:bundle.documents.find(d=>d.key===key)};
        if (typeof value === 'string' && value.trim()) return {value, key};
      }
    }
    return null;
  }
  function matchOption(options, value) {
    const wanted = normalize(value);
    const exact = options.filter(option=>normalize(option.textContent || option.text)===wanted);
    if (exact.length===1) return exact[0];
    const starts = options.filter(option=>normalize(option.textContent || option.text).startsWith(wanted+' '));
    if (starts.length===1) return starts[0];
    const date = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(String(value));
    if (date) {
      const month = new Date(Number(date[3]), Number(date[2])-1,1).toLocaleString('en-GB',{month:'long'});
      for (const label of [month+' '+date[3], date[3]]) {
        const matched = options.filter(option=>normalize(option.textContent || option.text)===normalize(label));
        if (matched.length===1) return matched[0];
      }
    }
    return null;
  }
  function setText(field, value) {
    const prototype = field.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype,'value').set;
    setter.call(field,String(value));
    field.dispatchEvent(new Event('input',{bubbles:true}));
    field.dispatchEvent(new Event('change',{bubbles:true}));
  }
  const reactProps = element => {
    const key = Object.keys(element).find(key=>key.startsWith('__reactProps$'));
    return key ? element[key] : null;
  };
  const reactEvent = field => ({target:field,currentTarget:field,nativeEvent:{isComposing:false},preventDefault(){},stopPropagation(){}});
  const options = () => [...document.querySelectorAll('[role="option"],[class*="select__option"],[id*="-option-"]')].filter(visible);
  async function setCombo(field, value) {
    // React Select exposes its real choices through the mounted component.
    // Using its selection method preserves controlled form state in Safari.
    const fiberKey=Object.keys(field).find(key=>key.startsWith('__reactFiber$'));
    for (let fiber=fiberKey && field[fiberKey]; fiber; fiber=fiber.return) {
      const select=fiber.stateNode;
      if (!select || typeof select.selectOption!=='function' || typeof select.buildFocusableOptions!=='function') continue;
      let choices=select.buildFocusableOptions();
      if (!choices.length && select.props.isSearchable) {
        select.onInputChange(String(value).split(',')[0],{action:'input-change',prevInputValue:''});
        for(let i=0;i<20 && !choices.length;i++){await sleep(200);choices=select.buildFocusableOptions();}
      }
      const match=matchOption(choices.map(option=>({textContent:String(select.getOptionLabel(option)),option})),value);
      if (!match) {select.onInputChange('',{action:'menu-close',prevInputValue:select.props.inputValue});select.onMenuClose();return false;}
      select.selectOption(match.option);
      for (let i=0;i<15;i++) {
        if (select.state.selectValue.some(option=>select.getOptionValue(option)===select.getOptionValue(match.option))) return true;
        // Async locations can remount the control while resolving the place.
        const current=field.id ? document.getElementById(field.id) : field;
        const selected=current?.closest('[class*="control"]')?.querySelector('[class*="single-value"]');
        if (selected && normalize(selected.textContent)===normalize(value)) return true;
        await sleep(100);
      }
      return false;
    }
    const original = field.value;
    field.focus();
    const control = field.closest('[class*="control"]') || field;
    control.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));
    field.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',code:'ArrowDown',keyCode:40,which:40,bubbles:true}));
    for (let parent=field; parent && parent!==document.body; parent=parent.parentElement) {
      const props=reactProps(parent);
      if (props?.onKeyDown) { props.onKeyDown({...reactEvent(field),key:'ArrowDown',code:'ArrowDown'}); break; }
    }
    await sleep(200);
    let selected = matchOption(options(),value);
    if (!selected && !field.readOnly) {
      // Location services search by the city; match the precise saved place below.
      setText(field, String(value).includes(',') ? String(value).split(',')[0] : value);
      reactProps(field)?.onChange?.(reactEvent(field));
      for (let i=0;i<15 && !selected;i++) { await sleep(200); selected=matchOption(options(),value); }
    }
    if (selected) {
      selected.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));
      selected.click();
      reactProps(selected)?.onClick?.(reactEvent(selected));
      await sleep(200);
    }
    else if (!field.readOnly) setText(field, original);
    field.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true}));
    return Boolean(selected);
  }
  async function fillField(field, answer) {
    if (field.type === 'file') {
      if (!answer.document || field.files?.length) return false;
      const bytes = Uint8Array.from(atob(answer.document.base64), character=>character.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes],answer.document.name,{type:answer.document.mime}));
      field.files = transfer.files;
      field.dispatchEvent(new Event('change',{bubbles:true}));
      return true;
    }
    if (field.tagName === 'SELECT') {
      const option = matchOption([...field.options],answer.value);
      if (!option) return false;
      field.value = option.value;
      field.dispatchEvent(new Event('change',{bubbles:true}));
      return true;
    }
    // Radio groups need group-aware option matching; leave them for the applicant.
    if (field.type === 'radio') return false;
    if (field.type === 'checkbox') {
      if (!answer.explicit || !['yes','true','1','no','false','0'].includes(normalize(answer.value))) return false;
      const wanted = ['yes','true','1'].includes(normalize(answer.value));
      if (field.checked!==wanted) field.click();
      return true;
    }
    const combo = field.getAttribute('role')==='combobox' || ['list','both'].includes(field.getAttribute('aria-autocomplete')) || field.closest('[class*="select__"]') || field.readOnly;
    if (combo) return setCombo(field, answer.value);
    let value = answer.value;
    if (field.type==='date' && /^\d{2}\/\d{2}\/\d{4}$/.test(value)) value=value.slice(6)+'-'+value.slice(3,5)+'-'+value.slice(0,2);
    setText(field,value);
    return true;
  }
  globalThis.applicationTrackrFill = async bundle => {
    if (globalThis.applicationTrackrFilling) return;
    globalThis.applicationTrackrFilling = true;
    const touched = new WeakSet(), filled = new WeakSet();
    const userEdit = event => { if (event.isTrusted) touched.add(event.target); };
    document.addEventListener('input',userEdit,true);
    const badge = document.createElement('div');
    badge.setAttribute('role','status');
    badge.style.cssText='position:fixed;bottom:18px;right:18px;z-index:2147483647;background:#fff;color:#172033;border:1px solid #d8dce3;border-radius:10px;padding:14px 18px;box-shadow:0 5px 24px #0002;font:14px system-ui;max-width:310px;';
    badge.textContent='ApplicationTrackr is filling your saved details…';
    document.body.append(badge);
    let count=0;
    for (let pass=0;pass<8;pass++) {
      const fields=[...document.querySelectorAll('input,textarea,select')];
      for (const field of fields) {
        if (filled.has(field) || touched.has(field) || field.disabled || ['hidden','submit','reset','button','image'].includes(field.type)) continue;
        if (!visible(field) && field.type!=='file') continue;
        if (field.type!=='file' && !['checkbox','radio'].includes(field.type) && String(field.value || '').trim()) continue;
        const label=labels(field)[0] || '';
        // Agreements and attestations stay with the applicant.
        if (/\b(terms|consent|certify|attest|signature|privacy policy)\b/.test(label)) continue;
        const answer=answerFor(field,bundle);
        if (!answer || (!answer.document && !String(answer.value || '').trim())) continue;
        try { if(await fillField(field,answer)) {filled.add(field);count++;} } catch { /* Leave this input for the applicant. */ }
      }
      if (pass===0 && count) break;
      await sleep(500);
    }
    document.removeEventListener('input',userEdit,true);
    globalThis.applicationTrackrFilling=false;
    badge.textContent=`ApplicationTrackr filled ${count} fields. Finish the remaining answers and submit when you’re ready.`;
    const close=document.createElement('button');close.textContent='×';close.setAttribute('aria-label','Dismiss autofill message');close.style.cssText='margin-left:10px;border:0;background:transparent;font-size:18px;cursor:pointer';close.onclick=()=>badge.remove();badge.append(close);
    // No submit, Apply or Next button is invoked by this helper.
    return {filled:count};
  };
  if (typeof module !== 'undefined') module.exports={normalize, answerFor, matchOption, setCombo};
})();
