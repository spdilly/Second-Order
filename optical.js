/* Abstract interference artwork. It does not encode financial information. */
(() => {
  const canvas=document.getElementById('optical-field');if(!canvas)return;
  const context=canvas.getContext('2d');if(!context)return;
  const surface=canvas.parentElement,toggle=surface.querySelector('.motion-toggle');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),fine=matchMedia('(hover: hover) and (pointer: fine)');
  let width=1,height=1,x=0,y=0,tx=0,ty=0,visible=true,paused=false,frame=0,last=0;
  try{paused=sessionStorage.getItem('second-order-motion')==='paused';}catch{}
  function draw(){
    context.clearRect(0,0,width,height);const scale=Math.min(width,height);
    for(let family=0;family<2;family++){
      context.strokeStyle=family?'rgba(56,244,221,.78)':'rgba(221,246,244,.26)';context.lineWidth=family?1.15:1;
      for(let i=0;i<82;i++){
        const base=(i/81-.5)*width*1.24;context.beginPath();
        for(let j=0;j<=62;j++){
          const py=(j/62-.5)*height*1.1,cx=width*(family?.08:-.03)+x*(family?1:.22),cy=height*(family?-.08:.07)+y;
          const dx=(base-cx)/scale,dy=(py-cy)/scale,envelope=Math.exp(-(dx*dx+dy*dy)*3.1);
          const bend=Math.sin(py/scale*5.2+family*.9+base/scale*1.7)*envelope*scale*.39;
          const px=width*.5+base+bend+py*(family?-.1:.2),yy=height*.5+py;
          if(j===0)context.moveTo(px,yy);else context.lineTo(px,yy);
        }context.stroke();
      }
    }
  }
  const canMove=()=>!paused&&!reduced.matches&&fine.matches&&visible&&!document.hidden;
  function stop(){cancelAnimationFrame(frame);frame=0;}
  function tick(time){frame=0;if(!canMove())return;if(time-last<32){frame=requestAnimationFrame(tick);return;}last=time;x+=(tx-x)*.12;y+=(ty-y)*.12;draw();if(Math.abs(tx-x)+Math.abs(ty-y)>.18)frame=requestAnimationFrame(tick);}
  function request(){if(!frame&&canMove())frame=requestAnimationFrame(tick);}
  function controls(){toggle.hidden=!fine.matches||reduced.matches;toggle.setAttribute('aria-pressed',String(paused));toggle.textContent=paused?'Resume motion':'Pause motion';if(!canMove())stop();}
  function resize(){const box=canvas.getBoundingClientRect();width=box.width;height=box.height;if(!width||!height)return;const d=Math.min(devicePixelRatio||1,1.5,1400/Math.max(width,height));canvas.width=Math.round(width*d);canvas.height=Math.round(height*d);context.setTransform(d,0,0,d,0,0);draw();}
  surface.addEventListener('pointermove',e=>{if(!canMove())return;const b=canvas.getBoundingClientRect();tx=((e.clientX-b.left)/b.width-.5)*70;ty=((e.clientY-b.top)/b.height-.5)*45;request();});
  surface.addEventListener('pointerleave',()=>{tx=ty=0;request();});
  toggle.addEventListener('click',()=>{paused=!paused;try{sessionStorage.setItem('second-order-motion',paused?'paused':'active');}catch{}controls();if(!paused)request();});
  reduced.addEventListener('change',()=>{x=y=tx=ty=0;controls();draw();});fine.addEventListener('change',controls);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else request();});
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(!visible)stop();else request();}).observe(canvas);
  new ResizeObserver(resize).observe(surface);controls();resize();
})();
