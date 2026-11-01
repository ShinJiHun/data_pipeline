"""
페르소나 코퍼스 공통 유틸 - 청킹, 임베딩, DB 저장

블로그 크롤러(crawl_blog.py)와 PDF 로더(crawl_pdf.py)가 공유한다.
DB 접속은 프로젝트 공통 모듈(common.database)을 재사용한다.
"""

import sys
import hashlib
import numpy as np
from sqlalchemy import text

sys.path.insert(0, "/mnt/200gb/project/data_pipeline")
from common.database import SessionLocal  # noqa: E402

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

_model = None  # lazy-load, 한 프로세스에서 한 번만 로딩


def get_embedding_model():
    global _model
    if _model is None:
        print("🧠 임베딩 모델 로딩 중 (bge-m3)...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        print(f"✅ 모델 로딩 완료 (차원: {_model.get_sentence_embedding_dimension()})")
    return _model


def chunk_text(text_content, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    if not text_content:
        return []

    chunks = []
    start = 0
    n = len(text_content)

    while start < n:
        end = min(start + chunk_size, n)
        chunk = text_content[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == n:
            break
        start = end - overlap

    return chunks


def content_hash_of(text_content):
    return hashlib.sha256(text_content.encode("utf-8")).hexdigest()


def get_existing_document(db, source_path):
    return db.execute(
        text("SELECT id, content_hash FROM persona_document WHERE source_path = :p"),
        {"p": source_path},
    ).first()


def delete_document_and_chunks(db, document_id):
    db.execute(text("DELETE FROM persona_chunk WHERE document_id = :id"), {"id": document_id})
    db.execute(text("DELETE FROM persona_document WHERE id = :id"), {"id": document_id})


def insert_document(db, source_type, source_path, title, content_hash):
    result = db.execute(
        text(
            "INSERT INTO persona_document (source_type, source_path, title, content_hash, ingested_at) "
            "VALUES (:source_type, :source_path, :title, :content_hash, NOW())"
        ),
        {
            "source_type": source_type,
            "source_path": source_path,
            "title": title,
            "content_hash": content_hash,
        },
    )
    return result.lastrowid


def insert_chunk(db, document_id, chunk_index, content, embedding_vec):
    """
    embedding은 numpy float32 배열을 그대로 바이트로 직렬화해서 저장한다.
    나중에 검색 시엔 반드시 동일하게 np.frombuffer(blob, dtype=np.float32)로 복원해야 한다.
    """
    embedding_bytes = np.asarray(embedding_vec, dtype=np.float32).tobytes()
    db.execute(
        text(
            "INSERT INTO persona_chunk (document_id, chunk_index, content, embedding, embedding_dim, created_at) "
            "VALUES (:document_id, :chunk_index, :content, :embedding, :embedding_dim, NOW())"
        ),
        {
            "document_id": document_id,
            "chunk_index": chunk_index,
            "content": content,
            "embedding": embedding_bytes,
            "embedding_dim": len(embedding_vec),
        },
    )


def ingest_document(db, source_type, source_path, title, content, stats):
    """
    문서 하나를 통째로 처리: 변경 감지 → (필요시) 재수집 → 청킹 → 임베딩 → 저장
    stats 딕셔너리(new/updated/skipped 카운터)를 직접 갱신한다.
    성공 시 True, 스킵/실패 시 False 반환.
    """
    if not content or len(content) < 50:
        print("  ⏭️ 본문 없음/너무 짧음 - 스킵")
        stats["skipped"] += 1
        return False

    chash = content_hash_of(content)
    existing = get_existing_document(db, source_path)

    if existing and existing.content_hash == chash:
        print("  ⏭️ 변경 없음 - 스킵")
        stats["skipped"] += 1
        return False

    if existing:
        print("  🔄 내용 변경됨 - 기존 청크 삭제 후 재수집")
        delete_document_and_chunks(db, existing.id)
        stats["updated"] += 1
    else:
        stats["new"] += 1

    document_id = insert_document(db, source_type, source_path, title, chash)

    chunks = chunk_text(content)
    if not chunks:
        print("  ⚠️ 청크 생성 실패")
        db.commit()
        return False

    model = get_embedding_model()
    embeddings = model.encode(chunks, normalize_embeddings=True, show_progress_bar=False)

    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        insert_chunk(db, document_id, idx, chunk, emb)

    db.commit()
    print(f"  ✅ {title} → {len(chunks)}개 청크 저장")
    return True
