"use strict";
(() => {
  const byId = id => document.getElementById(id);
  let current = 0;
  let fullView = false;
  let originalSize = false;
  const image = byId("step-image");
  const stage = byId("image-stage");
  const dialog = byId("image-dialog");
  const nav = byId("step-list");
  QQ_STEPS.forEach((step, i) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "step-item";
    button.setAttribute("aria-label", `第 ${i + 1} 步：${step.title}`);
    const number = document.createElement("span");
    number.className = "step-number";
    number.textContent = String(i + 1).padStart(2, "0");
    const name = document.createElement("span");
    name.className = "step-name";
    name.textContent = step.title;
    button.append(number, name);
    button.addEventListener("click", () => go(i));
    item.append(button);
    nav.append(item);
  });

  function highlight(layer, boxes, view) {
    layer.replaceChildren();
    if (!byId("show-hints").checked) return;
    boxes.forEach((box, i) => {
      const mark = document.createElement("div");
      mark.className = "hotspot";
      Object.assign(mark.style, {
        left: `${(box[0] - view[0]) / view[2] * 100}%`,
        top: `${(box[1] - view[1]) / view[3] * 100}%`,
        width: `${box[2] / view[2] * 100}%`,
        height: `${box[3] / view[3] * 100}%`
      });
      if (boxes.length > 1) {
        const badge = document.createElement("span");
        badge.textContent = i + 1;
        mark.append(badge);
      }
      layer.append(mark);
    });
  }

  function renderImage() {
    const step = QQ_STEPS[current];
    const view = fullView ? [0, 0, ...step.size] : step.crop;
    stage.style.aspectRatio = `${view[2]} / ${view[3]}`;
    Object.assign(image.style, {
      width: `${step.size[0] / view[2] * 100}%`,
      height: `${step.size[1] / view[3] * 100}%`,
      left: `${-view[0] / view[2] * 100}%`, top: `${-view[1] / view[3] * 100}%`
    });
    highlight(byId("focus-layer"), step.focus, view);
    byId("focus-view").setAttribute("aria-pressed", String(!fullView));
    byId("full-view").setAttribute("aria-pressed", String(fullView));
    byId("image-caption").textContent = fullView ? "完整截图 · 可点击「放大查看」检查细节" : "当前操作区 · 切换「完整截图」可查看页面全貌";
  }

  function render() {
    const step = QQ_STEPS[current];
    byId("phase").textContent = step.phase;
    byId("step-title").textContent = step.title;
    byId("counter").textContent = `${String(current + 1).padStart(2, "0")} / ${QQ_STEPS.length}`;
    byId("progress-fill").style.width = `${(current + 1) / QQ_STEPS.length * 100}%`;
    byId("actions").replaceChildren(...step.actions.map(action => {
      const li = document.createElement("li"); li.textContent = action; return li;
    }));
    byId("detail").textContent = step.detail;
    byId("instructions").classList.toggle("caution", !!step.caution);
    byId("expected-result").textContent = step.result;
    const link = byId("step-link");
    link.hidden = !step.link;
    if (step.link) { link.textContent = step.link.text + " ↗"; link.href = step.link.href; }
    image.src = step.image;
    image.alt = `第 ${current + 1} 步：${step.title}的平台截图`;
    image.onerror = () => { byId("image-caption").textContent = "截图未能加载，请保留教程目录内的 images 文件夹。"; };
    byId("previous").disabled = current === 0;
    byId("next").textContent = current === QQ_STEPS.length - 1 ? "查看测试步骤 ↓" : "下一步 →";
    Array.from(nav.querySelectorAll("button")).forEach((button, i) => {
      if (i === current) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      button.classList.toggle("completed", i < current);
    });
    renderImage();
  }

  function go(index, scroll = true) {
    current = Math.min(QQ_STEPS.length - 1, Math.max(0, index));
    history.replaceState(null, "", `#step-${current + 1}`);
    render();
    if (scroll) document.querySelector(".step-content").scrollIntoView({block: "start"});
  }

  function hashStep() {
    const match = location.hash.match(/^#step-(\d+)$/);
    const index = match ? Number(match[1]) - 1 : 0;
    current = Math.min(QQ_STEPS.length - 1, Math.max(0, index));
    render();
  }

  byId("previous").addEventListener("click", () => go(current - 1));
  byId("next").addEventListener("click", () => {
    if (current < QQ_STEPS.length - 1) go(current + 1);
    else byId("test-next").scrollIntoView({block: "start"});
  });
  byId("skip-creation").addEventListener("click", () => go(8));
  byId("back-start").addEventListener("click", () => go(0));
  byId("focus-view").addEventListener("click", () => { fullView = false; renderImage(); });
  byId("full-view").addEventListener("click", () => { fullView = true; renderImage(); });
  byId("show-hints").addEventListener("change", renderImage);
  byId("enlarge").addEventListener("click", () => {
    const step = QQ_STEPS[current];
    byId("zoom-title").textContent = step.title;
    byId("zoom-image").src = step.image;
    byId("zoom-image").alt = image.alt;
    originalSize = false;
    byId("zoom-stage").style.width = "100%";
    byId("zoom-size").textContent = "原始尺寸";
    byId("zoom-size").setAttribute("aria-pressed", "false");
    highlight(byId("zoom-focus-layer"), step.focus, [0, 0, ...step.size]);
    dialog.showModal();
    byId("zoom-scroll").scrollTo(0, 0);
  });
  byId("zoom-size").addEventListener("click", () => {
    originalSize = !originalSize;
    byId("zoom-stage").style.width = originalSize ? `${QQ_STEPS[current].size[0]}px` : "100%";
    byId("zoom-size").textContent = originalSize ? "适应窗口" : "原始尺寸";
    byId("zoom-size").setAttribute("aria-pressed", String(originalSize));
  });
  byId("close-dialog").addEventListener("click", () => dialog.close());
  document.addEventListener("keydown", event => {
    if (dialog.open || ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A", "SUMMARY"].includes(event.target.tagName) || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowRight" && current < QQ_STEPS.length - 1) { event.preventDefault(); go(current + 1); }
    if (event.key === "ArrowLeft" && current > 0) { event.preventDefault(); go(current - 1); }
  });
  window.addEventListener("hashchange", hashStep);
  hashStep();
})();
