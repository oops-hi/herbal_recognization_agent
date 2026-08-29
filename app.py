"""
app.py
Flask Web 后端：上传识别 + 多轮对话（SSE 流式工具链）+ 图谱页 + 档案 API。

设计约定（CLAUDE.md 关键设计 / 需求分析 5.1 架构图）：
- 会话：服务端 dict + localStorage client_id（cookie session 4KB 装不下工具返回）
- 上传文件不删除（智能体需跨轮次重复识别）；新图上传时清理旧图；启动时清理 24h 前文件
- SSE：fetch + ReadableStream（POST 语义，EventSource 用不了）
- fail-soft：无模型/无密钥/断网时识别与图谱仍可用，仅对话降级，不让请求 500
- 低置信度（Top-1 < LOW_CONF_THRESHOLD，现 0.75）拒答并给补拍建议（FR-03）

启动：conda run -n task python app.py  →  http://localhost:5000
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context
from PIL import Image, UnidentifiedImageError
from werkzeug.serving import make_server

from config import (
    BASE_DIR,
    FROZEN,
    UPLOAD_DIR,
    SESSION_FILE,
    MAX_UPLOAD_MB,
    ALLOWED_EXT,
    LOW_CONF_THRESHOLD,
    llm_state,
    normalize_endpoint,
    save_llm_config,
    save_vision_config,
    vision_state,
)
from agent import core, vision as vmod
from kg import builder, query as kq

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ---- Vue 构建产物托管（打包/桌面版） ----
# prod（frozen）→ 打包进 _MEIPASS 的 frontend_dist；dev（Electron spawn）→ frontend/out/renderer
# （electron-vite 构建产物目录；裸跑 python app.py 无 dist 时自动回退 Jinja 模板）
FRONTEND_DIST = Path(os.environ.get(
    "HERB_DIST_DIR",
    (BASE_DIR / "frontend_dist") if FROZEN else (BASE_DIR / "frontend" / "out" / "renderer"),
))

# ---- 会话（服务端 dict，client_id 来自前端 localStorage） ----
# session[client_id] = {"messages": [...], "current_herb": str|None, "last_active": ts,
#                       "stats": {...上下文统计}, "upload_ctx": str|None(拒识上传注记)}
# 重启不丢：本次加落盘 sessions.json（原子写 + fail-soft），与 .env 同目录
sessions: dict[str, dict] = {}
MAX_SESSIONS = 200

# 与前端 ContextStatsBar 字段对齐（跨重启恢复时补默认值，兼容旧格式）
SESSION_STATS_DEFAULTS = {
    "turns": 0, "steps": 0, "llm_time": 0.0, "tool_time": 0.0,
    "tokens_in": 0, "tokens_out": 0, "cache_hit": 0, "cache_miss": 0,
}

_SAFE_PATH_SEG = re.compile(r"^[A-Za-z0-9._-]+$")


def _get_session(client_id: str) -> dict:
    """取会话（不存在则新建）；超量时淘汰最久未活动的会话。"""
    if len(sessions) >= MAX_SESSIONS:
        oldest = min(sessions, key=lambda k: sessions[k]["last_active"])
        sessions.pop(oldest, None)
    sess = sessions.setdefault(client_id, {
        "messages": [],
        "current_herb": None,
        "last_active": time.time(),
        "stats": SESSION_STATS_DEFAULTS.copy(),
        "upload_ctx": None,
    })
    sess["last_active"] = time.time()
    return sess


def _load_sessions() -> None:
    """启动时从 SESSION_FILE 恢复会话（fail-soft：缺失/损坏/非法条目一律静默丢弃）。

    结构校检：messages 必须 list、current_herb str|None、stats 补默认字段、
    last_active float 兜底；非法条目丢弃；超出 MAX_SESSIONS 按 last_active 淘汰。
    """
    global sessions
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        raw = data.get("sessions", {})
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    restored: dict[str, dict] = {}
    for cid, s in raw.items():
        if not isinstance(cid, str) or not isinstance(s, dict):
            continue
        msgs = s.get("messages")
        if not isinstance(msgs, list):
            continue
        cur = s.get("current_herb")
        if cur is not None and not isinstance(cur, str):
            continue
        uc = s.get("upload_ctx")
        if uc is not None and not isinstance(uc, str):
            uc = None
        st = s.get("stats") if isinstance(s.get("stats"), dict) else {}
        try:
            last = float(s.get("last_active", 0)) or time.time()
        except (TypeError, ValueError):
            last = time.time()
        restored[cid] = {
            "messages": [m for m in msgs if isinstance(m, dict)],
            "current_herb": cur,
            "last_active": last,
            "stats": {k: st.get(k, default) for k, default in SESSION_STATS_DEFAULTS.items()},
            "upload_ctx": uc,
        }
    if len(restored) > MAX_SESSIONS:
        for cid in sorted(restored, key=lambda k: restored[k]["last_active"])[:len(restored) - MAX_SESSIONS]:
            restored.pop(cid, None)
    if restored:
        sessions = restored
        print(f"[启动] 已恢复 {len(sessions)} 个会话（{SESSION_FILE}）")


def _reset_messages_for_new_upload(sess: dict) -> None:
    """新上传图片 = 新识别语境：历史里关于旧图片的问答全部作废。

    实测（2026-08-29）：只靠提示词注记防不住——模型会照抄自己上一轮的句式
    （先传猫图被拒、再传山楂，问『这是啥』仍答猫科动物；反向时还编造
    『当前识别上下文=枸杞子』去调工具）。根因=模型对自身历史回复的自我一致性
    偏见强于 system 注记。所以新上传时把消息历史整体切除，只留底座 system
    prompt（HERB_CTX/UPLOAD_CTX 注记由 stream_run 按最新判定重建）。
    tool_calls 历史随切除消失，不存在悬空 tool_calls（_sanitize_history 兜底仍在）。
    文本-only 对话不经过 /upload，不受影响；同一张图生命周期内的多轮追问照常累积。
    """
    sess["messages"][:] = [
        m for m in sess["messages"]
        if m.get("role") == "system"
        and str(m.get("content", "")).startswith("你是「多模态中草药识别智能体」")
    ]


def _save_sessions() -> None:
    """会话落盘：原子写（.tmp + os.replace）+ fail-soft（磁盘满/只读不阻塞对话）。

    消息含工具执行结果（未截断），量级答辩演示足够；JSON 序列化失败（TypeError）
    同样静默，绝不让对话链路崩。
    """
    try:
        blob = {"version": 1, "saved_at": time.time(), "sessions": sessions}
        tmp = SESSION_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SESSION_FILE)
    except (OSError, TypeError):
        pass


_load_sessions()


def _cleanup_old_uploads() -> None:
    """启动时清理 24h 前的上传文件（设计 4）。"""
    now = time.time()
    removed = 0
    for f in UPLOAD_DIR.glob("**/*") if UPLOAD_DIR.exists() else []:
        if f.is_file() and now - f.stat().st_mtime > 24 * 3600:
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        print(f"[启动] 已清理 {removed} 个 24h 前的上传文件")


# ---------- 页面 ----------

def _serve_spa():
    """Vue SPA 壳（hash 路由，#/ 与 #/graph 都是同一个 index.html）。"""
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.get("/assets/<path:filename>")
def spa_assets(filename: str):
    """Vue 构建产物静态资源（index.html 引用 /assets/*.js|css|png）。"""
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.get("/")
def index():
    if os.environ.get("HERB_SERVE_DIST", "1") == "1" and FRONTEND_DIST.is_dir():
        return _serve_spa()
    return render_template("index.html")  # 旧 Web 版兜底


