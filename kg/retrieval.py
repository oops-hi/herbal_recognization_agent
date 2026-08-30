"""
kg/retrieval.py —— 二期 P1：自研 numpy 向量检索（混合检索的向量召回层）。

背景：方案 §5.2 选型「规模小（60+ 味 / 几百条文档）、0 外部请求、断网可用」——
chromadb 1.5.9 拖入 40+ 依赖（onnxruntime/kubernetes client/pydantic 2.x）且代理下载不稳，
改为自研：bge-small-zh-v1.5 本地 embedding（transformers，CPU 可跑）+ numpy 归一化内积。
接口保持 chromadb 兼容抽象（ingest/search），未来体量上来可无痛换回。

数据流：data/tcm_docs/<version>/docs/*.json（kg/build_docs.py 生成，带 source+hash）
        → kg_rag/vectors.npy（float32 矩阵，N×512）+ kg_rag/index.json（元数据）
        → search(query) → [{herb, section, text, source, score, hash}, ...]

设计要点：
- 幂等入库：index.json 记录 built_from 指纹（version 目录 + MANIFEST.source_records_hash），
  指纹一致则跳过（--force 强制重建）
- fail-soft：模型缺失 / index 缺失 → search 返回 None，调用方（query.retrieve_doc）降级提示
- 哈希溯源：文档 hash 来自 build_docs（sha256(text)），随命中结果返回，供证据链展示
- 阈值：MIN_SCORE=0.45 以下视为无证据（bge 中文语义相似度经验值，见 config 注释风格）

用法：
    conda run -n task python -m kg.retrieval --ingest --force   # 重建向量库
    conda run -n task python -m kg.retrieval --query "哪种药能明目"
    conda run -n task python -m kg.retrieval --teach-ingest      # 重建讲解向量库（STUDY）
    conda run -n task python -m kg.retrieval --query "什么药润肺" --lib teach

双库（参数化复用，全部默认参数向后兼容）：
    kg   档案语料 data/tcm_docs → kg_rag/          （FIELDS，含 herb/section/text/source/source_edition/hash）
    teach 讲解语料 data/teach_docs → teach_rag/     （FIELDS_TEACH，额外含 source_kind/title/quote/quote_loc）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from config import BASE_DIR

TCM_DOCS_DIR = BASE_DIR / "data" / "tcm_docs"
KG_RAG_DIR = BASE_DIR / "kg_rag"
TEACH_DOCS_DIR = BASE_DIR / "data" / "teach_docs"
TEACH_RAG_DIR = BASE_DIR / "teach_rag"
MODEL_DIR = BASE_DIR / "models" / "bge-small-zh"

# 入库保留字段（kg 档案语料）；teach 讲解语料额外带 source_kind（档位）/title/quote/quote_loc
FIELDS = ("herb", "section", "text", "source", "source_edition", "hash")
FIELDS_TEACH = FIELDS + ("source_kind", "title", "quote", "quote_loc")

DIM = 512
MIN_SCORE = 0.45  # top-1 低于此分视为无证据（拒答走知识缺口，不硬答）
BATCH = 32

# 口语问法 → 药典术语 同义词扩展（方剂 keywords 同款思路，通用语言资源非评测特设）。
# bge-small 对口语↔术语（「活血化瘀」↔「活血祛瘀」、「食欲不振」↔「食少」）词面鸿沟理解弱，
# 命中 query 子串时附加术语 query，多向量并集取 max——任一术语命中即提权。
QUERY_EXPANSION = {
    "活血化瘀": ["活血祛瘀"],
    "跌打": ["活血"],
    "有毒": ["大毒"],
    "中毒": ["大毒"],
    "上火": ["清热泻火"],
    "降火": ["清热泻火"],
    "食欲不振": ["食少"],
    "脾胃虚弱": ["脾虚"],
    "消化不良": ["脾虚"],
    "失眠": ["养心安神"],
    "睡不好": ["安神"],
    "咳嗽": ["化痰"],
    "咳痰": ["化痰"],
    "便秘": ["润肠通便"],
    "大便干结": ["润肠通便"],
    "腹泻": ["止泻"],
    "拉肚子": ["止泻"],
    "补血": ["养血"],
    "贫血": ["养血"],
    "眼睛干": ["明目"],
    "感冒": ["解表"],
    "发烧": ["退热"],
    "孕妇": ["妊娠"],
    "遗精": ["固精"],
}


def _expand_query(query: str) -> list[str]:
    """query → 扩展 query 列表（原文 + 命中口语词的术语），去重保序。"""
    qs = [query]
    for word, terms in QUERY_EXPANSION.items():
        if word in query:
            qs.extend(terms)
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out

# ---- 模型懒加载单例（首次 ~10s，常驻后单条查询毫秒级）----
_model = None
_tokenizer = None


def _load_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    try:
        from transformers import AutoModel, AutoTokenizer
        t0 = time.time()
        _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
        _model = AutoModel.from_pretrained(str(MODEL_DIR))
        _model.eval()
        print(f"[retrieval] bge-small-zh 加载完成（{time.time() - t0:.0f}s）", file=sys.stderr)
        return _model, _tokenizer
    except Exception as e:  # 缺模型/库损坏 → fail-soft
        print(f"[retrieval] 模型加载失败，检索服务不可用：{e}", file=sys.stderr)
        return None, None


def _encode(texts: list[str]) -> np.ndarray | None:
    """文本列表 → 归一化向量矩阵（N×512）；失败返回 None。"""
    model, tok = _load_model()
    if model is None:
        return None
    import torch
    vecs = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        inputs = tok(batch, padding=True, truncation=True, max_length=512,
                     return_tensors="pt")
        with torch.no_grad():
            out = model(**inputs)
        v = out.last_hidden_state[:, 0].numpy()  # CLS（bge 官方推荐）
        vecs.append(v)
    mat = np.vstack(vecs).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12
    return mat


def _doc_dir(docs_root: Path | None = None) -> Path:
    """当前版本文档目录（读 MANIFEST.current；docs_root 默认 tcm 语料）。"""
    root = docs_root or TCM_DOCS_DIR
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    return root / manifest["current"] / "docs"


def _fingerprint(docs_root: Path | None = None) -> dict | None:
    """当前文档目录指纹：{dir, source_records_hash}（与 MANIFEST 比对用）。"""
    root = docs_root or TCM_DOCS_DIR
    try:
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
        cur = manifest["current"]
        ver = next(v for v in manifest["versions"] if v["dir"] == cur)
        return {"dir": cur, "source_records_hash": ver["source_records_hash"]}
    except Exception:
        return None


def ingest(force: bool = False, docs_root: Path | None = None,
           rag_dir: Path | None = None, fields: tuple = FIELDS) -> dict:
    """文档目录 → 向量库（幂等：指纹一致且非 force → 跳过）。默认 kg 档案库。"""
    root = docs_root or TCM_DOCS_DIR
    rdir = rag_dir or KG_RAG_DIR
    docs_dir = _doc_dir(root)
    fp = _fingerprint(root)
    index_path = rdir / "index.json"
    if not force and fp and index_path.exists():
        old = json.loads(index_path.read_text(encoding="utf-8"))
        if old.get("built_from") == fp:
            n = len(old["docs"])
            return {"skipped": True, "docs": n}

    # ---- 收集全部文档 ----
    docs = []
    for path in sorted(docs_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for d in payload["docs"]:
            docs.append({k: d.get(k, "") for k in fields})
    if not docs:
        raise SystemExit(f"[FATAL] 文档目录为空：{docs_dir}")

    # ---- 计算 embedding ----
    t0 = time.time()
    texts = [d["text"] for d in docs]
    mat = _encode(texts)
    if mat is None:
        raise SystemExit("[FATAL] 模型不可用，无法入库（检查 models/bge-small-zh 是否完整）")
    print(f"[retrieval] {len(docs)} 条文档 embedding 完成（{time.time() - t0:.0f}s）", file=sys.stderr)

    os.makedirs(rdir, exist_ok=True)
    np.save(rdir / "vectors.npy", mat)
    index = {
        "schema_version": 1,
        "dim": DIM,
        "built_from": fp,
        "docs": docs,
    }
    (rdir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"skipped": False, "docs": len(docs)}


def ingest_teach(force: bool = False) -> dict:
    """讲解语料库 data/teach_docs → teach_rag/（同 ingest，参数化复用）。"""
    return ingest(force=force, docs_root=TEACH_DOCS_DIR, rag_dir=TEACH_RAG_DIR,
                  fields=FIELDS_TEACH)


def _load_index(rag_dir: Path | None = None) -> tuple[list[dict], np.ndarray] | None:
    rdir = rag_dir or KG_RAG_DIR
    index_path = rdir / "index.json"
    vec_path = rdir / "vectors.npy"
    if not (index_path.exists() and vec_path.exists()):
        return None
    index = json.loads(index_path.read_text(encoding="utf-8"))
    mat = np.load(vec_path)
    return index["docs"], mat


def search(query: str, top_k: int = 5, with_score: bool = True,
           rag_dir: Path | None = None) -> list[dict] | None:
    """向量召回：query（经同义词扩展）→ 归一化内积 → top-k（降序）。
    多扩展 query 时对每个文档取最大分（任一术语命中即提权）。
    返回 None 表示检索服务不可用（调用方 fail-soft）。默认 kg 档案库。"""
    qs = _expand_query(query)
    qv = _encode(qs)
    if qv is None:
        return None
    idx = _load_index(rag_dir)
    if idx is None:
        return None
    docs, mat = idx
    scores = (mat @ qv.T).max(axis=1).astype(float)
    order = np.argsort(-scores)[:top_k]
    hits = []
    for i in order:
        d = dict(docs[i])
        d["score"] = round(float(scores[i]), 4)
        if not with_score:
            d.pop("score", None)
        hits.append(d)
    return hits


def search_teach(query: str, top_k: int = 5, with_score: bool = True) -> list[dict] | None:
    """讲解向量召回（同 search，teach_rag 索引）。"""
    return search(query, top_k=top_k, with_score=with_score, rag_dir=TEACH_RAG_DIR)


def format_hits(hits: list[dict]) -> str:
    """命中结果 → 给 LLM 看的字符串（带 source + hash 溯源，与 get_profile 风格一致）。"""
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. 【{h['herb']} · {h['section']}】{h['text']}\n"
            f"   出处：{h['source']}（{h['source_edition']}）；"
            f"相似度 {h['score']}；内容哈希 {h['hash'][:12]}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="自研 numpy 向量检索（bge-small-zh 本地 embedding）")
    ap.add_argument("--ingest", action="store_true", help="从 data/tcm_docs 重建档案向量库")
    ap.add_argument("--teach-ingest", action="store_true", help="从 data/teach_docs 重建讲解向量库")
    ap.add_argument("--force", action="store_true", help="忽略指纹强制重建")
    ap.add_argument("--query", help="检索测试")
    ap.add_argument("--lib", choices=("kg", "teach"), default="kg",
                    help="--query 检索哪个库（kg 档案语料 / teach 讲解语料）")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    if args.ingest:
        r = ingest(force=args.force)
        print(json.dumps(r, ensure_ascii=False))
    elif args.teach_ingest:
        r = ingest_teach(force=args.force)
        print(json.dumps(r, ensure_ascii=False))
    elif args.query:
        if args.lib == "teach":
            hits = search_teach(args.query, top_k=args.top_k)
        else:
            hits = search(args.query, top_k=args.top_k)
        if hits is None:
            print("[FAIL] 检索服务不可用（模型或向量库缺失，先 --ingest / --teach-ingest）")
            sys.exit(1)
        print(format_hits(hits))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
