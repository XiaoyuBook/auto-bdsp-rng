(() => {
    const root = document.getElementById('rng-roi-demo');
    const q = s => root.querySelector(s);
    const scene = q('.scene'), ctx = scene.getContext('2d');
    const loupe = q('.loupe'), lens = loupe.getContext('2d');
    const source = q('.source');
    const W = 1586, H = 1280;
    const roiButton = {x:57, y:568, w:188, h:44};
    const eyeButton = {x:255, y:568, w:188, h:44};
    const saveButton = {x:340, y:774, w:104, h:45};
    const preview = {x:520, y:576, w:1017, h:505};
    const sampleRoi = {x:1034, y:785, w:60, h:68};
    const sampleEye = {x:1050, y:807, w:31, h:32};
    const zoomArea = {x:990, y:765, w:126, h:110};
    const green = '#49edb2', amber = '#ffcc71';
    const titles = ['先点击「框选眼睛区域」', '右键拖动，框出识别范围', '再点击「截取眼睛」', '右键拖动，贴着眼睛框选', '最后点击「保存配置」'];
    const details = ['先确定软件要在哪个范围内识别眼睛。', '框住一只眼睛，并在周围留一点余量。', '接下来用实际人物的眼睛更新模板。', '只截取一只睁开的眼睛，模板要小于 ROI。', '把 ROI 和眼睛模板保存到当前配置。'];
    const durations = [4000, 5400, 3600, 5400, 4400];
    const total = durations.reduce((a,b) => a+b,0);
    const centers = r => ({x:r.x+r.w/2, y:r.y+r.h/2});
    const bound = (v,a,b) => Math.max(a,Math.min(b,v));
    const ease = p => p*p*(3-2*p);
    const mix = (a,b,p) => ({x:a.x+(b.x-a.x)*ease(bound(p,0,1)), y:a.y+(b.y-a.y)*ease(bound(p,0,1))});
    const inside = (r,p) => p.x>=r.x && p.x<=r.x+r.w && p.y>=r.y && p.y<=r.y+r.h;
    const rectFrom = (a,b) => ({x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),w:Math.abs(a.x-b.x),h:Math.abs(a.y-b.y)});
    let elapsed = 600, playing = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    let lastFrame = 0, lastLabel = '', frameRequest = 0;
    function updateLabels(index, finished=false) {
      const key = [index,finished].join(':');
      if (key===lastLabel) return;
      lastLabel=key;
      q('.eyebrow').textContent = `操作演示 · ${index+1} / 5`;
      q('.step-title').textContent = titles[index];
      q('.step-detail').textContent = finished ? '配置已保存，稍后会从头再演示一遍。' : details[index];
      const right=index===1 || index===3;
      q('.mouse').dataset.button=right?'right':'left';
      q('.gesture-text').textContent=finished?'保存完成':right?'按住右键 → 拖动 → 松开':'左键点击按钮';
      q('.loupe-wrap').hidden=!right;
    }
    function demoState() {
      let t=elapsed%total, i=0;
      while(t>=durations[i] && i<4) { t-=durations[i]; i++; }
      const button = i===0 ? roiButton : i===2 ? eyeButton : saveButton;
      let cursor, roi = i>1 ? sampleRoi:null, eye=i>3 ? sampleEye:null, pulse=0, dragging=false;
      let hole = i===1 || i===3 ? preview : button;
      if(i===0) {
        cursor=mix({x:750,y:630},centers(button),(t-600)/1400);
        pulse=t>=2050 && t<2800 ? (t-2050)/750 : 0;
        if(t<500) hole=null;
      } else if(i===1 || i===3) {
        const box=i===1 ? sampleRoi:sampleEye;
        const from=centers(i===1?roiButton:eyeButton);
        cursor=t<1400 ? mix(from,box,t/1400) : mix(box,{x:box.x+box.w,y:box.y+box.h},(t-1800)/2200);
        dragging=t>=1800 && t<4000;
        const drawn=t>=4000 ? box : t>=1800 ? rectFrom(box,cursor) : null;
        if(i===1) roi=drawn; else eye=drawn;
      } else {
        const from=i===2 ? {x:sampleRoi.x+sampleRoi.w,y:sampleRoi.y+sampleRoi.h} : {x:sampleEye.x+sampleEye.w,y:sampleEye.y+sampleEye.h};
        cursor=mix(from,centers(button),t/1500);
        pulse=t>=1650 && t<2400 ? (t-1650)/750 : 0;
      }
      return {index:i, hole, cursor, roi, eye, pulse, dragging, finished:i===4 && t>2500};
    }
    function sizeCanvas(canvas,context) {
      const r=canvas.getBoundingClientRect(), dpr=Math.min(devicePixelRatio||1,2);
      const w=Math.round(r.width*dpr), h=Math.round(r.height*dpr);
      if(canvas.width!==w || canvas.height!==h) {canvas.width=w;canvas.height=h;}
      context.setTransform(dpr,0,0,dpr,0,0);
      return r;
    }
    function strokeSelection(context,r,color,scale=1) {
      if(!r || r.w<1 || r.h<1) return;
      context.save(); context.fillStyle=color+'18'; context.fillRect(r.x,r.y,r.w,r.h);
      context.strokeStyle=color; context.lineWidth=2/scale; context.strokeRect(r.x,r.y,r.w,r.h); context.restore();
    }
    function drawCursor(context,p,s,buttonPressed,pulse) {
      if(!p) return;
      context.save(); context.translate(p.x,p.y); context.scale(1/s,1/s);
      if(pulse>0) {context.beginPath();context.arc(0,0,8+24*pulse,0,Math.PI*2);context.strokeStyle=`rgba(73,237,178,${1-pulse})`;context.lineWidth=3;context.stroke();}
      context.beginPath(); context.moveTo(0,0); context.lineTo(3,26);context.lineTo(10,19);context.lineTo(16,30);context.lineTo(22,27);context.lineTo(16,16);context.lineTo(27,15);context.closePath();
      context.shadowColor='#0008';context.shadowBlur=4;context.fillStyle='white';context.strokeStyle='#223c30';context.lineWidth=1.5;context.fill();context.stroke();context.shadowBlur=0;
      if(buttonPressed) {context.fillStyle='#087c58';context.beginPath();context.roundRect(27,21,48,24,5);context.fill();context.fillStyle='white';context.font='12px "Microsoft YaHei UI",sans-serif';context.fillText('右键',36,38);}
      context.restore();
    }
    function paintLens(state) {
      if(q('.loupe-wrap').hidden) return;
      const r=sizeCanvas(loupe,lens), s=r.width/zoomArea.w;
      lens.clearRect(0,0,r.width,r.height);
      lens.drawImage(source,zoomArea.x,zoomArea.y,zoomArea.w,zoomArea.h,0,0,r.width,r.height);
      lens.save();lens.scale(s,r.height/zoomArea.h);lens.translate(-zoomArea.x,-zoomArea.y);
      strokeSelection(lens,state.roi,green,s);strokeSelection(lens,state.eye,amber,s);
      if(state.cursor && inside(zoomArea,state.cursor)) drawCursor(lens,state.cursor,s,false,0);
      lens.restore();
    }
    function paint() {
      if(!source.complete || !source.naturalWidth) return;
      const state=demoState();
      updateLabels(state.index,state.finished);
      const r=sizeCanvas(scene,ctx), s=r.width/W;
      ctx.clearRect(0,0,r.width,r.height);ctx.save();ctx.scale(s,s);
      ctx.drawImage(source,0,0,W,H);
      const hole=state.hole, mask=new Path2D();mask.rect(0,0,W,H);
      if(hole) mask.roundRect(hole.x-4,hole.y-4,hole.w+8,hole.h+8,9);
      ctx.fillStyle='rgba(0,0,0,.78)';ctx.fill(mask,'evenodd');
      if(hole) {ctx.strokeStyle=green;ctx.lineWidth=2/s;ctx.beginPath();ctx.roundRect(hole.x-4,hole.y-4,hole.w+8,hole.h+8,9);ctx.stroke();}
      strokeSelection(ctx,state.roi,green,s);strokeSelection(ctx,state.eye,amber,s);
      drawCursor(ctx,state.cursor,s,state.dragging,state.pulse);
      ctx.restore(); paintLens(state);
      q('.elapsed').style.width=(elapsed%total/total*100)+'%';
    }
    function tick(time) {
      if(lastFrame && playing && !document.hidden) {
        elapsed+=Math.min(time-lastFrame,100);
        paint();
      }
      lastFrame=time;frameRequest=requestAnimationFrame(tick);
    }
    let bridge=null;
    const readyButton=q('.learned');
    q('.pause').addEventListener('click',()=>{
      playing=!playing;q('.pause').textContent=playing?'暂停':'继续播放';
    });
    q('.close').addEventListener('click',()=>{ if(bridge) bridge.pause(); });
    readyButton.addEventListener('click',()=>{
      if(!bridge || readyButton.disabled) return;
      readyButton.disabled=true;bridge.learned();
    });
    new QWebChannel(qt.webChannelTransport, channel=>{
      bridge=channel.objects.eyeGuideBridge;
      bridge.failed.connect(message=>{q('.error').textContent=message;readyButton.disabled=false;});
      const ready=()=>{ if(!source.naturalWidth)return;paint();bridge.pageReady();readyButton.disabled=false; };
      if(source.complete) ready(); else source.addEventListener('load',ready,{once:true});
    });
    if(!playing) q('.pause').textContent='播放演示';
    source.addEventListener('load',paint,{once:true});
    new ResizeObserver(paint).observe(scene);
    document.addEventListener('visibilitychange',()=>{lastFrame=0;});
    paint();frameRequest=requestAnimationFrame(tick);
    window.addEventListener('pagehide',()=>cancelAnimationFrame(frameRequest),{once:true});
  })();