@app.get("/graph")
def graph_page():
    if os.environ.get("HERB_SERVE_DIST", "1") == "1" and FRONTEND_DIST.is_dir():
        return _serve_spa()
    return render_template("graph.html")  # 旧 Web 版兜底


# ---------- 上传 + 识别 ----------

@app.post("/upload")
def upload():
    """上传图片 → 校验 → 识别 → 识别卡/拒答（FR-01/02/03）。"""
    file = request.files.get("image")
    if file is None or file.filename == "":
        return jsonify({"error": "未选择文件，请先上传图片"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify({
            "error": f"不支持的文件格式 {ext or '(无扩展名)'}，仅支持 "
                     f"{' / '.join(sorted(ALLOWED_EXT))}"
        }), 400

    client_id = (request.form.get("client_id") or "").strip() or uuid.uuid4().hex
    # 硬校验：client_id 直接拼文件路径，防目录穿越（非法值回退随机 id）
    if not _SAFE_PATH_SEG.match(client_id):
        client_id = uuid.uuid4().hex
    sess = _get_session(client_id)

    # 保存：uploads/<client_id>/<时间戳><ext>；同名目录仅保留最近 5 张
    # （图片消息跨重启要旧图显示；agent 识药工具按 image_path 需保留最新；24h 启动清理兜底）
    # ⚠️ 清理必须在保存后执行：保存前算 files[:-5] 会留下 6 张
    user_dir = UPLOAD_DIR / client_id
    user_dir.mkdir(parents=True, exist_ok=True)
    save_path = user_dir / f"{int(time.time())}{ext}"
    file.save(save_path)
    files = sorted((p for p in user_dir.iterdir() if p.is_file()), key=lambda p: p.name)
    for old in files[:-5]:
        old.unlink(missing_ok=True)
    image_url = f"/uploads/{client_id}/{save_path.name}"

    # 图片完整性校验
    try:
        with Image.open(save_path) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        save_path.unlink(missing_ok=True)
        return jsonify({"error": "图片文件无效或已损坏，请上传有效的图片"}), 400

    # 识别（fail-soft：模型未就绪 → 明确提示，不 500）
    from classifier import predictor

    ready, reason = predictor.is_ready()
    if not ready:
        return jsonify({"status": "model_not_ready", "message": reason,
                        "client_id": client_id, "image_url": image_url}), 200
    try:
        top3 = predictor.predict_topk(save_path, k=3)
    except Exception as e:
        return jsonify({"error": f"图片识别失败：{type(e).__name__}: {e}"}), 500

    top1, conf = top3[0]
    cards = [{"name": n, "confidence": c} for n, c in top3]

    # 可选 VLM 二段验证（二期 V2-A6，方案 §8.5；fail-soft 绝不阻断主流程）。
    # 灰区触发（conf≥0.60 且命中 T1/T2/T3），双通道一致可提信放行低置信图。
    vision_meta = {"state": "skipped"}
    try:
        if vmod.should_use_vision(top3):
            vision_meta = vmod.decide(top3, vmod.verify(save_path))
    except Exception as e:
        vision_meta = {"state": "unavailable", "reason": f"vision_error:{type(e).__name__}"}

    # 新上传 = 新识别语境：下面的每个判定分支都会写 current_herb/upload_ctx，
    # 先切除旧图片相关的全部问答历史（防模型照抄旧结论——见函数注释的实测）
    _reset_messages_for_new_upload(sess)

    if vision_meta["state"] == "non_herb":
        # 域外图一票否决（VLM 能力实测边界：域外判定可靠，域内细粒度弱于本地——仅此方向有否决权）
        cat = vision_meta.get("category") or ""
        hint = f"疑似「{cat}」照片" if cat else "图片可能不是中药饮片"
        # 拒识也要进上下文：后续追问『这是什么』时模型知道上传过一张非药材图（清掉旧药材上下文）
        sess["current_herb"] = None
        sess["upload_ctx"] = (
            f"用户上传过一张图片，但判定为非中药饮片（{hint}），已拒绝下结论。"
            "若用户追问这张图片，请如实告知判定结果，并建议补拍干燥饮片特写、光照均匀、纯色背景。"
        )
        _save_sessions()
        return jsonify({
            "status": "low_confidence",   # 复用前端既有拒答卡片（后端字段向后兼容扩展）
            "refuse_reason": "域外图",
            "top3": cards,
            "advice": [
                f"云端视觉复核判定：{hint}，非中药饮片，拒绝下结论",
                "请上传干燥饮片特写（果实种子类）、光照均匀、纯色背景",
                "若确为药材请重新拍摄后重试",
            ],
            "vision": vision_meta,
            "client_id": client_id,
            "image_url": image_url,
        }), 200

    if vision_meta["state"] == "none":
        # 云端无法确认 → 置信不足 + 补拍建议（不硬猜）
        sess["current_herb"] = None
        sess["upload_ctx"] = (
            "用户上传过一张图片，但识别置信不足、未能确认药材（可能为非药材或拍摄条件不佳）。"
            "若用户追问这张图片，请如实告知，并建议补拍干燥饮片特写、光照均匀、纯色背景。"
        )
        _save_sessions()
        return jsonify({
            "status": "low_confidence",
            "refuse_reason": "置信不足",
            "top3": cards,
            "advice": [
                "云端视觉复核未能确认图片所属药材（疑似非药材或拍摄条件不佳）",
                "建议补拍：干燥饮片特写、光照均匀、纯色背景",
                "以下 Top-3 供人工参考，请勿自行采食或药用",
            ],
            "vision": vision_meta,
            "client_id": client_id,
            "image_url": image_url,
        }), 200

    # 本地低置信拒答：conf<0.60 一律拒答（VLM 一致也不放行——本地太弱，防 0.49 猫图
    # 被 VLM 误认药材后提信放行）；0.60~0.75 仅双通道一致（consistent）提信放行
    if conf < LOW_CONF_THRESHOLD and (conf < vmod.LOW_CONF_BOUND[0] or vision_meta["state"] != "consistent"):
        sess["current_herb"] = None
        sess["upload_ctx"] = (
            "用户上传过一张图片，但识别置信度不足，未能确认药材（可能为非药材或拍摄条件不佳）。"
            "若用户追问这张图片，请如实告知，并建议补拍干燥饮片特写、光照均匀、纯色背景。"
        )
        _save_sessions()
        return jsonify({
            "status": "low_confidence",
            "refuse_reason": "置信不足",
            "top3": cards,
            "advice": [
                "识别置信度较低，无法确认是否为某一味药材（可能为非药材或拍摄条件不佳）",
                "建议补拍：干燥饮片特写、光照均匀、纯色背景",
                "以下 Top-3 供人工参考，请勿自行采食或药用",
            ],
            "vision": vision_meta,
            "client_id": client_id,
            "image_url": image_url,
        }), 200

    # 识别成功（双通道一致提信 或 本地高置信云端未参与）→ 写入会话上下文（后续『这个/它』指代消解）
    sess["current_herb"] = top1
    sess["upload_ctx"] = None   # 成功识别新图：清掉旧的拒识注记
    _save_sessions()
    return jsonify({
        "status": "ok",
        "top1": top1,
        "confidence": conf,
        "top3": cards,
        "profile": kq.get_profile(top1),
        "vision": vision_meta,
        "client_id": client_id,
        "image_url": image_url,
    }), 200


# ---------- 上传图片静态访问（聊天流图片消息跨重启显示用） ----------

@app.get("/uploads/<client_id>/<filename>")
def uploads_file(client_id: str, filename: str):
    """图片静态访问：/uploads/<client_id>/<时间戳>.<ext>（与 /upload 落盘路径一致）。

    防穿越双保险：白名单正则 + realpath 前缀校验（send_from_directory 内部也会拒，双保险确保明确中文错误）。
    """
    if not _SAFE_PATH_SEG.match(client_id) or not _SAFE_PATH_SEG.match(filename):
        return jsonify({"error": "非法文件路径"}), 400
    base = UPLOAD_DIR.resolve()
    target = (base / client_id / filename).resolve()
    if not target.is_relative_to(base):
        return jsonify({"error": "非法文件路径"}), 400
    if not target.is_file():
        return jsonify({"error": "文件不存在或已清理"}), 404
    return send_from_directory(UPLOAD_DIR, f"{client_id}/{filename}")


# ---------- 对话（SSE 流式工具链） ----------

@app.post("/chat")
def chat():
    """多轮问答：SSE 流式推送智能体事件（工具逐个出现 → 最终回答）。"""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    client_id = (data.get("client_id") or "").strip() or uuid.uuid4().hex
    if not question:
        return jsonify({"error": "提问内容为空"}), 400

    sess = _get_session(client_id)

    def sse_event(obj: dict) -> str:
        import json
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def generate():
        try:
            if not core.is_configured():
                yield sse_event({
                    "type": "error",
                    "text": "未配置 API 密钥，对话服务不可用。请点击右上角 ⚙️ 设置页配置后立即生效。",
                    "status": "no_key",
                })
                return
            for ev in core.stream_run(
                question,
                sess["messages"],
                current_herb=sess.get("current_herb"),
                session_stats=sess.get("stats"),
                upload_ctx=sess.get("upload_ctx"),
            ):
                yield sse_event(ev)
        except GeneratorExit:
            raise  # 客户端断开：历史已在 stream_run 内保证完整，正常收尾
        except Exception as e:  # ⚠️ 任何非预期异常（非 requests 系）都必须给客户端一个 error 帧，
            #    否则流静默掐断 → 前端「无输出、卡死」（用户实测现象，2026-08-29）
            yield sse_event({"type": "error", "text": f"服务异常：{e}", "status": "error"})
        finally:
            # 落盘历史：正常收尾与客户端断开（GeneratorExit）都执行；
            # 工具轮次两遍循环已保证历史完整，这里只把结果写入磁盘
            _save_sessions()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反代不做缓冲（若部署在 nginx 后）
            "Connection": "keep-alive",
        },
    )


