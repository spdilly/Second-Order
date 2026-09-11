/* Two cursor-driven motion studies. Abstract linework; no financial data. */
(() => {
  'use strict';
  const canvas = document.getElementById('optical-field');
  const ctx = canvas?.getContext('2d');
  if (!ctx) return;
  const surface = canvas.parentElement;
  const hero = surface.closest('.digital-hero') || surface;
  const toggle = surface.querySelector('.motion-toggle');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const fine = matchMedia('(hover: hover) and (pointer: fine)');
  const mode = canvas.dataset.motion === 'ripple' ? 'ripple' : 'flow';
  const diagnostics = document.querySelector('[data-motion-diagnostics]');
  let width = 1, height = 1, scale = 1, lines = [], raf = 0, previous = 0;
  let visible = true, paused = false, forceReduced = false, forceTouch = false;
  let frames = 0, draws = 0, peakDrawMs = 0, averageDrawMs = 0, lastReport = 0;
  let now = 0, lastPointer = 0, waveClock = 0, replay = 0, touchUntil = 0;
  const pointer = {x:0,y:0,tx:0,ty:0,vx:0,vy:0,px:0,py:0,active:0,target:0};
  const waves = [];
  const clamp = (v,a,b) => Math.max(a,Math.min(b,v));
  try { paused = sessionStorage.getItem('second-order-motion') === 'paused'; } catch {}

  function makeLines() {
    lines = [];
    const steps = width < 500 ? 38 : 48;
    for (let family=0; family<2; family++) {
      const count = family ? 64 : 38;
      for (let i=0;i<count;i++) {
        const points=[];
        for (let j=0;j<=steps;j++) {
          const base=(i/(count-1)-.5)*width*1.35;
          const py=(j/steps-.5)*height*1.22;
          const centerX=width*(family?.08:-.03), centerY=height*(family?-.08:.07);
          const dx=(base-centerX)/scale,dy=(py-centerY)/scale;
          const envelope=Math.exp(-(dx*dx+dy*dy)*3.1);
          const bend=Math.sin(py/scale*5.2+family*.9+base/scale*1.7)*envelope*scale*.39;
          points.push({x:width*.5+base+bend+py*(family?-.1:.2),y:height*.5+py,dx:0,dy:0,vx:0,vy:0});
        }
        lines.push({family,points});
      }
    }
  }
  const mayMove = () => !paused && !reduced.matches && !forceReduced && visible && !document.hidden;
  const cursorEnabled = () => mayMove() && fine.matches && !forceTouch;
  function stop() { cancelAnimationFrame(raf);raf=0;previous=0; }
  function request() { if (!raf && mayMove()) raf=requestAnimationFrame(tick); }
  function reset() {
    pointer.active=pointer.target=0;pointer.vx=pointer.vy=0;
    pointer.x=pointer.tx=width*.5;pointer.y=pointer.ty=height*.5;
    pointer.px=pointer.x;pointer.py=pointer.y;waves.length=0;
    replay=0;touchUntil=0;
    for (const line of lines) for (const p of line.points) p.dx=p.dy=p.vx=p.vy=0;
  }
  function draw(dt=0) {
    const started=performance.now();
    ctx.clearRect(0,0,width,height);
    const radius=scale*(mode==='flow'?.39:.45),r2=radius*radius;
    let energy=0,displacement=0;
    const dragX=clamp(pointer.vx*1.6,-scale*.18,scale*.18);
    const dragY=clamp(pointer.vy*1.6,-scale*.14,scale*.14);
    for (const line of lines) {
      ctx.beginPath();
      const family=line.family;
      for (let j=0;j<line.points.length;j++) {
        const p=line.points[j];
        const dx=p.x-pointer.x,dy=p.y-pointer.y;
        const distance2=dx*dx+dy*dy;
        const envelope=Math.exp(-distance2/r2*1.5)*pointer.active;
        let targetX=0,targetY=0;
        if (mode==='flow') {
          // Local attraction, tangential flow and a short wake from cursor velocity.
          const turn=family?1:-.35;
          targetX=envelope*(-dx*.34-dy*.19*turn+dragX);
          targetY=envelope*(-dy*.2+dx*.15*turn+dragY);
          const parallax=family?1:.45;
          targetX+=(pointer.x-width*.5)*.10*pointer.active*parallax;
          targetY+=(pointer.y-height*.5)*.045*pointer.active*parallax;
        } else {
          // Finite rings propagate from movement, with no perpetual idle animation.
          for (const wave of waves) {
            const wx=p.x-wave.x,wy=p.y-wave.y,d=Math.sqrt(wx*wx+wy*wy);
            const age=(now-wave.time)/1000,travel=d-age*scale*.46;
            const packet=Math.exp(-Math.pow(travel/(scale*.18),2))*Math.exp(-age*1.9);
            const amount=Math.sin(travel/(scale*.04))*packet*wave.strength;
            targetX+=wx/(d||1)*amount;targetY+=wy/(d||1)*amount*.62;
          }
          targetX-=dx*.2*envelope;targetY-=dy*.12*envelope;
        }
        if (dt) {
          // Semi-implicit damped springs, integrated in bounded frame-time steps.
          p.vx+=(targetX-p.dx)*(.095*dt);p.vy+=(targetY-p.dy)*(.095*dt);
          const damping=Math.pow(mode==='flow'?.73:.76,dt);
          p.vx*=damping;p.vy*=damping;p.dx+=p.vx*dt;p.dy+=p.vy*dt;
        }
        const x=p.x+p.dx,y=p.y+p.dy;
        if (!j) ctx.moveTo(x,y);else ctx.lineTo(x,y);
        energy=Math.max(energy,Math.abs(targetX-p.dx)+Math.abs(targetY-p.dy)+Math.abs(p.vx)+Math.abs(p.vy));
        displacement=Math.max(displacement,Math.hypot(p.dx,p.dy));
      }
      ctx.strokeStyle=family?'rgba(56,244,221,.74)':'rgba(215,242,240,.24)';
      ctx.lineWidth=family?1.12:.9;ctx.stroke();
    }
    draws++;
    const elapsed=performance.now()-started;
    averageDrawMs=averageDrawMs ? averageDrawMs*.92+elapsed*.08 : elapsed;
    peakDrawMs=Math.max(peakDrawMs,elapsed);
    if (diagnostics && (now-lastReport>200 || !dt)) {
      diagnostics.textContent=`${mode==='flow'?'Liquid lines':'Ripple field'} · ${Math.round(width)} × ${Math.round(height)} · ${averageDrawMs.toFixed(1)} ms draw · ${frames} animated frames · ${displacement.toFixed(1)} px response · ${reduced.matches||forceReduced?'reduced motion':paused?'paused':forceTouch?'touch preview':'active'}`;
      diagnostics.dataset.frames=String(frames);diagnostics.dataset.draws=String(draws);
      diagnostics.dataset.displacement=displacement.toFixed(3);
      diagnostics.dataset.averageDrawMs=averageDrawMs.toFixed(3);diagnostics.dataset.peakDrawMs=peakDrawMs.toFixed(3);
      lastReport=now;
    }
    return energy;
  }
  function setPointer(x,y,time,impulse=true) {
    if (diagnostics) diagnostics.dataset.settled='false';
    const distance=Math.hypot(x-pointer.tx,y-pointer.ty);
    pointer.tx=clamp(x,-width*.3,width*1.3);pointer.ty=clamp(y,-height*.3,height*1.3);
    pointer.target=1;lastPointer=time;
    if (mode==='ripple' && impulse && (time-waveClock>80) && distance>4) {
      waves.push({x,y,time,strength:Math.min(scale*.08,10+distance*.2)});
      if (waves.length>7) waves.shift();waveClock=time;
    }
    request();
  }
  function tick(time) {
    raf=0;if (!mayMove()) return;
    now=time;
    const dt=previous?clamp((time-previous)/16.667,.2,2):1;previous=time;
    if (replay) {
      const t=(time-replay)/1000;
      if (t<4.2) {
        setPointer(width*(.5+.29*Math.sin(t*2.3)),height*(.5+.29*Math.sin(t*3.1+.4)),time);
      } else {replay=0;pointer.target=0;}
    }
    if (touchUntil) {pointer.target=time<touchUntil?1:0;if(time>=touchUntil)touchUntil=0;}
    const ease=1-Math.exp(-dt*.28);
    pointer.x+=(pointer.tx-pointer.x)*ease;pointer.y+=(pointer.ty-pointer.y)*ease;
    pointer.vx=(pointer.x-pointer.px)/dt;pointer.vy=(pointer.y-pointer.py)/dt;
    pointer.px=pointer.x;pointer.py=pointer.y;
    pointer.active+=(pointer.target-pointer.active)*(1-Math.exp(-dt*.12));
    while (waves.length && time-waves[0].time>2200) waves.shift();
    frames++;const energy=draw(dt);
    const moving=Math.abs(pointer.tx-pointer.x)+Math.abs(pointer.ty-pointer.y)+Math.abs(pointer.target-pointer.active)>0.002;
    if (energy>.018 || moving || waves.length || replay || touchUntil) request();
    else {previous=0;if(diagnostics) diagnostics.dataset.settled='true';}
  }
  function resize() {
    const box=canvas.getBoundingClientRect();
    if (!box.width || !box.height) return;
    width=box.width;height=box.height;scale=Math.min(width,height);
    const dpr=Math.min(devicePixelRatio||1,2,1600/Math.max(width,height));
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);stop();makeLines();reset();draw();
  }
  function controls() {
    toggle.hidden=reduced.matches||forceReduced;
    toggle.setAttribute('aria-pressed',String(paused));toggle.textContent=paused?'Resume motion':'Pause motion';
    if (!mayMove()) stop();
  }
  hero.addEventListener('pointermove',event=>{
    if (!cursorEnabled() || event.pointerType==='touch' || event.target.closest('button,a')) return;
    const box=canvas.getBoundingClientRect();
    setPointer(event.clientX-box.left,event.clientY-box.top,performance.now());
  },{passive:true});
  hero.addEventListener('pointerleave',()=>{pointer.target=0;request();});
  canvas.addEventListener('pointerdown',event=>{
    if (!mayMove() || (event.pointerType!=='touch'&&!forceTouch)) return;
    const box=canvas.getBoundingClientRect();
    const time=performance.now();touchUntil=time+320;
    setPointer(event.clientX-box.left,event.clientY-box.top,time);
  },{passive:true});
  canvas.addEventListener('pointerup',()=>{if (!fine.matches||forceTouch) request();},{passive:true});
  canvas.addEventListener('pointercancel',()=>{touchUntil=0;pointer.target=0;request();},{passive:true});
  toggle.addEventListener('click',()=>{
    paused=!paused;try{sessionStorage.setItem('second-order-motion',paused?'paused':'active');}catch{}
    replay=0;controls();if (!paused) request();else draw();
  });
  reduced.addEventListener('change',()=>{stop();reset();controls();draw();});
  fine.addEventListener('change',()=>{stop();reset();controls();draw();});
  document.addEventListener('visibilitychange',()=>{stop();if(!document.hidden)request();});
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(!visible)stop();else request();}).observe(canvas);
  new ResizeObserver(resize).observe(surface);
  // Review controls are only wired on the separate, unpublished motion-study page.
  document.querySelector('[data-replay]')?.addEventListener('click',()=>{
    if (!mayMove()) return;diagnostics.dataset.settled='false';peakDrawMs=0;frames=0;
    replay=performance.now();request();
  });
  document.querySelector('[data-reduced-preview]')?.addEventListener('change',event=>{
    forceReduced=event.target.checked;stop();reset();controls();draw();
  });
  document.querySelector('[data-touch-preview]')?.addEventListener('change',event=>{
    forceTouch=event.target.checked;stop();reset();controls();draw();
  });
  controls();resize();
})();
