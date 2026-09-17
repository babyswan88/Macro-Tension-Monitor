(function(){
'use strict';
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const getJSON=p=>fetch(p+(p.includes('?')?'&':'?')+'v='+Date.now()).then(r=>{if(!r.ok)throw new Error(p+' '+r.status);return r.json();});
const today=new Date().toISOString().slice(0,10);

function fmt(v,u){
  if(v==null)return '—';
  const s=Math.abs(v)>=1000?Math.round(v).toLocaleString('en-US'):(Number.isInteger(v)&&(u==='bp'||u==='$B'||u==='$')?String(v):v.toFixed(2));
  if(u==='$B')return '$'+s+'B';
  if(u==='$')return '$'+s;
  return s;
}
const unitSuffix=u=>(u==='%'||u==='bp'||u==='$/gal')?u:'';

function classify(ind,r){
  if(!r||r.value==null)return 'gray';
  const v=r.value,near=(ind.hi-ind.lo)*0.15;
  if(ind.dir==='above'){if(v>=ind.trigger)return 'red';if(v>=ind.trigger-near)return 'amber';return 'green';}
  if(v<=ind.trigger)return 'red';if(v<=ind.trigger+near)return 'amber';return 'green';
}
function dist(ind,r){
  if(!r||r.value==null)return 'no reading';
  const d=ind.trigger-r.value,abs=Math.abs(d);
  const u=ind.unit==='%'?'bp':(ind.unit==='bp'?'bp':(ind.unit==='$'||ind.unit==='$B'?'$':''));
  let n=ind.unit==='%'?Math.round(abs*100):(abs>=100?Math.round(abs):+abs.toFixed(2));
  const through=(ind.dir==='above'&&d<=0)||(ind.dir==='below'&&d>=0);
  const amt=u==='$'?'$'+n:n+(u?' '+u:'');
  if(through)return n===0?'at trigger':amt+' through';
  return amt+' to trigger';
}
function counts(dash,data){
  const c={red:0,amber:0,green:0,gray:0};
  dash.indicators.forEach(i=>c[classify(i,data.indicators[i.id])]++);
  return c;
}
function ageDays(asof){if(!asof)return null;return Math.round((Date.parse(today)-Date.parse(asof))/864e5);}

function card(ind,r,openSet){
  const c=classify(ind,r),v=r?r.value:null;
  const pct=v==null?0:Math.max(0,Math.min(100,(v-ind.lo)/(ind.hi-ind.lo)*100));
  const tpct=Math.max(0,Math.min(100,(ind.trigger-ind.lo)/(ind.hi-ind.lo)*100));
  const pills=[];
  if(r&&r.stale)pills.push('<span class="pill stale" title="'+esc(r.error||'Latest fetch failed')+'">stale</span>');
  if(r&&r.manual)pills.push('<span class="pill" title="Entered by hand">manual</span>');
  const a=ind.about||[];
  const src=r&&r.source?'<dt>Source</dt><dd class="src">'+esc(r.source)+(r.fetched?' · fetched '+esc(r.fetched.slice(0,10)):'')+'</dd>':'';
  return '<div class="card">'+
    '<div class="label">'+esc(ind.label)+(ind.sub?'<small>'+esc(ind.sub)+'</small>':'')+'</div>'+
    '<div class="val '+c+'">'+fmt(v,ind.unit)+'<span class="u">'+unitSuffix(ind.unit)+'</span></div>'+
    '<div class="gauge" role="img" aria-label="'+esc(ind.label+': '+fmt(v,ind.unit)+', trigger '+fmt(ind.trigger,ind.unit))+'"><div class="fill '+c+'" style="width:'+pct+'%"></div><div class="tick" style="left:'+tpct+'%" data-t="'+esc(fmt(ind.trigger,ind.unit)+unitSuffix(ind.unit))+'"></div></div>'+
    '<div class="meta"><span class="dist">'+dist(ind,r)+'</span><span class="right">'+pills.join('')+(r&&r.asof?'<span>as of '+esc(r.asof)+'</span>':'')+'</span></div>'+
    (a.length?'<details class="about" data-about="'+esc(ind.id)+'"'+(openSet.has(ind.id)?' open':'')+'><summary>About this measure</summary><dl><dt>What it is</dt><dd>'+esc(a[0])+'</dd><dt>How to read it</dt><dd>'+esc(a[1])+'</dd><dt>Why it matters</dt><dd>'+esc(a[2])+'</dd>'+src+'</dl></details>':'')+
    '</div>';
}

async function dashboardPage(){
  const app=document.getElementById('app');
  const id=new URLSearchParams(location.search).get('d')||'liquidity';
  let dash,data,events=[],news=[];
  try{
    [dash,data]=await Promise.all([getJSON('dashboards/'+encodeURIComponent(id)+'.json'),getJSON('data/latest.json')]);
  }catch(e){app.innerHTML='<p class="empty">This dashboard could not load. Check the address, or try again in a minute.</p>';return;}
  try{events=await getJSON('data/events.json');}catch(e){}
  try{news=await getJSON('data/news/'+encodeURIComponent(id)+'.json');}catch(e){}
  document.title=dash.title+' · Macro Tension Monitor';
  document.getElementById('title').textContent=dash.title;
  document.getElementById('tagline').textContent=dash.tagline||'';
  document.getElementById('status').textContent='Data updated '+(data.generated||'').replace('T',' ').slice(0,16)+' UTC';
  const openSet=new Set();
  function render(){
    const c=counts(dash,data);
    let h='<div class="summary">'+
      '<div class="tile red"><div class="n">'+c.red+'</div><div class="l">at or through trigger</div></div>'+
      '<div class="tile amber"><div class="n">'+c.amber+'</div><div class="l">within 15% of range of trigger</div></div>'+
      '<div class="tile"><div class="n">'+c.gray+'</div><div class="l">no reading yet</div></div></div>';
    for(const [g,title,why] of dash.groups){
      const inds=dash.indicators.filter(i=>i.g===g);if(!inds.length)continue;
      h+='<section><h2>'+esc(title)+'</h2><p class="why">'+esc(why)+'</p><div class="grid">'+inds.map(i=>card(i,data.indicators[i.id],openSet)).join('')+'</div></section>';
    }
    // news
    const items=[...news].sort((a,b)=>b.date.localeCompare(a.date)).slice(0,25);
    h+='<section><h2>News</h2><p class="why">Headlines that moved or could move these levels, newest first. Summaries are written for this site; follow the link for the source.</p><div class="list news">';
    h+=items.length?items.map(n=>'<div class="row"><span class="d">'+esc(n.date)+'</span><div><a class="h" href="'+esc(n.url)+'" target="_blank" rel="noopener">'+esc(n.headline)+'</a><span class="tag">'+esc(n.source||'')+'</span>'+(n.summary?'<p>'+esc(n.summary)+'</p>':'')+'</div></div>').join(''):'<p class="empty">No news entries yet.</p>';
    h+='</div></section>';
    // events: curated + announced auctions
    const terms=new Set(dash.auction_terms||[]),tags=new Set(dash.event_tags||[]);
    const ev=events.filter(e=>(e.tags||[]).some(t=>tags.has(t))).map(e=>({date:e.date,label:e.label}));
    (data.auctions||[]).filter(a=>a.date>=today&&terms.has(a.term)).forEach(a=>{
      const lbl=a.term.replace('-Year','y')+(a.term==='20-Year'||a.term==='30-Year'?' bond':' note')+' auction'+(a.reopening?' (reopening)':'');
      if(!ev.some(e=>e.date===a.date&&e.label.toLowerCase().includes(a.term.replace('-Year','y').toLowerCase())))ev.push({date:a.date,label:lbl});
    });
    ev.sort((a,b)=>a.date.localeCompare(b.date));
    const upcoming=ev.filter(e=>e.date>=today).slice(0,15),recent=ev.filter(e=>e.date<today).slice(-3);
    h+='<section><h2>Dated events</h2><p class="why">Scheduled tests of demand and policy. Treasury auctions appear automatically once announced.</p><div class="list">';
    h+=[...recent,...upcoming].map(e=>'<div class="row"><span class="d'+(e.date<today?' past':'')+'">'+esc(e.date)+'</span><span>'+esc(e.label)+'</span></div>').join('')||'<p class="empty">No events scheduled.</p>';
    h+='</div></section>';
    const failed=(data.failed||[]).filter(f=>dash.indicators.some(i=>i.id===f));
    h+='<p class="note">Gauges run from the low to high end of each indicator\'s plausible range; the tick is the trigger. Red is at or through the trigger, amber is within 15% of the range, gray is no reading. These are levels of market tension, not trade recommendations. Data comes from free public sources and is refreshed on weekdays; cards marked stale kept their last good reading because the latest fetch failed'+(failed.length?' (this run: '+esc(failed.join(', '))+')':'')+'. Always check the as-of date.</p>';
    app.innerHTML=h;
  }
  app.addEventListener('toggle',e=>{const d=e.target;if(d.dataset&&d.dataset.about){d.open?openSet.add(d.dataset.about):openSet.delete(d.dataset.about);}},true);
  render();
}

async function hubPage(){
  const app=document.getElementById('app');
  let idx,data;
  try{[idx,data]=await Promise.all([getJSON('dashboards/index.json'),getJSON('data/latest.json')]);}
  catch(e){app.innerHTML='<p class="empty">Dashboards could not load. Try again in a minute.</p>';return;}
  document.getElementById('status').textContent='Data updated '+(data.generated||'').replace('T',' ').slice(0,16)+' UTC';
  const cards=await Promise.all(idx.dashboards.map(async d=>{
    if(d.status!=='live')return '<div class="dash soon"><h2>'+esc(d.title)+'</h2><p>'+esc(d.tagline)+'</p><div class="counts">Coming soon</div></div>';
    try{
      const full=await getJSON('dashboards/'+encodeURIComponent(d.id)+'.json');const c=counts(full,data);
      return '<a class="dash" href="dashboard.html?d='+encodeURIComponent(d.id)+'"><h2>'+esc(d.title)+'</h2><p>'+esc(d.tagline)+'</p><div class="counts"><span class="r"><b>'+c.red+'</b>through trigger</span><span class="a"><b>'+c.amber+'</b>near</span><span><b>'+full.indicators.length+'</b>tracked</span></div></a>';
    }catch(e){return '';}
  }));
  app.innerHTML='<div class="hub">'+cards.join('')+'</div><p class="note">Each dashboard tracks the levels where a macro theme starts to strain: how far each input sits from its trigger. These are levels of tension, not trade recommendations. Data comes from free public sources and is refreshed on weekdays.</p>';
}

window.MTM={dashboardPage,hubPage};
})();