@app.post("/api/session/reset")
def session_reset():
    """重建当前会话（清空对话历史 + 识别上下文 + 上下文统计）。

    client_id 不变（前端 localStorage 持久），仅服务端会话对象重建。
    """
    client_id = (request.get_json(silent=True) or {}).get("client_id") or ""
    if client_id:
        sessions.pop(client_id, None)
        _get_session(client_id)
        # 立刻落盘：否则重启后旧历史又复活
        _save_sessions()
    return jsonify({"ok": True})


# ---------- 会话列表（历史会话查看/切换/删除，2026-08-29） ----------

@app.get("/api/sessions")
def sessions_list():
    """会话列表（按最近活动倒序）：历史会话切换 UI 的数据源。

    title = 首条用户消息（截 20 字，跳过系统注入/空消息，无则「新会话」）；
    updated = last_active（秒级时间戳）；前端本地索引与本列表合并去重。
    """
    items = []
    for cid, s in sessions.items():
        title = ""
        for m in s.get("messages", []):
            if m.get("role") == "user":
                t = str(m.get("content", "")).strip()
                if t and not t.startswith("[系统注入]"):
                    title = t[:20]
                    break
        items.append({
            "id": cid,
            "title": title or "新会话",
            "updated": s.get("last_active", 0),
            "messages": len(s.get("messages", [])),
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return jsonify({"sessions": items})


@app.delete("/api/sessions/<client_id>")
def session_delete(client_id: str):
    """删除单个会话（历史会话管理）；不存在也返回 ok（幂等）。"""
    if client_id in sessions:
        sessions.pop(client_id, None)
        _save_sessions()
    return jsonify({"ok": True})


# ---------- LLM 配置（设置页 · 应用内热更新，无需重启） ----------

@app.get("/api/config")
def config_get():
    """当前 LLM 配置快照（key 只回显掩码）。"""
    return jsonify(llm_state())


@app.post("/api/config")
def config_post():
    """保存 LLM 配置：写 exe 旁 .env → 热更新运行状态（对话立即生效）。

    入参 {base_url, api_key, model, clear_key}；api_key 留空 = 保留已保存 key。
    """
    data = request.get_json(silent=True) or {}
    try:
        save_llm_config(
            data.get("base_url", ""),
            data.get("api_key", ""),
            data.get("model", ""),
            clear_key=bool(data.get("clear_key")),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **llm_state()})


@app.post("/api/config/test")
def config_test():
    """用提交的未保存值发最小 ping 请求，验证连通性（不落盘、不做工具链全链路）。"""
    data = request.get_json(silent=True) or {}
    try:
        url = normalize_endpoint(data.get("base_url", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    model = (data.get("model") or "").strip()
    key = (data.get("api_key") or "").strip()
    if not model:
        return jsonify({"error": "模型名不能为空"}), 400
    if not key:
        # 未填新 key：退回到已保存 key 测试（若都无 → 明确提示）
        import os
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            return jsonify({"error": "未填写 API Key（且当前没有已保存的 Key）"}), 400

    hint = {
        401: "鉴权失败（401）：API Key 无效，请检查后重试",
        402: "账户余额不足（402），请到服务商后台充值",
        404: "接口路径不存在（404）：请检查 API 地址是否以 /chat/completions 结尾",
        429: "请求过于频繁（429）：请稍后再试",
    }
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 1, "stream": False},
            timeout=10,
        )
    except requests.Timeout:
        return jsonify({"error": "连接超时：请检查 API 地址与网络"}), 502
    except requests.ConnectionError:
        return jsonify({"error": "无法连接到该地址：请检查 API 地址是否正确、服务是否已启动（本地模型如 Ollama 需先运行）"}), 502
    if resp.status_code == 200:
        return jsonify({"ok": True, "message": f"连接成功（{model}），配置可正常使用"})
    if resp.status_code in hint:
        return jsonify({"error": hint[resp.status_code]}), 400
    return jsonify({"error": f"服务返回异常（{resp.status_code}）：{resp.text[:120]}"}), 400


# ---------- VLM 配置（设置页「视觉验证」卡片 · 二期 V2-A6，独立于聊天 LLM 配置） ----------

@app.get("/api/vision-config")
def vision_config_get():
    """当前 VLM 配置快照（key 只回显掩码）。"""
    return jsonify(vision_state())


@app.post("/api/vision-config")
def vision_config_post():
    """保存 VLM 配置：写 exe 旁 .env（VISION_* 键）→ 热更新运行状态（上传识别立即生效）。

    入参 {base_url, api_key, model, enabled, clear_key}；api_key 留空 = 保留已保存 key。
    """
    data = request.get_json(silent=True) or {}
    try:
        save_vision_config(
            data.get("base_url", ""),
            data.get("api_key", ""),
            data.get("model", ""),
            enabled=bool(data.get("enabled")),
            clear_key=bool(data.get("clear_key")),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **vision_state()})


