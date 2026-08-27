/* ============================================================
   多模态中草药识别智能体 · 前端逻辑
   - client_id：localStorage 持久（会话记忆服务端 dict 的钥匙）
   - 上传：拖拽/点击 → POST /upload → 识别卡 / 拒答建议
   - 对话：fetch + ReadableStream 解析 SSE（POST 语义，EventSource 用不了）
   - 时间轴：每个问题一组工具调用链，可折叠，实时逐条出现
   ============================================================ */
(function () {
  "use strict";

  // ---------- 工具 ----------
  const $ = (id) => document.getElementById(id);
  const chatBody = $("chat-body");
  const clientKey = "herb_agent_client_id";

  function getClientId() {
    let id = localStorage.getItem(clientKey);
    if (!id) {
      id = (crypto.randomUUID && crypto.randomUUID()) ||
           "c" + Date.now() + Math.random().toString(16).slice(2);
      localStorage.setItem(clientKey, id);
    }
    return id;
  }

  function scrollBottom() {
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  // opts.markdown=true 时渲染 markdown（仅智能体回答，本地渲染器离线可用）
  function addMsg(kind, text, opts) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + kind;
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = kind === "user" ? "你" :
                      kind === "error" ? "系统" :
                      kind === "system-note" ? "·" : "智能体";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (opts && opts.markdown && window.renderMarkdown) {
      bubble.innerHTML = window.renderMarkdown(text);
    } else {
      bubble.textContent = text;
    }
    wrap.appendChild(who);
    wrap.appendChild(bubble);
    chatBody.appendChild(wrap);
    scrollBottom();
    return wrap;
  }

  // 时间轴：每个问题一个折叠块
  function createTimeline() {
    const tl = document.createElement("div");
    tl.className = "timeline";
    const ttl = document.createElement("div");
    ttl.className = "ttl";
    ttl.textContent = "▾ 工具调用链";
    ttl.addEventListener("click", () => {
      const collapsed = tl.dataset.collapsed === "1";
      tl.dataset.collapsed = collapsed ? "0" : "1";
      ttl.textContent = collapsed ? "▾ 工具调用链" : "▸ 工具调用链（已折叠）";
      tl.querySelectorAll(".tl-step").forEach((s) => (s.style.display = collapsed ? "" : "none"));
    });
    tl.appendChild(ttl);
    chatBody.appendChild(tl);
    scrollBottom();
    return tl;
  }

  function addToolStep(tl, name, args, result) {
    const step = document.createElement("div");
    step.className = "tl-step" + (String(result).includes("异常") || String(result).includes("失败") ? " bad" : "");
    const nameEl = document.createElement("span");
    nameEl.className = "name";
    nameEl.textContent = name;
    const argText = JSON.stringify(args);
    step.appendChild(nameEl);
    step.appendChild(document.createTextNode(argText.length > 60 ? argText.slice(0, 60) + "…" : argText));
    const res = document.createElement("div");
    res.className = "result";
    res.textContent = result;
    step.appendChild(res);
    tl.appendChild(step);
    scrollBottom();
  }

  // ---------- 健康检查 ----------
  async function healthCheck() {
    try {
      const j = await (await fetch("/api/health")).json();
      $("badge-model").textContent = j.model_ready ? "模型：就绪" : "模型：未就绪";
      $("badge-model").className = "badge " + (j.model_ready ? "ok" : "warn");
      $("badge-model").title = j.model_reason || "";
      $("badge-api").textContent = j.deepseek_configured ? "DeepSeek：已配置" : "DeepSeek：未配置";
      $("badge-api").className = "badge " + (j.deepseek_configured ? "ok" : "warn");
    } catch (e) {
      $("badge-model").textContent = "服务异常";
      $("badge-model").className = "badge warn";
    }
  }

  // ---------- 上传 ----------
  const dropzone = $("dropzone");
  const fileInput = $("file-input");

  function pickFile(file) {
    if (!file) return;
    if (!/\.(jpe?g|png|webp|bmp)$/i.test(file.name)) {
      showUploadError("不支持的文件格式，仅支持 jpg / png / webp / bmp");
      return;
    }
    if (file.size > 16 * 1024 * 1024) {
      showUploadError("图片过大，请上传 16MB 以内的图片");
      return;
    }
    // 本地预览
    $("preview-box").style.display = "block";
    $("preview-img").src = URL.createObjectURL(file);
    hideRecAndAdvice();
    upload(file);
  }

  async function upload(file) {
    const fd = new FormData();
    fd.append("image", file);
    fd.append("client_id", getClientId());
    try {
      const resp = await fetch("/upload", { method: "POST", body: fd });
      const j = await resp.json();
      if (!resp.ok) { showUploadError(j.error || "上传失败"); return; }
      if (j.status === "ok") {
        showRecCard(j);
        addMsg("system-note", "已识别： " + j.top1 + "（置信度 " + (j.confidence * 100).toFixed(1) + "%）—— 可直接提问，如『这个能和菊花一起泡水吗？』");
      } else if (j.status === "low_confidence") {
        showAdvice(j);
      } else if (j.status === "model_not_ready") {
        showUploadError(j.message + "。上传已保存，可先体验图谱查询与（配置密钥后的）对话。");
      }
    } catch (e) {
      showUploadError("网络异常：" + e.message);
    }
  }

  function showRecCard(j) {
    $("rec-card").style.display = "block";
    $("rec-top1").textContent = j.top1;
    $("rec-conf").textContent = "置信度 " + (j.confidence * 100).toFixed(1) + "%";
    $("rec-top3").textContent = "Top-3：" + j.top3.map((c) => c.name + " " + (c.confidence * 100).toFixed(0) + "%").join(" ｜ ");
    $("rec-profile").textContent = j.profile || "";
  }

  function showAdvice(j) {
    $("advice-box").style.display = "block";
    $("advice-box").innerHTML = "";
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = "无法确认药材，拒绝下结论";
    $("advice-box").appendChild(t);
    j.advice.forEach((a) => {
      const p = document.createElement("div");
      p.textContent = "· " + a;
      $("advice-box").appendChild(p);
    });
    if (j.top3 && j.top3.length) {
      const p = document.createElement("div");
      p.textContent = "Top-3 参考：" + j.top3.map((c) => c.name).join(" / ");
      $("advice-box").appendChild(p);
    }
  }

  function showUploadError(msg) {
    $("upload-error").style.display = "block";
    $("upload-error").textContent = msg;
  }

  function hideRecAndAdvice() {
    $("rec-card").style.display = "none";
    $("advice-box").style.display = "none";
    $("upload-error").style.display = "none";
  }

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    pickFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", () => pickFile(fileInput.files[0]));

  // ---------- 对话（SSE） ----------
  const chatInput = $("chat-input");
  const sendBtn = $("send-btn");
  let streaming = false;

  function handleEvent(ev, tl) {
    switch (ev.type) {
      case "tool":
        addToolStep(tl, ev.name, ev.arguments || {}, ev.result || "");
        break;
      case "answer":
        addMsg("assistant", ev.text || "（无内容）", { markdown: true });
        break;
      case "error":
        addMsg("error", ev.text || "服务异常");
        break;
    }
  }

  async function send() {
    const q = chatInput.value.trim();
    if (!q || streaming) return;
    chatInput.value = "";
    addMsg("user", q);
    const tl = createTimeline();
    streaming = true;
    sendBtn.disabled = true;

    try {
      const resp = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, client_id: getClientId() }),
      });
      if (!resp.headers.get("content-type") || !resp.headers.get("content-type").includes("text/event-stream")) {
        const j = await resp.json();
        addMsg("error", j.error || ("请求失败（HTTP " + resp.status + "）"));
        return;
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder("utf-8");
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (line.startsWith("data: ")) {
              try { handleEvent(JSON.parse(line.slice(6)), tl); }
              catch (e) { /* 忽略坏帧 */ }
            }
          }
        }
      }
    } catch (e) {
      addMsg("error", "网络异常：" + e.message);
    } finally {
      streaming = false;
      sendBtn.disabled = false;
      chatInput.focus();
    }
  }

  sendBtn.addEventListener("click", send);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  // ---------- 启动 ----------
  getClientId();
  healthCheck();
  chatInput.focus();
})();
