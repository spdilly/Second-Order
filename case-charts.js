/* Responsive, dependency-free chart primitives for the case-study pages. */
(() => {
  'use strict';
  const NS='http://www.w3.org/2000/svg';
  const colors=['#27dcc4','#edf4f3','#d4ac73','#8ba8c4'];
  const dash=['','7 4','2 4','10 3 2 3'];
  const fmt=(v,d=1)=>Math.abs(v)<1e-9?'0':Math.abs(v)>=1e6?(v/1e6).toFixed(d).replace(/\.0$/,'')+'m':Math.abs(v)>=1e3?(v/1000).toFixed(d).replace(/\.0$/,'')+'k':Number(v).toLocaleString('en-US',{maximumFractionDigits:d});
  function el(tag,attrs={},text){const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;}
  function label(svg,x,y,text,anchor='start',cls='chart-label'){const n=el('text',{x,y,'text-anchor':anchor,class:cls},text);svg.append(n);return n;}
  function multiline(svg,x,y,value,max,anchor='start'){
    const words=String(value).split(/\s+/),lines=[];let line='';
    for(const word of words){if(line&&(line+' '+word).length>max){lines.push(line);line=word;}else line+=(line?' ':'')+word;}if(line)lines.push(line);
    const n=el('text',{x,y,'text-anchor':anchor,class:'chart-label'});lines.forEach((v,i)=>n.append(el('tspan',{x,dy:i?16:0},v)));svg.append(n);return lines.length;
  }
  function domain(values,zero=true){let lo=Math.min(...values),hi=Math.max(...values);if(zero){lo=Math.min(0,lo);hi=Math.max(0,hi);}const gap=hi-lo||1;return[lo<0?lo-gap*.07:lo,hi+gap*.09];}
  function axes(svg,box,lo,hi,unit){
    const sy=v=>box.y+box.h-(v-lo)/(hi-lo)*box.h;
    for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=sy(v);svg.append(el('line',{x1:box.x,x2:box.x+box.w,y1:y,y2:y,class:'chart-grid'}));label(svg,box.x-12,y+4,fmt(v), 'end');}
    if(lo<0)svg.append(el('line',{x1:box.x,x2:box.x+box.w,y1:sy(0),y2:sy(0),class:'chart-zero'}));
    label(svg,box.x,18,unit||'');return sy;
  }
  function render(host,c){
    const colors=c.palette||['#27dcc4','#edf4f3','#d4ac73','#8ba8c4'];
    if(c.kind==='ledger'){
      const list=document.createElement('ol');list.className='closing-ledger';
      c.labels.forEach((name,i)=>{const item=document.createElement('li'),term=document.createElement('span'),value=document.createElement('strong');term.textContent=name;value.textContent=(c.values[i]>0&&!c.totals.includes(i)?'+':'')+fmt(c.values[i],2);item.className=c.totals.includes(i)?'ledger-total':'';item.append(term,value);list.append(item);});
      host.replaceChildren(list);host.dataset.rendered='true';return;
    }
    const available=Math.max(280,Math.floor(host.getBoundingClientRect().width));
    const dense=c.kind==='heatmap'||c.kind==='waterfall';
    const W=dense?Math.max(640,available):available,H=c.kind==='bar'?Math.max(340,c.categories.length*(c.series.length*20+42)+80):c.kind==='heatmap'?Math.max(390,c.y.length*38+120):370;
    const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,width:W,height:H,role:'img','aria-label':c.title+'. '+c.subtitle});
    svg.append(el('title',{},c.title),el('desc',{},c.subtitle));
    host.replaceChildren(svg);host.style.setProperty('--chart-width',W+'px');
    const tooltip=host.parentElement.querySelector('.chart-tooltip');
    function show(text,event){tooltip.textContent=text;tooltip.hidden=false;const r=host.parentElement.getBoundingClientRect();const x=event?.clientX?r.width?Math.min(r.width-260,Math.max(8,event.clientX-r.left+12)):8:8;tooltip.style.left=Math.max(8,x)+'px';tooltip.style.top=event?.clientY?Math.max(12,event.clientY-r.top-65)+'px':'44px';}
    function hide(){tooltip.hidden=true;}
    host.onpointerleave=hide;host.onfocusout=hide;
    if(c.kind==='line'){
      const box={x:available<420?62:68,y:36,w:W-(available<420?79:86),h:H-96};
      const all=c.series.flatMap(s=>s.values),[lo,hi]=domain(all,c.zero!==false),sy=axes(svg,box,lo,hi,available<600?c.unit:c.y_label||c.unit);
      const xmin=Math.min(...c.x),xmax=Math.max(...c.x),sx=x=>box.x+(x-xmin)/(xmax-xmin||1)*box.w;
      const ticks=available<420?4:6;for(let i=0;i<=ticks;i++){const idx=Math.round(i*(c.x.length-1)/ticks);label(svg,sx(c.x[idx]),H-35,fmt(c.x[idx]),'middle');}
      label(svg,box.x+box.w/2,H-8,c.x_label||'','middle');
      c.series.forEach((s,si)=>{
        const path=s.values.map((y,i)=>i&&(c.step||s.step)?`H${sx(c.x[i])}V${sy(y)}`:`${i?'L':'M'}${sx(c.x[i])},${sy(y)}`).join(' ');
        svg.append(el('path',{d:path,fill:'none',stroke:colors[si%4],'stroke-width':2.5,'stroke-dasharray':dash[si%4]}));
        s.values.forEach((y,i)=>{if(i===0||i===s.values.length-1||s.values.length<16)svg.append(el('circle',{cx:sx(c.x[i]),cy:sy(y),r:3.3,fill:si%2?'#0b1519':colors[si%4],stroke:colors[si%4],'stroke-width':1.7}));});
      });
      const guide=el('line',{y1:box.y,y2:box.y+box.h,stroke:'#6d858b','stroke-dasharray':'3 4',visibility:'hidden'});svg.append(guide);
      const capture=el('rect',{x:box.x,y:box.y,width:box.w,height:box.h,fill:'transparent',tabindex:'0',role:'group','aria-label':'Explore chart values with left and right arrow keys'});svg.append(capture);
      let selected=0;
      function point(i,event){selected=Math.max(0,Math.min(c.x.length-1,i));guide.setAttribute('x1',sx(c.x[selected]));guide.setAttribute('x2',sx(c.x[selected]));guide.setAttribute('visibility','visible');const text=`${c.x_label||'Position'}: ${fmt(c.x[selected],2)}. `+c.series.map(s=>`${s.name}: ${fmt(s.values[selected],2)} ${c.unit||''}`).join(' · ');capture.setAttribute('aria-label',text);show(text,event);}
      capture.onpointermove=e=>{const r=svg.getBoundingClientRect(),x=(e.clientX-r.left)*W/r.width;point(Math.round((x-box.x)/box.w*(c.x.length-1)),e);};
      capture.onfocus=()=>point(selected);capture.onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();point(selected+(e.key==='ArrowRight'?1:-1));}};
      capture.onpointerleave=()=>{guide.setAttribute('visibility','hidden');hide();};
    }else if(c.kind==='dotplot'){
      const box={x:W<440?100:135,y:48,w:W-(W<440?130:170),h:H-125};
      const [lo,hi]=domain(c.series.flatMap(s=>s.values)),sx=v=>box.x+(v-lo)/(hi-lo)*box.w;
      for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,x=sx(v);svg.append(el('line',{x1:x,x2:x,y1:box.y,y2:box.y+box.h,class:'chart-grid'}));label(svg,x,H-40,fmt(v,2),'middle');}
      c.categories.forEach((cat,ci)=>{const y=box.y+(ci+.5)*box.h/c.categories.length,values=c.series.map(s=>s.values[ci]);multiline(svg,box.x-15,y+4,cat,14,'end');svg.append(el('line',{x1:sx(Math.min(...values)),x2:sx(Math.max(...values)),y1:y,y2:y,stroke:'#536d77','stroke-width':3}));c.series.forEach((s,si)=>{const v=s.values[ci],circle=el('circle',{cx:sx(v),cy:y,r:6,fill:colors[si],stroke:'#0b1519','stroke-width':2});circle.append(el('title',{},`${cat}; ${s.name}: ${fmt(v,2)} ${c.unit}`));svg.append(circle);label(svg,sx(v),y+(si%2?27:-17),fmt(v,2),'middle');});});
      label(svg,box.x,18,c.unit);label(svg,box.x+box.w/2,H-8,'Cash coverage (x)','middle');
    }else if(c.kind==='stacked'){
      const left=W<440?64:90,box={x:left,y:43,w:W-left-20,h:H-110};
      const total=Math.max(...c.categories.map((_,i)=>c.series.reduce((a,s)=>a+s.values[i],0))),sx=v=>box.x+v/total*box.w;
      for(let i=0;i<=4;i++){const v=total*i/4;label(svg,sx(v),H-33,fmt(v),'middle');}
      c.categories.forEach((cat,ci)=>{const y=box.y+(ci+.25)*box.h/c.categories.length;label(svg,left-12,y+21,cat,'end');let sum=0;c.series.forEach((s,si)=>{const v=s.values[ci],rect=el('rect',{x:sx(sum),y,width:Math.max(0,sx(v)-sx(0)),height:36,fill:colors[si],stroke:'#0b1519','stroke-width':1.5});rect.append(el('title',{},`${cat}; ${s.name}: ${fmt(v,2)} ${c.unit}`));svg.append(rect);if(sx(v)-sx(0)>70)label(svg,sx(sum+v/2),y+23,fmt(v),'middle','chart-cell dark-label');sum+=v;});});
      label(svg,box.x,18,'Profit above returned capital ($)');
    }else if(c.kind==='bar'){
      const left=W<440?100:Math.min(210,W*.27),box={x:left,y:40,w:W-left-58,h:H-90};
      const [lo,hi]=domain(c.series.flatMap(s=>s.values));const sx=v=>box.x+(v-lo)/(hi-lo)*box.w;
      for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,x=sx(v);svg.append(el('line',{x1:x,x2:x,y1:box.y,y2:box.y+box.h,class:'chart-grid'}));label(svg,x,H-27,fmt(v),'middle');}
      const group=box.h/c.categories.length,bar=Math.min(22,(group-25)/c.series.length);
      c.categories.forEach((cat,ci)=>{multiline(svg,left-12,box.y+group*ci+18,cat,W<440?12:24,'end');c.series.forEach((s,si)=>{const v=s.values[ci],y=box.y+ci*group+si*(bar+3);const rect=el('rect',{x:Math.min(sx(0),sx(v)),y,width:Math.max(1,Math.abs(sx(v)-sx(0))),height:bar,fill:si%2?'#0b1519':colors[si%4],stroke:colors[si%4],'stroke-width':1.5});rect.append(el('title',{},`${cat} · ${s.name}: ${fmt(v,2)} ${c.unit}`));svg.append(rect);label(svg,sx(v)+(v>=0?7:-7),y+bar/2+4,fmt(v),v>=0?'start':'end');});});
      label(svg,box.x,18,c.y_label===c.unit?c.unit:c.y_label+' · '+c.unit);
    }else if(c.kind==='heatmap'){
      const left=85,top=42,bottom=H-58,right=W-20,cw=(right-left)/c.x.length,ch=(bottom-top)/c.y.length;
      const vals=c.z.flat(),vmin=Math.min(...vals),vmax=Math.max(...vals),span=Math.max(Math.abs(vmin),Math.abs(vmax))||1;
      function fill(v){const t=Math.abs(v)/span;const start=[18,36,42],end=v<0?[199,158,103]:[39,220,196];return `rgb(${start.map((k,i)=>Math.round(k+(end[i]-k)*(.14+.86*t))).join(',')})`;}
      c.y.forEach((y,yi)=>{label(svg,left-12,top+(yi+.5)*ch+4,y,'end');c.x.forEach((x,xi)=>{const v=c.z[yi][xi],rect=el('rect',{x:left+xi*cw+1,y:top+yi*ch+1,width:cw-2,height:ch-2,fill:fill(v),tabindex:'0',role:'img','aria-label':`${c.y_label} ${y}; ${c.x_label} ${x}; ${fmt(v,2)} ${c.unit}`});rect.onpointerenter=e=>show(rect.getAttribute('aria-label'),e);rect.onfocus=e=>show(rect.getAttribute('aria-label'));rect.onblur=hide;svg.append(rect);if(cw>49)label(svg,left+(xi+.5)*cw,top+(yi+.5)*ch+4,fmt(v,span<10?2:span<100?1:0),'middle',Math.abs(v)/span>.65?'chart-cell dark-label':'chart-cell');});});
      c.x.forEach((x,xi)=>label(svg,left+(xi+.5)*cw,H-35,x,'middle'));
      label(svg,left,18,c.y_label);label(svg,(left+right)/2,H-8,c.x_label,'middle');
      const key=host.closest('.case-chart').querySelector('.heat-key');if(key){key.textContent=`${fmt(vmin)} to ${fmt(vmax)} ${c.unit}. `+(vmin<0?'Amber cells are below zero.':'Brighter cells are larger.');}
    }else if(c.kind==='waterfall'){
      let acc=0;const items=c.values.map((v,i)=>{if(c.totals.includes(i)){acc=v;return{from:0,to:v,total:true,v};}const from=acc;acc+=v;return{from,to:acc,total:false,v};});
      const [lo,hi]=domain(items.flatMap(d=>[d.from,d.to]));const box={x:60,y:35,w:W-80,h:H-122},sy=axes(svg,box,lo,hi,c.y_label||c.unit),step=box.w/items.length,bw=Math.min(78,step*.7);
      items.forEach((d,i)=>{const x=box.x+step*i+(step-bw)/2,y=sy(Math.max(d.from,d.to)),height=Math.abs(sy(d.from)-sy(d.to)),fill=d.total?'#ecf3f2':d.v<0?'#c79e67':'#27dcc4';svg.append(el('rect',{x,y,width:bw,height:Math.max(1,height),fill}));const vtext=(d.total||d.v<=0?'':'+')+fmt(d.v,2);label(svg,x+bw/2,y-9,vtext,'middle');multiline(svg,x+bw/2,box.y+box.h+27,c.labels[i],16,'middle');if(i<items.length-1)svg.append(el('line',{x1:x+bw,x2:box.x+step*(i+1)+(step-bw)/2,y1:sy(d.to),y2:sy(d.to),class:'chart-connector'}));});
    }
    host.dataset.rendered='true';
  }
  for(const figure of document.querySelectorAll('.case-chart')){
    const host=figure.querySelector('.chart-surface');const c=JSON.parse(figure.querySelector('script[type="application/json"]').textContent);
    const paint=()=>render(host,c);let raf=0;new ResizeObserver(()=>{cancelAnimationFrame(raf);raf=requestAnimationFrame(paint);}).observe(host);paint();
  }
})();
