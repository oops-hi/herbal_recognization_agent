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
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from PIL import Image, UnidentifiedImageError

from config import (
    BASE_DIR,
    UPLOAD_DIR,
    MAX_UPLOAD_MB,
    ALLOWED_EXT,
    LOW_CONF_THRESHOLD,
)
from agent import core
from kg import builder, query as kq

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ---- 会话（服务端 dict，client_id 来自前端 localStorage） ----
# session[client_id] = {"messages": [...], "current_herb": str|None, "last_active": ts}
sessions: dict[str, dict] = {}
MAX_SESSIONS = 200


def _get_session(client_id: str) -> dict:
    """取会话（不存在则新建）；超量时淘汰最久未活动的会话。"""
    if len(sessions) >= MAX_SESSIONS:
        oldest = min(sessions, key=lambda k: sessions[k]["last_active"])
        sessions.pop(oldest, None)
    sess = sessions.setdefault(client_id, {
        "messages": [],
        "current_herb": None,
        "last_active": time.time(),
    })
    sess["last_active"] = time.time()
    return sess


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

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/graph")
def graph_page():
    return render_template("graph.html")


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
    _get_session(client_id)

    # 保存：uploads/<client_id>/<时间戳><ext>；新图上传时清理该用户旧图（设计 4）
    user_dir = UPLOAD_DIR / client_id
    user_dir.mkdir(parents=True, exist_ok=True)
    for old in user_dir.iterdir():
        if old.is_file():
            old.unlink(missing_ok=True)
    save_path = user_dir / f"{int(time.time())}{ext}"
    file.save(save_path)

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
                        "client_id": client_id}), 200
    try:
        top3 = predictor.predict_topk(save_path, k=3)
    except Exception as e:
        return jsonify({"error": f"图片识别失败：{type(e).__name__}: {e}"}), 500

    top1, conf = top3[0]
    cards = [{"name": n, "confidence": c} for n, c in top3]

    if conf < LOW_CONF_THRESHOLD:
        # FR-03：低置信度拒绝下结论，给补拍建议（识别结果保留供人工判断）
        return jsonify({
            "status": "low_confidence",
            "top3": cards,
            "advice": [
                "识别置信度较低，无法确认是否为某一味药材（可能为非药材或拍摄条件不佳）",
                "建议补拍：干燥饮片特写、光照均匀、纯色背景",
                "以下 Top-3 供人工参考，请勿自行采食或药用",
            ],
            "client_id": client_id,
        }), 200

    # 识别成功 → 写入会话上下文（后续『这个/它』指代消解）
    sess = _get_session(client_id)
    sess["current_herb"] = top1
    return jsonify({
        "status": "ok",
        "top1": top1,
        "confidence": conf,
        "top3": cards,
        "profile": kq.get_profile(top1),
        "client_id": client_id,
    }), 200


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
        if not core.is_configured():
            yield sse_event({
                "type": "error",
                "text": "未配置 DEEPSEEK_API_KEY，对话服务不可用。请配置 .env 后重启。",
                "status": "no_key",
            })
            return
        for ev in core.stream_run(
            question,
            sess["messages"],
            current_herb=sess.get("current_herb"),
        ):
            yield sse_event(ev)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反代不做缓冲（若部署在 nginx 后）
            "Connection": "keep-alive",
        },
    )


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
    # ⚠️ 禁止 debug=True：werkzeug reloader 子进程里 import torch 触发
    #    torch 2.12.0.dev 的 _native.dsl_registry 循环导入（上传识别 500），
    #    实测 debug=False 完全正常。答辩演示用非 debug 模式更稳。
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