@app.post("/api/vision-config/test")
def vision_config_test():
    """用提交的未保存值发最小图像请求验证连通性（1x1 像素图，不落盘）。"""
    import base64 as _b64
    import io as _io
    from PIL import Image as _PIL

    data = request.get_json(silent=True) or {}
    try:
        url = normalize_endpoint(data.get("base_url", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    model = (data.get("model") or "").strip()
    key = (data.get("api_key") or "").strip()
    if not model:
        return jsonify({"error": "模型名不能为空"}), 400
    if not key:
        key = os.environ.get("VISION_API_KEY", "")
        if not key:
            return jsonify({"error": "未填写 API Key（且当前没有已保存的 Key）"}), 400

    # 内存生成 1x1 红色像素图（测完整图像链路，不落盘）
    buf = _io.BytesIO()
    _PIL.new("RGB", (1, 1), (200, 30, 30)).save(buf, "JPEG")
    data_url = "data:image/jpeg;base64," + _b64.b64encode(buf.getvalue()).decode()

    hint = {
        401: "鉴权失败（401）：API Key 无效，请检查后重试",
        402: "账户余额不足（402），请到服务商后台充值",
        404: "接口路径不存在（404）：请检查 API 地址是否以 /chat/completions 结尾",
        429: "请求过于频繁（429）：请稍后再试",
    }
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "ping"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}],
                "max_tokens": 8,
                "stream": False,
            },
            timeout=10,
        )
    except requests.Timeout:
        return jsonify({"error": "连接超时：请检查 API 地址与网络"}), 502
    except requests.ConnectionError:
        return jsonify({"error": "无法连接到该地址：请检查 API 地址是否正确、服务是否已启动"}), 502
    if resp.status_code == 200:
        return jsonify({"ok": True, "message": f"连接成功（{model}），视觉验证通道可正常使用"})
    if resp.status_code in hint:
        return jsonify({"error": hint[resp.status_code]}), 400
    return jsonify({"error": f"服务返回异常（{resp.status_code}）：{resp.text[:120]}"}), 400


