/* Packaged, offline UI. Authentication and binding are owned by the Qt service. */
(() => {
  'use strict';
  const root = document.getElementById('bdsp-qq-design');
  const get = id => document.getElementById('bd-' + id);
  const icons = {
    bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4',
    x:'m6 6 12 12 M6 18 18 6', eye:'M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7 M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
    'book-open':'M12 5v16 M12 5C8 2 3 3 2 4v15c4-2 7-1 10 2 3-3 6-4 10-2V4c-4-2-7-1-10 1',
    image:'M3 3h18v18H3z M3 17l6-6 5 5 3-3 4 4 M16 7h.01',
    send:'m22 2-7 20-4-9-9-4 20-7 M11 13 22 2',
    'arrow-left':'M20 12H4 m6-6-6 6 6 6', 'arrow-right':'M4 12h16 m-6-6 6 6-6 6',
    'check-circle-2':'M22 11a10 10 0 1 1-6-9 M9 11l3 3L22 4',
    'messages-square':'M3 3h15v12H7l-4 4z M8 19h10l4 3V8',
  };
  root.querySelectorAll('[data-lucide]').forEach(node => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('fill','none');
    svg.setAttribute('stroke','currentColor'); svg.setAttribute('stroke-width','1.7');
    svg.setAttribute('stroke-linecap','round'); svg.setAttribute('stroke-linejoin','round');
    svg.setAttribute('aria-hidden','true');
    const path = document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d',icons[node.dataset.lucide] || ''); svg.append(path); node.replaceWith(svg);
  });
  let bridge, state, assets, lastImage = '', zoomScale = 1, lastRecords = '';
  let full = false, highlight = true;
  const testPanel = root.querySelector('.bd-test-side');
  const send = (action,args={}) => bridge.command(action,JSON.stringify(args));
  const navigate = change => {
    const before = state.view;
    if ('step' in change || change.phase !== undefined && change.phase !== before.phase) full = false;
    send('navigate',change);
  };
  const on = (id,fn) => get(id).addEventListener('click',fn);
  const move = (child,parent) => { if(child.parentNode !== parent) parent.append(child); };
  const text = (id,value) => { get(id).textContent = value; };

  function drawGuide() {
    const view = state.view, register = view.phase === 'register';
    get('guide-layout').hidden = !register;
    get('guide-connect').hidden = view.phase !== 'bind';
    get('guide-practice').hidden = view.phase !== 'test';
    root.querySelectorAll('[data-phase]').forEach(el => el.setAttribute('aria-pressed',String(el.dataset.phase === view.phase)));
    if(register) {
      const step = assets.steps[view.step];
      text('step-title',step.title); text('step-counter',String(view.step+1).padStart(2,'0')+' / 12');
      const rect = full ? [0,0,...step.size] : step.crop;
      const wrap = get('screenshot-wrap'), img = get('step-image');
      if(lastImage !== step.image) { img.src = step.image; lastImage = step.image; }
      img.alt = step.title + '原始截图';
      wrap.style.width = Math.min(560,335*rect[2]/rect[3])+'px';
      wrap.style.aspectRatio = rect[2] + '/' + rect[3];
      img.style.width = (step.size[0]/rect[2]*100)+'%';
      img.style.left = (-rect[0]/rect[2]*100)+'%'; img.style.top = (-rect[1]/rect[3]*100)+'%';
      text('step-action',step.actions.join(' ')); text('step-detail',step.detail);
      text('guide-result',step.result);
      text('full-image',full ? '返回重点区域' : '查看完整截图');
      text('image-label',full ? '原始截图 · 完整画面' : '原始截图 · 重点区域');
      get('highlight').checked = highlight;
      const layer = get('focus-layer'); layer.replaceChildren(); layer.hidden = !highlight;
      step.focus.forEach(([x,y,w,h]) => {
        const mark = document.createElement('span'); mark.className = 'bd-focus-box';
        Object.assign(mark.style,{left:((x-rect[0])/rect[2]*100)+'%',top:((y-rect[1])/rect[3]*100)+'%',width:(w/rect[2]*100)+'%',height:(h/rect[3]*100)+'%'});
        layer.append(mark);
      });
      root.querySelectorAll('[data-step]').forEach(el => {
        if(Number(el.dataset.step) === view.step) el.setAttribute('aria-current','step');
        else el.removeAttribute('aria-current');
      });
    } else {
      text('guide-result',view.phase === 'bind' ? '在当前页面验证凭据、绑定接收方，然后继续图文验证' : '测试消息默认附带软件 Logo');
    }
    get('prev').disabled = register && view.step === 0;
    text('next',register ? (view.step === 11 ? '连接与绑定 →' : '下一步 →') : (view.phase === 'bind' ? '图文验证 →' : '完成教程'));
  }

  function drawRecords() {
    const value = JSON.stringify(state.records);
    if(lastRecords === value) return;
    lastRecords = value;
    const table = get('record-table'); table.replaceChildren();
    const header = document.createElement('div'); header.className = 'bd-record-row bd-record-header';
    ['时间 / 类型','接收方','结果'].forEach(value => {const el=document.createElement('span'); el.textContent=value; header.append(el);});
    table.append(header);
    if(!state.records.length) {
      const empty = document.createElement('p'); empty.className='bd-muted';
      empty.textContent = '暂无发送记录。完成绑定后，发送一次图文测试。'; table.append(empty);
    }
    state.records.forEach(record => {
      const row = document.createElement('div'); row.className='bd-record-row';
      const time = document.createElement('span'); time.textContent=record.time;
      const event = document.createElement('strong'); event.textContent=record.event; time.append(event);
      const target = document.createElement('span'); target.textContent=record.recipient;
      const result = document.createElement('span'); result.className=record.success?'bd-ok':'bd-warn';
      result.textContent=record.success?'提交成功':'发送未完成'; row.append(time,target,result); table.append(row);
      const details=document.createElement('details'); details.className='bd-details';
      const summary=document.createElement('summary'); summary.textContent='查看发送详情';
      const detail=document.createElement('p'); detail.textContent=record.detail;
      details.append(summary,detail); table.append(details);
    });
  }

  function render() {
    const s=state.settings, v=state.view, b=state.binding;
    const inBinding=v.guide && v.phase==='bind';
    move(get('credentials-form'),inBinding?get('guide-credentials'):get('setup-main'));
    move(get('recipients-form'),inBinding?get('guide-recipients'):get('setup-main'));
    move(testPanel,v.guide && v.phase==='test'?get('guide-test-slot'):get('setup'));
    text('dialog-title',v.guide?'QQ 机器人注册与绑定':'QQ 通知');
    text('dialog-subtitle',v.guide?'按步骤操作，可随时返回设置':'接收任务结果与运行截图');
    get('settings').hidden=v.guide; get('guide').hidden=!v.guide;
    get('enabled').checked=s.enabled; get('enabled').disabled=state.busy;
    text('switch-text',s.enabled?'已启用':'未启用');
    for(const tab of ['setup','rules','records']) {
      get(tab).hidden=v.tab!==tab;
      get('tab-'+tab).setAttribute('aria-selected',String(v.tab===tab));
    }
    text('credential-state',state.verified?'✓ 已验证':'待验证');
    get('credential-state').classList.toggle('bd-ok',state.verified);
    text('credentials-feedback',state.credentials_feedback);
    get('credentials-feedback').classList.toggle('bd-warn',!state.verified);
    for(const id of ['app-id','secret','remember','verify','user-on','group-on']) get(id).disabled=state.busy;
    for(const kind of ['user','group']) {
      const oid=s[kind+'_openid']; get(kind+'-on').checked=s[kind+'_enabled'];
      text(kind+'-state',oid?'已绑定 · OpenID …'+oid.slice(-4):'未绑定');
      text('bind-'+kind,b.kind===kind?'等待绑定…':oid?'重新绑定':kind==='user'?'绑定私聊':'绑定群聊');
      get('bind-'+kind).disabled=state.busy || !state.verified;
    }
    get('binding').hidden=!b.code;
    text('binding-title',b.kind==='group'?'在目标 QQ 群中发送绑定码':'向机器人私聊发送绑定码');
    text('binding-code',b.code.slice(0,3)+' '+b.code.slice(3));
    text('countdown','剩余 '+String(b.seconds).padStart(2,'0')+' 秒');
    get('countdown-track').setAttribute('aria-valuenow',String(b.seconds));
    get('countdown-fill').style.width=(b.seconds/60*100)+'%';
    get('binding').classList.toggle('is-expiring',b.seconds<=10);
    text('binding-help',(b.kind==='group'?'在目标群里 @机器人，并发送 ':'向机器人私聊发送 ')+b.code+'。');
    text('refresh-notice',b.generation>1?`已自动更新第 ${b.generation} 组绑定码，旧码已失效。`:'到期自动换码并重新计时，旧码立即失效。');
    get('test-send').disabled=state.busy || !state.ready;
    text('test-result',state.test==='sent'?'文字和图片均已提交，请在 QQ 中确认是否收到。':state.test==='confirmed'?'已确认收到文字和图片 · 测试通过':state.test==='failure'?'图文测试未通过：'+state.test_detail:state.ready?'将发送到：'+['user','group'].filter(k=>s[k+'_enabled']).map(k=>k==='user'?'QQ 私聊':'QQ 群聊').join('、'):'请先填写凭据并绑定勾选的接收方');
    get('test-result').classList.toggle('bd-warn',state.test==='failure');
    if(state.operation==='test' && state.busy) text('test-result','正在发送文字和图片…');
    get('test-result').classList.toggle('bd-ok',state.test==='confirmed');
    get('confirm-received').hidden=state.test!=='sent';
    for(const [id,key] of [['attach-image','attach_image'],['event-done','on_completed'],['event-error','on_failed'],['event-stop','on_stopped']]) get(id).checked=s[key];
    text('image-description',s.attach_image?'＋ 1 张运行截图':'仅发送文字结果');
    text('footer-status',state.feedback || ('配置已保存 · 自动通知'+(s.enabled?'已启用':'未启用')));
    get('footer-status').classList.toggle('bd-warn',state.feedback_error);
    get('cancel-operation').hidden=!state.busy;
    get('guide-cancel').hidden=!state.busy || state.operation==='bind';
    drawGuide(); drawRecords();
    // Errors remain visible while reading the guide, including verification failures.
    if(v.guide && (state.feedback_error || state.busy || v.phase==='test')) text('guide-result',state.feedback || get('guide-result').textContent);
    get('guide-result').classList.toggle('bd-warn',state.feedback_error);
  }

  function zoom() {
    const step=assets.steps[state.view.step]; get('zoom-image').src=step.image;
    text('zoom-title',step.title+' · 原图'); get('zoom').showModal(); fitZoom();
  }
  function fitZoom() {
    const area=get('zoom-area'), step=assets.steps[state.view.step];
    zoomScale=Math.min((area.clientWidth-20)/step.size[0],(area.clientHeight-20)/step.size[1]); resizeZoom();
  }
  function resizeZoom() { get('zoom-image').style.width=(assets.steps[state.view.step].size[0]*zoomScale)+'px'; }

  new QWebChannel(qt.webChannelTransport, channel => {
    bridge=channel.objects.notifications;
    bridge.initialize(raw => {
      assets=JSON.parse(raw); state=assets.state;
      get('app-id').value=assets.credentials.app_id; get('secret').value=assets.credentials.secret;
      get('remember').checked=assets.credentials.remember_secret;
      delete assets.credentials;
      root.querySelectorAll('[data-logo]').forEach(el=>el.src=assets.logo);
      const titles=['注册账号','完善主体信息','选择机器人','进入机器人管理','新建机器人','填写资料并创建','稍后连接','打开管理页','打开开发设置','获取接入凭据','按需重置密钥','复制新密钥'];
      let previousPhase='';
      assets.steps.forEach((step,index)=>{
        if(step.phase!==previousPhase) {const h=document.createElement('span'); h.className='bd-chapter-label'; h.textContent=step.phase; get('guide-steps').append(h); previousPhase=step.phase;}
        const button=document.createElement('button'); button.type='button'; button.className='bd-step-button'; button.dataset.step=String(index);
        button.setAttribute('aria-label',`第 ${index+1} 步：${step.title}`);
        const number=document.createElement('span'); number.textContent=String(index+1).padStart(2,'0');
        const name=document.createElement('span'); name.textContent=titles[index]; button.append(number,name);
        button.addEventListener('click',()=>navigate({step:index})); get('guide-steps').append(button);
      });
      const list=document.createElement('ol');
      ['点击左侧「发送图文测试」，软件将发送文字和一张 Logo 图片。','打开 QQ，检查文字和图片是否都收到。文字成功、图片失败时不算通过。','收到后点击「我已收到文字和图片」，再打开右上角开关，开始接收任务通知。'].forEach(value=>{const item=document.createElement('li');item.textContent=value;list.append(item);});
      text('practice-title','收到文字和图片，才算验证完成'); get('practice-copy').replaceChildren(list);
      bridge.changed.connect(raw=>{state=JSON.parse(raw);render();});
      root.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',()=>navigate({tab:el.dataset.tab})));
      root.querySelectorAll('[data-phase]').forEach(el=>el.addEventListener('click',()=>navigate({phase:el.dataset.phase})));
      on('guide-open',()=>navigate({guide:true})); on('guide-back',()=>navigate({guide:false,tab:'setup'}));
      for(const id of ['close','done']) on(id,()=>send('close'));
      on('secret-toggle',()=>{const el=get('secret');el.type=el.type==='password'?'text':'password';get('secret-toggle').setAttribute('aria-label',el.type==='password'?'显示密钥':'隐藏密钥');});
      const credentials=()=>send('credentials',{app_id:get('app-id').value,secret:get('secret').value,remember_secret:get('remember').checked});
      for(const id of ['app-id','secret']) get(id).addEventListener('input',credentials);
      get('remember').addEventListener('change',credentials);
      on('verify',()=>send('verify'));
      for(const kind of ['user','group']) on('bind-'+kind,()=>send('bind',{kind}));
      on('bind-cancel',()=>send('cancel')); on('cancel-operation',()=>send('cancel')); on('guide-cancel',()=>send('cancel')); on('copy-code',()=>send('copy'));
      on('test-send',()=>send('test')); on('confirm-received',()=>send('confirm'));
      for(const [id,key] of [['enabled','enabled'],['user-on','user_enabled'],['group-on','group_enabled'],['attach-image','attach_image'],['event-done','on_completed'],['event-error','on_failed'],['event-stop','on_stopped']]) get(id).addEventListener('change',event=>send('update',{key,value:event.target.checked}));
      on('review-credentials',()=>navigate({phase:'register',step:9})); on('platform',()=>send('platform'));
      get('highlight').addEventListener('change',event=>{highlight=event.target.checked;drawGuide();});
      on('full-image',()=>{full=!full;drawGuide();});
      on('prev',()=>{const v=state.view;navigate(v.phase==='test'?{phase:'bind'}:v.phase==='bind'?{phase:'register',step:11}:{step:Math.max(0,v.step-1)});});
      on('next',()=>{const v=state.view;navigate(v.phase==='register'?(v.step<11?{step:v.step+1}:{phase:'bind'}):v.phase==='bind'?{phase:'test'}:{guide:false,tab:'setup'});});
      on('practice-action',()=>navigate({phase:'bind'}));
      root.querySelector('.bd-screenshot-stage').addEventListener('click',zoom);
      root.querySelector('.bd-screenshot-stage').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();zoom();}});
      on('zoom-close',()=>get('zoom').close()); on('zoom-fit',fitZoom);
      on('zoom-in',()=>{zoomScale=Math.min(3,zoomScale*1.25);resizeZoom();}); on('zoom-out',()=>{zoomScale=Math.max(.1,zoomScale*.8);resizeZoom();});
      get('loading').remove(); render(); root.dataset.ready='true';
    });
  });
})();
