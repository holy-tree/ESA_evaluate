/* Display-only time navigation. Input times stay UTC; scores are immutable snapshots. */
(() => {
  const page = JSON.parse(document.querySelector('#page-data').textContent);
  if (!page.chart) return;
  const $ = id => document.getElementById(id);
  const svg = $('raw-chart'), overview = $('chart-overview'), tracks = $('chart-event-tracks');
  const scroll = document.querySelector('.raw-chart-scroll');
  const status = $('chart-status'), error = $('chart-error'), readout = $('chart-tooltip'), hint = $('chart-hover-hint');
  const mode = $('chart-display'), layout = $('chart-layout'), preset = $('chart-scale'), date = $('chart-date');
  const ns = 'http://www.w3.org/2000/svg', DAY = 86400000;
  const domain = [Date.parse(page.chart.navigation_start || page.chart.start), Date.parse(page.chart.navigation_end || page.chart.end)];
  const minSpan = Math.min(1000, domain[1]-domain[0]);
  const allBoxes = [...document.querySelectorAll('#chart-channels input[type=checkbox]')];
  let selected = allBoxes.filter(b => b.checked).map(b => b.value);
  let data = null, controller = null, serial = 0, timer = null, frame = null, stale = false;
  let checkpoints = [], checkpointIndex = -1, needCheckpoints = true, history = [];
  let hoveredChannel = null, pinnedChannel = null, activeEvent = null, plot = null, hover = null;
  let hoveredEvent = null, focusedEvent = null, eventPaintOrder = [];
  let waveNodes = new Map(), eventNodes = new Map(), eventMap = new Map(), drag = null, overviewDrag = null;
  let wheelActive = false, wheelEnd = null;
  const categoryNames = {'Anomaly': '异常', 'Rare Event': '罕见正常事件', 'Communication Gap': '通信中断', 'Invalid Segment': '无效片段', 'Invalid': '无效片段'};

  function node(tag, attrs = {}, text = null) {
    const e = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k,v]) => e.setAttribute(k, String(v)));
    if (text !== null) e.textContent = text;
    return e;
  }
  function clamp(start, end) {
    const span = Math.max(minSpan, Math.min(domain[1]-domain[0], end-start));
    const a = Math.max(domain[0], Math.min(domain[1]-span, start));
    return [a, a+span];
  }
  let view = clamp(Math.max(domain[0], Date.parse(`${page.chart.date}T00:00:00Z`)), Math.max(domain[0], Date.parse(`${page.chart.date}T00:00:00Z`))+366*DAY);
  function iso(ms) { return new Date(ms).toISOString(); }
  function hideHover() {
    if (hover) hover.group.setAttribute('visibility', 'hidden');
    readout.hidden = true; readout.textContent = ''; hint.hidden = false;
  }
  function remember() {
    const last = history.at(-1);
    if (!last || last.start !== view[0] || last.end !== view[1]) history.push({start: view[0], end: view[1], checkpoint: checkpointIndex});
    if (history.length > 30) history.shift();
    $('chart-back').disabled = !history.length;
  }
  function updateRangeControls() {
    date.value = iso(view[0]).slice(0,19);
    const seconds = (view[1]-view[0])/1000;
    const duration = seconds >= 86400 ? `${(seconds/86400).toFixed(2)} 天` : seconds >= 3600 ? `${(seconds/3600).toFixed(2)} 小时` : `${seconds.toFixed(2)} 秒`;
    $('chart-view-summary').textContent = `当前视窗：${iso(view[0])} → ${iso(view[1])}（${duration}）。完整历史：${iso(domain[0])} → ${iso(domain[1])}。本次评分：${page.chart.start} → ${page.chart.end}。`;
    svg.dataset.viewStart = iso(view[0]); svg.dataset.viewEnd = iso(view[1]);
    $('chart-previous').disabled = view[0] <= domain[0];
    $('chart-next').disabled = view[1] >= domain[1];
    const has = checkpoints.length > 0;
    $('chart-pred-next').disabled = !has;
    $('chart-pred-previous').disabled = !has;
    $('chart-pred-focus').disabled = checkpointIndex < 0;
    $('chart-back').disabled = !history.length;
  }
  function setView(start, end, options = {}) {
    if (stale) return;
    view = clamp(start, end);
    if (!options.keepCheckpoint) checkpointIndex = -1;
    if (!options.eventJump) $('chart-event-navigation').hidden = true;
    preset.value = 'custom';
    updateRangeControls(); hideHover(); renderSoon();
    if (!options.keepCheckpoint) checkpointText();
    if (options.request !== false) schedule(options.delay ?? 150);
  }
  function schedule(delay = 150) {
    if (stale) return;
    clearTimeout(timer); if (controller) controller.abort(); ++serial;
    status.textContent = '视窗已更新，正在加载对应数据… 显示处理不参与评分。';
    timer = setTimeout(load, delay);
  }
  function renderSoon() {
    if (stale) return;
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = null; draw(); });
  }
  function ticks(start, end) {
    const span = end-start;
    const choices = [[120000,10000,'秒'],[7200000,600000,'分'],[2*DAY,21600000,'时'],[14*DAY,2*DAY,'日'],[93*DAY,7*DAY,'周']];
    const choice = choices.find(r => span <= r[0]);
    const values = [start];
    let unit;
    if (choice) {
      unit = choice[2];
      for (let t = Math.ceil(start/choice[1])*choice[1]; t < end; t += choice[1]) values.push(t);
    } else {
      const t = new Date(start); const months = span < 2*366*DAY ? 3 : 12;
      unit = months === 3 ? '月' : '年';
      t.setUTCDate(1); t.setUTCHours(0,0,0,0); if (months === 12) t.setUTCMonth(0);
      while (+t < end) { if (+t >= start) values.push(+t); t.setUTCMonth(t.getUTCMonth()+months); }
    }
    values.push(end);
    return {unit, values: [...new Set(values)].sort((a,b) => a-b).map(t => {
      const s = iso(t);
      const label = unit === '年' ? s.slice(0,4) : unit === '月' ? s.slice(0,7) : ['周','日'].includes(unit) ? s.slice(5,10) : unit === '秒' ? s.slice(11,19) : `${s.slice(5,10)} ${s.slice(11,16)}`;
      return {time:t, label};
    })};
  }
  function timeOf(point) { return Date.parse(data.window_start)+point[0]*1000; }
  function crop(points, start, end) {
    let lo = 0, hi = points.length;
    while (lo < hi) { const m = (lo+hi)>>1; if (timeOf(points[m]) < start) lo = m+1; else hi = m; }
    const from = Math.max(0, lo-1);
    lo = from; hi = points.length;
    while (lo < hi) { const m = (lo+hi)>>1; if (timeOf(points[m]) <= end) lo = m+1; else hi = m; }
    return points.slice(from, Math.min(points.length, lo+1));
  }
  function plotted(c) { return c.mode === 'raw' || c.mode === 'states' ? c.segments : c.trend_segments; }
  function transform(c, value, lane) {
    return lane.normalized ? (value-c.reference.center)/c.reference.scale : value;
  }
  function makeLanes() {
    const rows = data ? data.channels : [];
    const numeric = rows.filter(c => c.kind === 'numeric'), categorical = rows.filter(c => c.kind === 'categorical');
    const lanes = [];
    if (layout.value === 'lanes') rows.forEach(c => lanes.push({channels:[c], height:c.kind === 'numeric' ? 170 : 115, normalized:false}));
    else {
      if (numeric.length) lanes.push({channels:numeric, height:300, normalized:layout.value === 'normalized' && numeric.length > 1});
      categorical.forEach(c => lanes.push({channels:[c], height:115, normalized:false}));
    }
    if (!lanes.length) lanes.push({channels:[], height:300, normalized:false});
    let top = 32;
    for (const lane of lanes) {
      lane.top = top; lane.bottom = top+lane.height; top = lane.bottom+42;
      const first = lane.channels[0];
      if (first?.kind === 'categorical') {
        lane.min = -.5; lane.max = Math.max(.5, first.categories.length-.5);
      } else {
        let lo = Infinity, hi = -Infinity;
        for (const c of lane.channels) {
          const add = v => { const value = transform(c,v,lane); if (Number.isFinite(value)) { lo=Math.min(lo,value); hi=Math.max(hi,value); } };
          if (c.bins.length) c.bins.forEach(b => { add($('chart-extrema').checked ? b.min : b.p05); add($('chart-extrema').checked ? b.max : b.p95); });
          else c.segments.forEach(s => s.forEach(p => add(p[1])));
          c.trend_segments.forEach(s => s.forEach(p => add(p[1])));
        }
        if (!Number.isFinite(lo)) { lo=0; hi=1; }
        const padding = hi === lo ? Math.max(1,Math.abs(hi)*.05) : (hi-lo)*.08;
        lane.min=lo-padding; lane.max=hi+padding;
      }
      lane.y = value => lane.bottom-(value-lane.min)/(lane.max-lane.min)*(lane.bottom-lane.top);
    }
    return {lanes, height:top+5};
  }
  function pathFor(c, segment, lane, start=view[0], end=view[1]) {
    return crop(segment,start,end).map((p,i) => {
      const x=plot.x(timeOf(p)), y=lane.y(transform(c,p[1],lane));
      return i === 0 ? `M${x},${y}` : c.kind === 'categorical' ? `H${x}V${y}` : `L${x},${y}`;
    }).join(' ');
  }
  function bandRect(parent, start, end, top, height, attributes = {}) {
    if (end < view[0] || start > view[1]) return;
    const a=Math.max(view[0],start), b=Math.min(view[1],end);
    const actual=plot.x(b)-plot.x(a), width=Math.max(4,actual);
    parent.append(node('rect',{x:plot.x(a)-(width-actual)/2,y:top,width,height,rx:2,...attributes}));
  }
  function unionDetections(channels) {
    const rows=channels.flatMap(c => c.detections.map(i => [Date.parse(i.start_time),Date.parse(i.end_time)])).sort((a,b) => a[0]-b[0]);
    const out=[];
    for (const r of rows) { if (out.length && r[0] <= out.at(-1)[1]) out.at(-1)[1]=Math.max(out.at(-1)[1],r[1]); else out.push([...r]); }
    return out;
  }
  function applyHighlights() {
    const target=hoveredChannel || pinnedChannel;
    for (const [name,g] of waveNodes) { g.style.opacity=!target || name===target ? '1' : '.24'; g.classList.toggle('is-highlighted',name===target); }
    if (target && waveNodes.has(target)) { const g=waveNodes.get(target); g.parentNode.append(g); }
    if (!target && data) data.channels.forEach(c=>{const g=waveNodes.get(c.channel);if(g)g.parentNode.append(g);});
    document.querySelectorAll('.channel-legend-item').forEach(b => { b.classList.toggle('is-highlighted',b.dataset.channel===target); b.setAttribute('aria-pressed',String(b.dataset.channel===pinnedChannel)); });
    $('chart-highlight-status').textContent=target ? `正在突出显示：${target}（只改变层级、线宽和阴影，不移动数据坐标）` : '悬停通道图例可浮起加粗对应曲线；点击可固定高亮。';
    const raisedEvent=hoveredEvent || focusedEvent;
    const eventTarget=raisedEvent || activeEvent;
    for (const [key,nodes] of eventNodes) nodes.forEach(n => {
      const raised=key===raisedEvent;
      n.style.opacity=!eventTarget || key===eventTarget ? '1' : '.25';
      n.classList.toggle('is-event-raised',raised);
      if(n.classList.contains('event-track-item')){
        const y=Number(n.dataset.baseY);
        n.setAttribute('y',raised ? y-3 : y);
        n.setAttribute('height',raised ? 19 : 13);
      }
    });
    // Restore original paint order first, then lift every fragment of the
    // hovered identity (including fragments on other selected channels).
    eventPaintOrder.forEach(n=>n.parentNode?.append(n));
    if(raisedEvent)eventNodes.get(raisedEvent)?.forEach(n=>n.parentNode?.append(n));
    document.querySelectorAll('.event-legend-item').forEach(b=>b.classList.toggle('is-event-hovered',b.dataset.eventKey===raisedEvent));
  }
  function registerEventNode(key, element) {
    if(!eventNodes.has(key))eventNodes.set(key,[]);
    eventNodes.get(key).push(element);eventPaintOrder.push(element);
  }
  function buildLegends() {
    const channels=$('chart-channel-legend'), events=$('chart-event-legend');
    channels.replaceChildren(); events.replaceChildren(); eventMap=new Map();
    hoveredChannel=null;hoveredEvent=null;focusedEvent=null;
    if (!data) return;
    for (const c of data.channels) {
      const button=document.createElement('button'); button.type='button'; button.className='channel-legend-item'; button.dataset.channel=c.channel;
      const swatch=document.createElement('span'); swatch.className='event-swatch'; swatch.style.backgroundColor=c.color;
      button.append(swatch,document.createTextNode(`${c.channel} · ${c.kind==='categorical' ? '类别状态' : c.unit} · ${c.raw_count.toLocaleString()} 点`));
      button.addEventListener('pointerenter',()=>{hoveredChannel=c.channel;applyHighlights();});
      button.addEventListener('pointerleave',()=>{hoveredChannel=null;applyHighlights();});
      button.addEventListener('focus',()=>{hoveredChannel=c.channel;applyHighlights();});
      button.addEventListener('blur',()=>{hoveredChannel=null;applyHighlights();});
      button.addEventListener('click',()=>{pinnedChannel=pinnedChannel===c.channel ? null : c.channel;applyHighlights();});
      channels.append(button);
      c.events.forEach(e=>{
        const prior=eventMap.get(e.key);
        if(!prior)eventMap.set(e.key,{...e,channels:[c.channel]});
        else {
          prior.channels.push(c.channel);
          if(Date.parse(e.event_start)<Date.parse(prior.event_start))prior.event_start=e.event_start;
        }
      });
    }
    if(activeEvent && !eventMap.has(activeEvent))activeEvent=null;
    for (const e of eventMap.values()) {
      const button=document.createElement('button'); button.type='button'; button.className='event-legend-item'; button.dataset.eventId=e.id;button.dataset.eventKey=e.key;
      button.setAttribute('aria-pressed',String(activeEvent===e.key));
      button.title=`点击跳到事件起始日并缩小至当天（UTC）：${e.event_start || e.intervals[0]?.start_time}`;
      const swatch=document.createElement('span'); swatch.className='event-swatch'; swatch.style.backgroundColor=e.color;
      button.append(swatch,document.createTextNode(`${e.id} · ${categoryNames[e.category] || e.category}${e.selected ? '' : '（未选类别）'}`));
      button.addEventListener('pointerenter',()=>{hoveredEvent=e.key;applyHighlights();});
      button.addEventListener('pointerleave',()=>{if(hoveredEvent===e.key)hoveredEvent=null;applyHighlights();});
      button.addEventListener('focus',()=>{if(button.matches(':focus-visible'))focusedEvent=e.key;applyHighlights();});
      button.addEventListener('blur',()=>{if(focusedEvent===e.key)focusedEvent=null;applyHighlights();});
      button.addEventListener('click',()=>jumpToEvent(e));
      events.append(button);
    }
    if (!eventMap.size) events.textContent='当前视窗未匹配有效真实事件标签，不代表已逐点确认正常。';
    $('chart-event-colors').disabled=data.channels.length!==1;
  }
  function jumpToEvent(event) {
    const time=Date.parse(event.event_start || event.intervals[0]?.start_time);
    if(stale || !Number.isFinite(time))return;
    const stamp=new Date(time),day=Date.UTC(stamp.getUTCFullYear(),stamp.getUTCMonth(),stamp.getUTCDate());
    // A calendar day is centered at UTC noon. At the dataset boundary, clip
    // the day rather than shifting it into a different date to retain 24 h.
    const start=Math.max(domain[0],day),end=Math.min(domain[1],day+DAY);
    const target=clamp(start,end);
    if(target[0]!==view[0] || target[1]!==view[1])remember();
    activeEvent=event.key;hoveredChannel=null;
    if(pinnedChannel && !event.channels.includes(pinnedChannel))pinnedChannel=null;
    setView(start,end,{eventJump:true,delay:0});
    const message=$('chart-event-navigation');message.hidden=false;
    const boundaryNote=end<=start?'（末端点使用最小可用视窗）':end-start<DAY?'（数据集边界已裁剪）':'';
    message.textContent=`已定位真实事件 ${event.id} · ${iso(day).slice(0,10)}（UTC 起始日） · 日视窗 ${iso(view[0])} → ${iso(view[1])}${boundaryNote}；可返回上一视图。`;
  }
  function drawTracks(width) {
    tracks.replaceChildren();
    tracks.setAttribute('width',width); tracks.style.width=`${width}px`;
    tracks.setAttribute('height',Math.max(42,(data?.channels.length || 0)*22+24));
    if (!data) return;
    tracks.append(node('text',{x:8,y:14,class:'track-label'},'真实标签（不是预测）'));
    data.channels.forEach((c,index)=>{
      const y=24+index*22;
      tracks.append(node('text',{x:6,y:y+11,class:'track-label'},c.channel));
      tracks.append(node('line',{x1:plot.left,x2:plot.right,y1:y+6,y2:y+6,class:'track-axis'}));
      c.events.forEach(e=>e.intervals.forEach(i=>{
        const start=Date.parse(i.start_time), end=Date.parse(i.end_time);
        if (end<view[0] || start>view[1]) return;
        const rect=node('rect',{x:plot.x(Math.max(start,view[0])),y,width:Math.max(3,plot.x(Math.min(end,view[1]))-plot.x(Math.max(start,view[0]))),height:13,rx:2,fill:e.color,class:'event-track-item','data-base-y':y,'data-event-id':e.id,'data-event-key':e.key});
        rect.append(node('title',{},`${c.channel} | ${e.id} | ${e.category} | ${i.start_time} → ${i.end_time}`));
        registerEventNode(e.key,rect);tracks.append(rect);
      }));
    });
  }
  function draw() {
    if(stale)return;
    hideHover(); svg.replaceChildren(); waveNodes=new Map(); eventNodes=new Map();eventPaintOrder=[];
    const width=Math.max(900,scroll.clientWidth-2), left=125, right=width-32;
    const {lanes,height}=makeLanes();
    svg.setAttribute('width',width); svg.style.width=`${width}px`; svg.setAttribute('height',height); svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    plot={left,right,lanes,x:t=>left+(t-view[0])/(view[1]-view[0])*(right-left),inverse:x=>view[0]+(x-left)/(right-left)*(view[1]-view[0])};
    const defs=node('defs'); svg.append(defs);
    const grid=node('g',{class:'chart-grid'}); const axisTicks=ticks(...view); let prior=-Infinity;
    for (const t of axisTicks.values) {
      const px=plot.x(t.time); grid.append(node('line',{x1:px,x2:px,y1:32,y2:height-37}));
      if (px-prior>85) {grid.append(node('text',{x:px,y:height-12,'text-anchor':'middle'},t.label));prior=px;}
    }
    const waves=node('g',{class:'waveform-stack'});
    lanes.forEach((lane,laneIndex)=>{
      const clipId=`lane-${laneIndex}`; const clip=node('clipPath',{id:clipId});
      clip.append(node('rect',{x:left,y:lane.top,width:right-left,height:lane.height})); defs.append(clip);
      lane.clip=clipId;
      const first=lane.channels[0];
      const label=first?.kind==='categorical' ? `${first.channel} · 状态` : lane.normalized ? '参考标准化 · 仅绘图' : lane.channels.length===1 ? `${first?.channel || ''} · 原始值` : '原始值叠加 · 注意单位差异';
      grid.append(node('text',{x:left,y:lane.top-10},label));
      const yTicks=first?.kind==='categorical' ? first.categories.map((name,i)=>({v:i,text:name})).filter(r=>r.v%Math.max(1,Math.ceil(first.categories.length/8))===0) : Array.from({length:5},(_,i)=>{const v=lane.min+(lane.max-lane.min)*i/4;return {v,text:Number(v.toPrecision(5)).toString()};});
      yTicks.forEach(t=>{grid.append(node('line',{x1:left,x2:right,y1:lane.y(t.v),y2:lane.y(t.v)}));grid.append(node('text',{x:left-10,y:lane.y(t.v)+4,'text-anchor':'end'},t.text));});
      const background=node('g',{'clip-path':`url(#${clipId})`});
      const evaluation=[Date.parse(page.chart.start),Date.parse(page.chart.end)];
      for(const [a,b] of [[view[0],Math.min(view[1],evaluation[0])],[Math.max(view[0],evaluation[1]),view[1]]]) if(b>a){
        background.append(node('rect',{x:plot.x(a),y:lane.top,width:plot.x(b)-plot.x(a),height:lane.height,class:'outside-evaluation'}));
        if(plot.x(b)-plot.x(a)>130) background.append(node('text',{x:plot.x(a)+6,y:lane.top+18,class:'outside-label'},'本次评分范围外'));
      }
      for(const [a,b] of unionDetections(lane.channels)) bandRect(background,a,b,lane.top,lane.height,{class:`detection-shadow${a===b ? ' point-detection' : ''}`,'data-start':iso(a),'data-end':iso(b)});
      svg.append(background);
      lane.channels.forEach(c=>{
        const group=node('g',{class:'series-layer','data-channel':c.channel,'clip-path':`url(#${clipId})`}); waveNodes.set(c.channel,group);
        if(c.bins.length) for(const segment of c.trend_segments) {
          const shown=crop(segment,...view); if(!shown.length)continue;
          const envelope=(lower,upper,css)=>{
            const path=shown.map((p,i)=>`${i?'L':'M'}${plot.x(timeOf(p))},${lane.y(transform(c,c.bins[p[2]][lower],lane))}`).join(' ')
              + [...shown].reverse().map(p=>`L${plot.x(timeOf(p))},${lane.y(transform(c,c.bins[p[2]][upper],lane))}`).join(' ')+'Z';
            group.append(node('path',{d:path,fill:c.color,class:css}));
          };
          if($('chart-extrema').checked)envelope('min','max','extrema-envelope');
          envelope('p05','p95','quantile-envelope');
        }
        for(const segment of plotted(c)) group.append(node('path',{d:pathFor(c,segment,lane),fill:'none',stroke:c.color,class:'raw-curve',style:`stroke:${c.color}`}));
        if(data.channels.length===1 && $('chart-event-colors').checked) c.events.forEach((e,eIndex)=>{
          const eventClip=node('clipPath',{id:`event-${laneIndex}-${eIndex}`});
          e.intervals.forEach(i=>{const a=Date.parse(i.start_time),b=Date.parse(i.end_time);eventClip.append(node('rect',{x:plot.x(a)-(a===b?1:0),y:lane.top,width:Math.max(2,plot.x(b)-plot.x(a)),height:lane.height}));});defs.append(eventClip);
          const overlay=node('g',{'clip-path':`url(#event-${laneIndex}-${eIndex})`,class:'event-overlay','data-event-id':e.id});
          for(const segment of plotted(c)) for(const interval of e.intervals) {
            const a=Date.parse(interval.start_time),b=Date.parse(interval.end_time);
            if(timeOf(segment.at(-1))<a || timeOf(segment[0])>b)continue;
            overlay.append(node('path',{d:pathFor(c,segment,lane,a,b),fill:'none',stroke:e.color,class:'event-curve',style:`stroke:${e.color}`}));
          }
          registerEventNode(e.key,overlay);group.append(overlay);
        });
        waves.append(group);
      });
      if(!lane.channels.some(c=>c.raw_count))svg.append(node('text',{x:left+20,y:lane.top+60,class:'empty-chart'},'此窗口没有原始采样，请缩小或移动视窗。'));
    });
    svg.prepend(grid); svg.append(waves);
    if(checkpointIndex>=0 && checkpoints[checkpointIndex]){
      const p=checkpoints[checkpointIndex],g=node('g',{class:'checkpoint-highlight','pointer-events':'none'});
      bandRect(g,Date.parse(p.start),Date.parse(p.end),32,height-69,{fill:'none',stroke:'#d97706','stroke-width':2,'stroke-dasharray':'5 4'});svg.append(g);
    }
    lanes.forEach(l=>svg.append(node('rect',{x:left,y:l.top,width:right-left,height:l.height,class:'chart-hit-area'})));
    const hoverGroup=node('g',{class:'chart-hover',visibility:'hidden','pointer-events':'none'});
    const guide=node('line',{y1:32,y2:height-37,class:'hover-guide'}),marker=node('circle',{r:4.5,class:'hover-marker'});
    hoverGroup.append(guide,marker);svg.append(hoverGroup);hover={group:hoverGroup,guide,marker};
    drawTracks(width); drawOverview(); applyHighlights(); updateRangeControls();
  }

  function drawOverview() {
    overview.replaceChildren();const width=Math.max(300,overview.clientWidth || 1000),left=12,right=width-12;
    overview.setAttribute('viewBox',`0 0 ${width} 85`);
    const x=t=>left+(t-domain[0])/(domain[1]-domain[0])*(right-left);
    overview.append(node('rect',{x:left,y:15,width:right-left,height:36,rx:4,class:'overview-domain'}));
    const evalStart=Math.max(domain[0],Date.parse(page.chart.start)),evalEnd=Math.min(domain[1],Date.parse(page.chart.end));
    if(evalEnd>evalStart)overview.append(node('rect',{x:x(evalStart),y:15,width:x(evalEnd)-x(evalStart),height:36,class:'overview-evaluation'}));
    const columns=new Map();checkpoints.forEach(p=>{const px=Math.round(x(Date.parse(p.start)));columns.set(px,(columns.get(px)||0)+1);});
    for(const [px,count]of columns)overview.append(node('line',{x1:px,x2:px,y1:48-Math.min(24,3+Math.log2(count+1)*4),y2:49,class:'overview-prediction'}));
    const a=x(view[0]),b=x(view[1]);
    overview.append(node('rect',{id:'chart-overview-window',x:a,y:12,width:Math.max(12,b-a),height:42,rx:3,class:'overview-window','data-start':iso(view[0]),'data-end':iso(view[1])}));
    overview.append(node('rect',{x:a-4,y:11,width:8,height:44,class:'overview-handle','data-edge':'left'}));
    overview.append(node('rect',{x:Math.max(a+8,b)-4,y:11,width:8,height:44,class:'overview-handle','data-edge':'right'}));
    let last=-Infinity;for(const t of ticks(...domain).values){const px=x(t.time);if(px-last>85){overview.append(node('text',{x:px,y:75,'text-anchor':'middle',class:'overview-label'},t.label));last=px;}}
  }
  function checkpointText() {
    const p=checkpoints[checkpointIndex];
    $('chart-checkpoint-status').textContent=p ? `算法预测 ${checkpointIndex+1}/${checkpoints.length} · ${p.channels.join(', ')} · ${p.start} → ${p.end}${p.point?' · 点检测':''}` : checkpoints.length ? `已选通道共 ${checkpoints.length} 个预测导航检查点（重叠报警合并，不是真实事件）。` : data?.has_scored_predictions ? '已选通道没有预测检查点。' : '评测后可导航已选通道的预测区间。';
    updateRangeControls();
  }
  function jump(direction) {
    if(!checkpoints.length || stale)return;
    let index;
    if(checkpointIndex>=0)index=checkpointIndex+direction;
    else if(direction>0)index=checkpoints.findIndex(p=>Date.parse(p.start)>=view[0]);
    else {index=checkpoints.length-1;while(index>=0 && Date.parse(checkpoints[index].start)>view[1])--index;}
    if(index<0 || index>=checkpoints.length){$('chart-checkpoint-status').textContent=direction>0?'已到最后一个预测检查点。':'已到第一个预测检查点。';return;}
    remember();checkpointIndex=index;const p=checkpoints[index],a=Date.parse(p.start),b=Date.parse(p.end);
    const span=Math.max(view[1]-view[0],(b-a)*3,minSpan),center=(a+b)/2;
    setView(center-span/2,center+span/2,{keepCheckpoint:true,delay:0});checkpointText();
  }
  function focusCheckpoint() {
    const p=checkpoints[checkpointIndex];if(!p)return;
    remember();const a=Date.parse(p.start),b=Date.parse(p.end),cadence=Math.max(1,...(data?.channels.map(c=>c.cadence_seconds)||[1]));
    const span=Math.max((b-a)*3,cadence*20000,60000,minSpan),center=(a+b)/2;
    setView(center-span/2,center+span/2,{keepCheckpoint:true,delay:0});checkpointText();
  }
  async function load() {
    if(stale)return;clearTimeout(timer);if(controller)controller.abort();controller=new AbortController();const requestId=++serial;
    const range=[...view], names=[...selected];if(!names.length)return;
    const resultToken=document.querySelector('form[action="/download/json"] input[name=token]');
    error.hidden=true;status.textContent=`正在读取 ${names.length} 个通道并计算显示数据… 首次加载真实通道可能需要数秒。`;
    try{
      const response=await fetch('/api/series',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({kind:resultToken?'result':'chart',token:resultToken?resultToken.value:page.chart.token,channels:names,view_start:iso(range[0]),view_end:iso(range[1]),display_mode:mode.value,bandwidth_seconds:$('chart-bandwidth').value,pixel_width:Math.max(900,scroll.clientWidth-2),include_checkpoints:needCheckpoints})});
      const body=await response.json();if(!response.ok)throw new Error(body.error || '图表数据读取失败');if(requestId!==serial || stale)return;
      data=body;if(body.checkpoints){checkpoints=body.checkpoints;needCheckpoints=false;checkpointIndex=-1;}
      if(pinnedChannel && !names.includes(pinnedChannel))pinnedChannel=null;
      buildLegends();draw();checkpointText();
      const raw=body.channels.reduce((n,c)=>n+c.raw_count,0),shown=body.channels.reduce((n,c)=>n+c.displayed_count,0);
      const modes={raw:'原始折线',states:'状态序列',aggregate:'时间桶中位数',moving:'时间窗移动平均',kernel:'高斯核平滑'};
      const kinds=[...new Set(body.channels.map(c=>modes[c.mode]))].join('／');
      status.textContent=`${body.window_start} → ${body.window_end} · ${body.channels.length} 个通道，原始 ${raw.toLocaleString()} 点，显示 ${shown.toLocaleString()} 点 · ${kinds}。${body.channels.some(c=>['moving','kernel'].includes(c.mode))?`显示平滑窗口／带宽 ${body.bandwidth_seconds.toPrecision(4)} 秒，基于时间桶统计。`:''}${body.channels.some(c=>c.kind==='categorical')?'类别通道保持状态序列，不参与数值平滑。':''}处理仅用于显示，不改变检测和评分。${body.source==='demo'?'当前为演示合成序列。':''}`;
    }catch(problem){if(problem.name==='AbortError' || requestId!==serial)return;status.textContent='图表读取失败，评分结果保持不变。';error.textContent=problem.message;error.hidden=false;}
  }

  function pointerPosition(event,target) {return new DOMPoint(event.clientX,event.clientY).matrixTransform(target.getScreenCTM().inverse());}
  svg.addEventListener('wheel',event=>{
    if(stale || !plot)return;const p=pointerPosition(event,svg);if(p.x<plot.left || p.x>plot.right)return;
    event.preventDefault();if(!wheelActive){remember();wheelActive=true;}clearTimeout(wheelEnd);wheelEnd=setTimeout(()=>{wheelActive=false;},300);
    const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?scroll.clientHeight:1);
    const span=view[1]-view[0];if(event.shiftKey){const move=delta/500*span;setView(view[0]+move,view[1]+move);return;}
    const fraction=Math.max(0,Math.min(1,(p.x-plot.left)/(plot.right-plot.left))),anchor=view[0]+fraction*span;
    const nextSpan=Math.max(minSpan,Math.min(domain[1]-domain[0],span*Math.exp(Math.max(-1,Math.min(1,delta*.0015)))));
    setView(anchor-fraction*nextSpan,anchor+(1-fraction)*nextSpan);
  },{passive:false});
  svg.addEventListener('pointerdown',event=>{
    if(event.button!==0 || stale || !plot)return;const p=pointerPosition(event,svg);
    if(p.x<plot.left || p.x>plot.right)return;
    drag={x:p.x,range:[...view],moved:false};svg.setPointerCapture(event.pointerId);hideHover();
  });
  svg.addEventListener('pointermove',event=>{
    if(!plot)return;const cursor=pointerPosition(event,svg);
    if(drag){const dx=cursor.x-drag.x;if(Math.abs(dx)>3 && !drag.moved){remember();drag.moved=true;}if(drag.moved){const shift=-dx/(plot.right-plot.left)*(drag.range[1]-drag.range[0]);setView(drag.range[0]+shift,drag.range[1]+shift,{request:false});}return;}
    if(!data || !hover || cursor.x<plot.left || cursor.x>plot.right){hideHover();return;}
    const time=plot.inverse(cursor.x);let nearest=null;const active=hoveredChannel || pinnedChannel;
    for(const lane of plot.lanes){if(cursor.y<lane.top || cursor.y>lane.bottom)continue;
      for(const c of lane.channels){if(active && c.channel!==active)continue;
        for(const segment of plotted(c)){
          if(!segment.length)continue;const first=timeOf(segment[0]),last=timeOf(segment.at(-1));
          if(segment.length===1?Math.abs(plot.x(first)-cursor.x)>8:time<first || time>last)continue;
          let lo=0,hi=segment.length-1;while(lo<hi){const m=(lo+hi)>>1;if(timeOf(segment[m])<time)lo=m+1;else hi=m;}
          let point=segment[lo];if(lo>0 && Math.abs(timeOf(segment[lo-1])-time)<Math.abs(timeOf(point)-time))point=segment[lo-1];
          const py=lane.y(transform(c,point[1],lane)),distance=Math.abs(py-cursor.y);
          if(!nearest || distance<nearest.distance)nearest={c,lane,point,py,distance};
        }
      }
    }
    if(!nearest){hideHover();return;}
    const {c,lane,point,py}=nearest;hover.group.setAttribute('visibility','visible');const px=plot.x(timeOf(point));
    hover.marker.setAttribute('cx',px);hover.marker.setAttribute('cy',py);hover.marker.setAttribute('stroke',c.color);hover.guide.setAttribute('x1',px);hover.guide.setAttribute('x2',px);
    if(c.mode==='raw' || c.mode==='states'){
      const value=c.kind==='categorical'?c.categories[point[1]]:point[1];
      readout.textContent=`原始显示采样 · ${c.channel} · 时间 ${point[2]} · 数值／状态 ${value} · 通道内第 ${(point[3]+1).toLocaleString()} 条 · 事件 ${point[4].join('、') || '未覆盖有效标签（不等于确认正常）'}${c.downsampled?' · 当前经过显示抽样':''}`;
    }else{
      const b=c.bins[point[2]];readout.textContent=`聚合桶（非原始采样点） · ${c.channel} · ${b.start_time} → ${b.end_time} · ${b.count.toLocaleString()} 条 · 中位数 ${b.median} · 均值 ${b.mean} · 最小 ${b.min}／最大 ${b.max} · P5 ${b.p05}／P95 ${b.p95} · 显示趋势值 ${point[1]}`;
    }
    if(lane.normalized)readout.textContent+=` · 绘图标准化值 ${transform(c,point[1],lane).toPrecision(5)}（原始值见前）`;
    hint.hidden=true;readout.hidden=false;
  });
  function endDrag(event){if(!drag)return;const moved=drag.moved;drag=null;if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);if(moved)schedule(0);}
  svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);svg.addEventListener('pointerleave',()=>{if(!drag)hideHover();});

  overview.addEventListener('pointerdown',event=>{
    if(stale || event.button!==0)return;remember();const p=pointerPosition(event,overview),width=overview.viewBox.baseVal.width;
    const time=domain[0]+(p.x-12)/(width-24)*(domain[1]-domain[0]);
    overviewDrag={start:time,range:[...view],edge:event.target.dataset.edge || (time>=view[0] && time<=view[1]?'body':'jump')};
    overview.setPointerCapture(event.pointerId);
    if(overviewDrag.edge==='jump'){const span=view[1]-view[0];setView(time-span/2,time+span/2,{request:false});overviewDrag.range=[...view];overviewDrag.edge='body';}
  });
  overview.addEventListener('pointermove',event=>{
    if(!overviewDrag)return;const p=pointerPosition(event,overview),width=overview.viewBox.baseVal.width;
    const time=domain[0]+(p.x-12)/(width-24)*(domain[1]-domain[0]),d=overviewDrag;
    if(d.edge==='left')setView(Math.max(domain[0],Math.min(d.range[1]-minSpan,time)),d.range[1],{request:false});
    else if(d.edge==='right')setView(d.range[0],Math.min(domain[1],Math.max(d.range[0]+minSpan,time)),{request:false});
    else {const shift=time-d.start;setView(d.range[0]+shift,d.range[1]+shift,{request:false});}
  });
  function endOverview(event){if(!overviewDrag)return;overviewDrag=null;if(overview.hasPointerCapture(event.pointerId))overview.releasePointerCapture(event.pointerId);schedule(0);}
  overview.addEventListener('pointerup',endOverview);overview.addEventListener('pointercancel',endOverview);

  function countChannels(){ $('chart-channel-count').textContent=allBoxes.filter(b=>b.checked).length; }
  allBoxes.forEach(b=>b.addEventListener('change',countChannels));
  $('chart-channel-search').addEventListener('input',event=>{const text=event.target.value.toLowerCase();allBoxes.forEach(b=>{b.parentElement.hidden=!b.value.toLowerCase().includes(text);});});
  $('chart-select-all').addEventListener('click',()=>{allBoxes.forEach(b=>{b.checked=true;});countChannels();});
  $('chart-select-first').addEventListener('click',()=>{const first=allBoxes.find(b=>b.checked)||allBoxes[0];allBoxes.forEach(b=>{b.checked=b===first;});countChannels();});
  $('chart-apply-channels').addEventListener('click',()=>{
    const names=allBoxes.filter(b=>b.checked).map(b=>b.value);if(!names.length){error.textContent='请至少选择一个通道。';error.hidden=false;return;}
    selected=names;needCheckpoints=true;checkpoints=[];checkpointIndex=-1;activeEvent=null;hoveredChannel=null;pinnedChannel=null;data=null;
    document.querySelector('.chart-channel-picker').open=false;schedule(0);renderSoon();
  });
  preset.addEventListener('change',()=>{
    if(preset.value==='custom')return;remember();const lengths={year:366*DAY,month:30*DAY,week:7*DAY,day:DAY,hour:3600000};
    if(preset.value==='full')setView(...domain,{delay:0});else {const span=lengths[preset.value],center=(view[0]+view[1])/2;setView(center-span/2,center+span/2,{delay:0});}
  });
  date.addEventListener('change',()=>{const start=Date.parse(`${date.value}Z`);if(Number.isFinite(start)){remember();setView(start,start+view[1]-view[0],{delay:0});}});
  $('chart-previous').addEventListener('click',()=>{remember();const shift=(view[1]-view[0])*.8;setView(view[0]-shift,view[1]-shift,{delay:0});});
  $('chart-next').addEventListener('click',()=>{remember();const shift=(view[1]-view[0])*.8;setView(view[0]+shift,view[1]+shift,{delay:0});});
  $('chart-refresh').addEventListener('click',()=>schedule(0));mode.addEventListener('change',()=>schedule(0));$('chart-bandwidth').addEventListener('change',()=>schedule(0));
  [layout,$('chart-extrema'),$('chart-event-colors')].forEach(e=>e.addEventListener('change',()=>renderSoon()));
  $('chart-pred-next').addEventListener('click',()=>jump(1));$('chart-pred-previous').addEventListener('click',()=>jump(-1));$('chart-pred-focus').addEventListener('click',focusCheckpoint);
  $('chart-back').addEventListener('click',()=>{const old=history.pop();if(old){checkpointIndex=old.checkpoint;setView(old.start,old.end,{keepCheckpoint:true,delay:0});checkpointText();}});
  document.addEventListener('keydown',event=>{if(stale || event.ctrlKey || event.metaKey || event.altKey || event.target.matches('input,textarea,select,[contenteditable=true]'))return;if(event.key.toLowerCase()==='j'){event.preventDefault();jump(1);}if(event.key.toLowerCase()==='k'){event.preventDefault();jump(-1);}});
  scroll.addEventListener('scroll',hideHover);window.addEventListener('blur',hideHover);
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{renderSoon();schedule(0);},200);});
  window.addEventListener('esa:invalidate-chart',()=>{
    stale=true;clearTimeout(timer);if(controller)controller.abort();++serial;hideHover();data=null;svg.replaceChildren();tracks.replaceChildren();overview.replaceChildren();
    document.querySelectorAll('#raw-visualization input,#raw-visualization select,#raw-visualization button').forEach(e=>{e.disabled=true;});
    $('chart-channel-legend').replaceChildren();$('chart-event-legend').replaceChildren();status.textContent='输入或评分配置已改变，旧图表与预测已失效；重新提交后恢复浏览。';
  });
  date.min=iso(domain[0]).slice(0,19);date.max=iso(domain[1]).slice(0,19);updateRangeControls();draw();load();
})();
