/* Form state is retained while HTML result panels are updated through fetch. */
(() => {
  let data=JSON.parse(document.querySelector('#page-data').textContent);
  const form=document.querySelector('#evaluation-form'),rows=document.querySelector('#prediction-rows');
  const message=document.querySelector('#client-message'),identity=document.querySelector('#user-id');
  let dirty=false,busy=false,revision=0;
  function invalidate(clearUpload=false,contextChanged=false){
    ++revision;dirty=true;
    if(clearUpload)document.querySelector('#upload-token').value='';
    window.ESAWorkspace.invalidate(contextChanged);
    document.querySelector('#stale-notice').hidden=false;
    document.querySelector('#stale-notice').textContent='输入已变更，请重新提交。';
  }
  function addRow(row={}){
    const tr=document.createElement('tr'),cell=document.createElement('td'),select=document.createElement('select');
    select.name='channel';select.setAttribute('aria-label','通道');
    data.channels.forEach(c=>select.add(new Option(c,c,false,c===row.channel)));cell.append(select);tr.append(cell);
    ['starttime','endtime'].forEach(name=>{const td=document.createElement('td'),input=document.createElement('input');input.name=name;input.value=row[name]||'';input.placeholder='ISO-8601 · UTC';input.setAttribute('aria-label',name);td.append(input);tr.append(td);});
    const td=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='delete-row quiet';button.textContent='删除';td.append(button);tr.append(td);rows.append(tr);
  }
  rows.addEventListener('click',e=>{if(e.target.matches('.delete-row')){e.target.closest('tr').remove();invalidate();}});
  rows.addEventListener('input',()=>invalidate());rows.addEventListener('change',()=>invalidate());
  document.querySelector('#add-row').addEventListener('click',()=>{addRow();invalidate();});
  document.querySelector('#clear-rows').addEventListener('click',()=>{rows.replaceChildren();invalidate();});
  document.querySelectorAll('.context-select').forEach(select=>{
    select.addEventListener('change',()=>{
      const filled=[...rows.querySelectorAll('input')].some(i=>i.value.trim());
      if((dirty||filled||data.has_result||document.querySelector('#upload-token').value)&&!window.confirm('切换评测配置后将清空旧输入和分数，继续吗？')){select.value=select.dataset.previous;return;}
      const params=new URLSearchParams();['mission','source'].forEach(name=>params.set(name,document.querySelector('#'+name).value));
      params.set('mode',form.querySelector('[name=mode]:checked').value);if(identity.value.trim())params.set('user_id',identity.value.trim());
      window.location.assign('/?'+params+'#protocol');
    });select.dataset.previous=select.value;
  });
  document.querySelectorAll('.score-config').forEach(i=>i.addEventListener('input',()=>invalidate(true,true)));
  function syncIdentity(){identity.required=form.querySelector('[name=mode]:checked')?.value==='manual';}
  ['csv','json'].forEach(value=>document.querySelector('#prediction-'+value).addEventListener('change',()=>{invalidate(true);syncIdentity();}));
  identity.addEventListener('input',()=>{syncIdentity();invalidate();document.querySelector('#identity-error').hidden=true;document.querySelectorAll('.best-row-current').forEach(row=>row.classList.remove('best-row-current'));});
  function showMode(value){
    document.querySelector('#manual-input').hidden=value!=='manual';document.querySelector('#csv-input').hidden=value==='manual';
    ['csv','json'].forEach(format=>{const input=document.querySelector('#prediction-'+format);input.hidden=value!==format;input.disabled=value!==format;});
    document.querySelectorAll('[data-upload-format]').forEach(n=>{n.hidden=n.dataset.uploadFormat!==value;});syncIdentity();
  }
  document.querySelectorAll('[name=mode]').forEach(radio=>radio.addEventListener('change',()=>{
    showMode(radio.value);document.querySelector('#prediction-csv').value='';document.querySelector('#prediction-json').value='';document.querySelector('#identity-error').hidden=true;invalidate(true);
  }));
  const initial=form.querySelector('[name=mode]:checked')||form.querySelector('[name=mode][value=csv]');initial.checked=true;showMode(initial.value);
  document.querySelector('#load-example').addEventListener('click',async()=>{
    const params=new URLSearchParams({mission:document.querySelector('#mission').value,source:document.querySelector('#source').value,version:data.version});
    try{const response=await fetch('/example?'+params);const body=await response.json();if(!response.ok)throw new Error(body.error);rows.replaceChildren();body.rows.forEach(addRow);invalidate();message.hidden=true;}
    catch(error){message.textContent=error.message;message.hidden=false;}
  });
  window.addEventListener('esa:page-updated',event=>{data=event.detail.data;dirty=false;document.querySelector('#stale-notice').hidden=true;message.hidden=true;});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    const submitter=event.submitter,action=submitter?.value||'evaluate';
    if(!submitter?.formNoValidate&&!form.reportValidity())return;
    const payload=new FormData(form);payload.set('action',action);
    if(action==='leaderboard')['prediction_csv','prediction_json','upload_token','channel','starttime','endtime'].forEach(key=>payload.delete(key));
    const startedModule=window.ESAWorkspace.active,currentRevision=revision;
    const controls=[...form.querySelectorAll('input,select,button,textarea')].map(node=>[node,node.disabled]);
    busy=true;form.setAttribute('aria-busy','true');controls.forEach(([node])=>{node.disabled=true;});
    window.ESAWorkspace.status(action==='preview'?'正在上传并校验…':action==='leaderboard'?'正在读取排行榜…':'正在评测并保存结果…');
    try{
      const response=await fetch(form.getAttribute('action')||location.pathname,{method:'POST',body:payload,headers:{'X-Requested-With':'fetch'}});
      const html=await response.text();
      if(!response.ok){const document=new DOMParser().parseFromString(html,'text/html');throw new Error(document.querySelector('.alert')?.textContent.trim()||`请求失败（HTTP ${response.status}）`);}
      if(revision!==currentRevision){window.ESAWorkspace.status('输入已改变，返回结果未应用，请重新提交。',true);return;}
      window.ESAWorkspace.applyResponse(html,{action,startedModule});
    }catch(error){window.ESAWorkspace.status(error instanceof TypeError?'网络连接失败，请重试。':error.message||'请求失败，请重试。',true);}
    finally{busy=false;form.removeAttribute('aria-busy');controls.forEach(([node,disabled])=>{node.disabled=disabled;});showMode(form.querySelector('[name=mode]:checked').value);}
  });
  document.addEventListener('click',event=>{
    if(!event.target.closest('[data-submit-action="leaderboard"]')||busy)return;
    form.requestSubmit(form.querySelector('button[name=action][value=leaderboard]'));
  });
})();