# ---------- 图谱 / 档案 / 健康 ----------

@app.get("/api/herb/<name>")
def herb_api(name: str):
    """单味药档案 JSON（图谱页离线检索用）。"""
    return Response(kq.dump_for_json(name), mimetype="application/json; charset=utf-8")


@app.get("/api/graph")
def graph_api():
    """全量拓扑 JSON（图谱页交互式力导向图数据源）。

    注意不能用 jsonify：Flask 2.2.2 默认 ensure_ascii=True，90% 中文的载荷
    会转义成 \\uXXXX 膨胀约 2 倍 —— 与 /api/herb 的 dump_for_json 同模式。
    """
    return Response(
        json.dumps(kq.graph_dataset(), ensure_ascii=False),
        mimetype="application/json; charset=utf-8",
    )


@app.get("/api/health")
def health():
    from classifier import predictor

    ready, reason = predictor.is_ready()
    return jsonify({
        "status": "ok",
        "model_ready": ready,
        "model_reason": reason,
        "deepseek_configured": core.is_configured(),
        "graph": kq.graph_stats(),
    })


# ---------- 错误兜底（不让前端看到裸 500） ----------

@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": f"图片过大，请上传 {MAX_UPLOAD_MB}MB 以内的图片"}), 413


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "接口不存在"}), 404


@app.errorhandler(500)
def server_error(e):
    print(f"[500] {e}")
    return jsonify({"error": "服务器内部错误，请稍后重试"}), 500


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(exist_ok=True)
    _cleanup_old_uploads()
    builder.load()  # 预热知识图谱
    # ⚠️ 禁止 debug=True / reloader：werkzeug reloader 子进程里 import torch 触发
    #    torch 2.12.0.dev 的 _native.dsl_registry 循环导入（上传识别 500），
    #    实测 debug=False 完全正常。答辩演示用非 debug 模式更稳。
    host = os.environ.get("HERB_HOST", "0.0.0.0")   # 裸跑 python app.py 行为不变
    port = int(os.environ.get("HERB_PORT", "5000"))
    with make_server(host, port, app, threaded=True) as srv:
        # Electron 主进程从 stdout 解析此握手行拿实际端口（HERB_PORT=0 → 系统分配）
        print(f"HERB_READY_PORT={srv.server_port}", flush=True)
        srv.serve_forever()
