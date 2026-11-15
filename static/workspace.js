/* Lightweight module navigation and server-rendered partial updates, no page reload. */
(() => {
  const names=['protocol','submit','validation','results','curves','leaderboard'];
  const $=id=>document.getElementById(id);
  const positions=new Map();
  let active=null, booted=false, chartStale=false;
  function page(){return JSON.parse($('page-data').textContent);}
  function status(text,isError=false){const box=$('workspace-status');box.textContent=text;box.hidden=!text;box.classList.toggle('is-error',isError);}
  function readyIndicators(){
    const valid={validation:$('validation-report')&&!$('validation-report').hidden,
      results:$('evaluation-result')&&!$('evaluation-result').hidden,curves:!!page().chart&&!chartStale,
      leaderboard:$('leaderboard')&&!$('leaderboard').hidden};
    document.querySelectorAll('[data-module-target]').forEach(button=>{button.dataset.ready=String(!!valid[button.dataset.moduleTarget]);});
  }
  function show(name,options={}){
    if(!names.includes(name))name='protocol';
    if(active)positions.set(active,window.scrollY);
    active=name;
    names.forEach(key=>{
      $('module-'+key).hidden=key!==name;
      const button=$('module-tab-'+key);button.setAttribute('aria-selected',String(key===name));button.tabIndex=key===name?0:-1;
    });
    window.scrollTo({top:options.top ?? positions.get(name) ?? 0,behavior:'instant'});
    history.replaceState({...history.state,module:name},'',location.pathname+location.search+'#'+name);
    if(options.focus)$('module-tab-'+name).focus({preventScroll:true});
    if(name==='curves'&&!chartStale&&page().chart)window.ESAChart?.init();
    window.dispatchEvent(new CustomEvent('esa:module-show',{detail:{module:name}}));
    readyIndicators();
  }
  function placeholder(name,message){
    const panel=$('module-'+name),empty=panel.querySelector('[data-module-empty]');
    if(empty){empty.hidden=false;empty.querySelector('p').textContent=message;}
  }
  function invalidate(contextChanged=false){
    if($('evaluation-result'))$('evaluation-result').hidden=true;
    if($('validation-report'))$('validation-report').hidden=true;
    placeholder('results','输入已变更，请重新评测。');
    placeholder('validation','输入已变更，请重新校验。');
    chartStale=true;window.ESAChart?.destroy();
    if($('raw-visualization'))$('raw-visualization').hidden=true;
    placeholder('curves','输入已变更，重新提交校验或评测后可查看曲线。');
    if(contextChanged){
      status('评测范围已修改，请重新校验或评测。');
    }else if(!$('workspace-status').hidden)status('输入已更新，等待重新提交。');
    readyIndicators();
  }
  function copyContents(document,id){
    const target=$(id),source=document.getElementById(id);
    if(!target)return;
    target.replaceChildren(...(source?[...source.childNodes].map(n=>window.document.importNode(n,true)):[]));
  }
  function applyResponse(html,options={}){
    const incoming=new DOMParser().parseFromString(html,'text/html');
    const script=incoming.getElementById('page-data');
    if(!script||!incoming.getElementById('module-results'))throw new Error(incoming.querySelector('.alert')?.textContent.trim()||'服务器返回了无法识别的响应，请重试。');
    const updated=JSON.parse(script.textContent),meta=updated.meta || {};
    const prior=active, priorScroll=window.scrollY;
    if(options.action==='leaderboard'){
      copyContents(incoming,'module-leaderboard');
      show(prior===options.startedModule?'leaderboard':prior,{top:prior===options.startedModule&&prior!=='leaderboard'?0:priorScroll});
      status(meta.leaderboard_error||'排行榜已更新。',!!meta.leaderboard_error);
      return meta;
    }
    window.ESAChart?.destroy();
    ['module-validation','module-results','module-leaderboard','module-curves','page-feedback','protocol-meta','protocol-warnings'].forEach(id=>copyContents(incoming,id));
    const oldIdentity=$('identity-error'),newIdentity=incoming.getElementById('identity-error');
    if(oldIdentity){oldIdentity.textContent=newIdentity?.textContent||'';oldIdentity.hidden=!meta.identity_error;}
    $('upload-token').value=incoming.getElementById('upload-token')?.value||'';
    const version=incoming.querySelector('#evaluation-form input[name=version]');
    if(version&&document.querySelector('#evaluation-form input[name=version]'))document.querySelector('#evaluation-form input[name=version]').value=version.value;
    $('page-data').textContent=script.textContent;
    chartStale=false;
    window.ESAChannelTable?.init();
    window.dispatchEvent(new CustomEvent('esa:page-updated',{detail:{data:updated,action:options.action}}));
    const auto=prior===options.startedModule;
    const target=auto?(meta.active_module||prior):prior;
    if(auto&&target!==prior)positions.set(target,0);else positions.set(prior,priorScroll);
    show(target,{focus:auto&&target!==prior});
    const failure=meta.error||meta.identity_error||meta.report_has_errors;
    const text=failure?(meta.identity_error||meta.error||'校验未通过，请查看错误记录。'):meta.has_result?(meta.leaderboard_error?'评测完成，但排行榜保存失败，请查看排行榜模块。':'评测完成，结果和排行榜已更新。'):meta.leaderboard_error|| (meta.has_report?'校验完成，可开始评测。':'排行榜已更新。');
    status(text,!!failure||!!meta.leaderboard_error);
    return meta;
  }
  function boot(){
    if(booted)return;booted=true;
    document.addEventListener('click',event=>{
      const channelLink=event.target.closest('[data-chart-channel]');
      if(channelLink){
        event.preventDefault();
        const boxes=[...document.querySelectorAll('#chart-channels input[type=checkbox]')];
        const channel=channelLink.dataset.chartChannel;
        if(chartStale||!boxes.some(box=>box.value===channel))return;
        boxes.forEach(box=>{box.checked=box.value===channel;});
        show('curves',{focus:true,top:0});
        window.ESAChart?.selectChannel(channel);
        return;
      }
      const target=event.target.closest('[data-module-target],[data-module-link]');if(!target)return;
      event.preventDefault();show(target.dataset.moduleTarget||target.dataset.moduleLink,{focus:true});
    });
    $('module-nav').addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();let index=names.indexOf(active);
      index=event.key==='Home'?0:event.key==='End'?names.length-1:(index+(event.key==='ArrowRight'?1:-1)+names.length)%names.length;
      show(names[index],{focus:true});
    });
    window.addEventListener('scroll',()=>{if(active)positions.set(active,window.scrollY);},{passive:true});
    window.addEventListener('hashchange',()=>show(location.hash.slice(1)));
    const state=page().meta||{},hash=location.hash.slice(1);
    show(state.is_post?state.active_module:names.includes(hash)?hash:state.active_module||'protocol');
  }
  window.ESAWorkspace={show,status,invalidate,applyResponse,get active(){return active;}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
