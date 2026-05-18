"""Face-embedding identity gate for generated wedding images.

This gate is intentionally separate from vision-LLM QA. A beautiful image is
not deliverable when the generated face embedding does not match the uploaded
real person closely enough.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import httpx

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

from app.core.config import get_settings
from app.services.qa_rules import build_structured_qa_issues

logger = logging.getLogger(__name__)
settings = get_settings()


class IdentityEmbeddingUnavailable(RuntimeError):
    """Raised when the embedding model cannot run in the current runtime."""


@dataclass(frozen=True, slots=True)
class FaceEmbedding:
    embedding: Any
    bbox: tuple[float, float, float, float]
    det_score: float

    @property
    def area(self) -> float:
        left, top, right, bottom = self.bbox
        return max(0.0, right - left) * max(0.0, bottom - top)


def _cosine_similarity(a: Any, b: Any) -> float:
    if np is not None:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom <= 0:
            return 0.0
        return float(np.dot(va, vb) / denom)
    va = [float(value) for value in a]
    vb = [float(value) for value in b]
    dot = sum(x * y for x, y in zip(va, vb))
    norm_a = sum(x * x for x in va) ** 0.5
    norm_b = sum(y * y for y in vb) ** 0.5
    denom = norm_a * norm_b
    if denom <= 0:
        return 0.0
    return float(dot / denom)


class IdentityEmbeddingService:
    """Run ArcFace/InsightFace identity verification for delivery QA."""

    def __init__(self) -> None:
        self._app: Any | None = None
        self._init_error: str = ""

    def _get_app(self) -> Any:
        if self._app is not None:
            return self._app
        if self._init_error:
            raise IdentityEmbeddingUnavailable(self._init_error)
        if np is None or Image is None or ImageOps is None:
            self._init_error = "identity_embedding_dependencies_unavailable"
            raise IdentityEmbeddingUnavailable(self._init_error)
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name=settings.qa_identity_embedding_model_name)
            det_size = max(320, int(settings.qa_identity_embedding_det_size or 640))
            app.prepare(ctx_id=int(settings.qa_identity_embedding_ctx_id), det_size=(det_size, det_size))
            self._app = app
            return app
        except Exception as exc:  # pragma: no cover - depends on runtime model files
            self._init_error = f"identity_embedding_init_error:{type(exc).__name__}"
            logger.warning("Identity embedding model unavailable: %s", exc)
            raise IdentityEmbeddingUnavailable(self._init_error) from exc

    async def _fetch_image_bytes(self, image_url: str) -> bytes:
        raw = str(image_url or "").strip()
        if not raw:
            raise ValueError("identity_embedding_empty_image_url")
        if raw.startswith("data:image/") and ";base64," in raw:
            return base64.b64decode(raw.split(",", 1)[1])
        if not raw.startswith(("http://", "https://")):
            raise ValueError("identity_embedding_requires_remote_or_data_url")
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(raw)
            response.raise_for_status()
            return response.content

    def _detect_faces_from_bytes(self, content: bytes) -> list[FaceEmbedding]:
        app = self._get_app()
        if Image is None or ImageOps is None or np is None:
            raise IdentityEmbeddingUnavailable("identity_embedding_dependencies_unavailable")
        with Image.open(BytesIO(content)) as image:
            rgb = ImageOps.exif_transpose(image).convert("RGB")
            # InsightFace expects BGR ndarray.
            bgr = np.asarray(rgb)[:, :, ::-1]
            detected = app.get(bgr)
        faces: list[FaceEmbedding] = []
        for face in detected or []:
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = getattr(face, "embedding", None)
            bbox_raw = getattr(face, "bbox", None)
            if embedding is None or bbox_raw is None:
                continue
            bbox_values = [float(value) for value in list(bbox_raw)[:4]]
            if len(bbox_values) != 4:
                continue
            score = float(getattr(face, "det_score", 0.0) or 0.0)
            faces.append(FaceEmbedding(embedding=embedding, bbox=tuple(bbox_values), det_score=score))
        return sorted(faces, key=lambda item: (item.area, item.det_score), reverse=True)

    async def _faces_for_url(self, image_url: str) -> list[FaceEmbedding]:
        content = await self._fetch_image_bytes(image_url)
        return self._detect_faces_from_bytes(content)

    async def verify_identity_similarity(
        self,
        image_url: str,
        *,
        source_image_urls: list[str],
        is_couple: bool = False,
    ) -> dict[str, Any]:
        sources = [str(url).strip() for url in (source_image_urls or []) if str(url or "").strip()]
        if not sources:
            return {
                "passed": True,
                "reasons": [],
                "issues": [],
                "metrics": {},
                "notes": "no_source_images",
                "source": "identity_embedding",
            }

        expected_count = 2 if is_couple else 1
        reasons: list[str] = []
        metrics: dict[str, float] = {}

        try:
            source_faces: list[FaceEmbedding] = []
            for index, source_url in enumerate(sources[:expected_count], start=1):
                faces = await self._faces_for_url(source_url)
                metrics[f"source_{index}_face_count"] = float(len(faces))
                if not faces:
                    reasons.append("identity_face_missing")
                    continue
                source_faces.append(faces[0])

            generated_faces = await self._faces_for_url(image_url)
            metrics["generated_face_count"] = float(len(generated_faces))
            if len(source_faces) < expected_count or len(generated_faces) < expected_count:
                reasons.append("identity_face_missing")
                return self._verdict(reasons, metrics, "face_missing")

            if expected_count == 1:
                return self._single_subject_verdict(source_faces[0], generated_faces, metrics)
            return self._couple_verdict(source_faces[:2], generated_faces[:4], metrics)
        except IdentityEmbeddingUnavailable as exc:
            reasons = ["identity_embedding_unavailable"]
            return self._verdict(reasons, metrics, str(exc)[:160])

    def _single_subject_verdict(
        self,
        source_face: FaceEmbedding,
        generated_faces: list[FaceEmbedding],
        metrics: dict[str, float],
    ) -> dict[str, Any]:
        threshold = float(settings.qa_identity_similarity_single_threshold or 0.55)
        scores = [_cosine_similarity(source_face.embedding, face.embedding) for face in generated_faces[:3]]
        best = max(scores) if scores else 0.0
        metrics["identity_similarity"] = round(best, 4)
        metrics["identity_similarity_threshold"] = threshold
        reasons = ["identity_similarity_low"] if best < threshold else []
        if len(generated_faces) >= 2:
            primary_area = max(1.0, float(generated_faces[0].area))
            secondary_area = float(generated_faces[1].area)
            secondary_ratio = secondary_area / primary_area
            metrics["generated_secondary_face_area_ratio"] = round(secondary_ratio, 4)
            metrics["generated_secondary_face_score"] = round(float(generated_faces[1].det_score), 4)
            if secondary_ratio >= 0.18 and float(generated_faces[1].det_score) >= 0.45:
                reasons.append("unexpected_extra_subject")
        return self._verdict(reasons, metrics, f"single_similarity={best:.3f}")

    def _couple_verdict(
        self,
        source_faces: list[FaceEmbedding],
        generated_faces: list[FaceEmbedding],
        metrics: dict[str, float],
    ) -> dict[str, Any]:
        threshold = float(settings.qa_identity_similarity_couple_threshold or 0.50)
        margin_threshold = float(settings.qa_identity_similarity_margin_threshold or 0.08)
        generated_similarity_max = float(settings.qa_identity_generated_face_similarity_max or 0.82)
        reasons: list[str] = []
        best_indices: list[int] = []

        for source_index, source_face in enumerate(source_faces, start=1):
            scores = [_cosine_similarity(source_face.embedding, face.embedding) for face in generated_faces]
            ranked = sorted(scores, reverse=True)
            best = ranked[0] if ranked else 0.0
            second = ranked[1] if len(ranked) > 1 else 0.0
            best_idx = scores.index(best) if scores else -1
            best_indices.append(best_idx)
            metrics[f"identity_{source_index}_similarity"] = round(best, 4)
            metrics[f"identity_{source_index}_match_margin"] = round(best - second, 4)
            if best < threshold:
                reasons.append("identity_similarity_low")
            if best - second < margin_threshold:
                reasons.append("identity_margin_low")

        metrics["identity_similarity_threshold"] = threshold
        metrics["identity_margin_threshold"] = margin_threshold

        if len(set(best_indices)) < len(best_indices):
            reasons.append("identity_averaging")
        if len(generated_faces) >= 2:
            generated_pair_similarity = _cosine_similarity(generated_faces[0].embedding, generated_faces[1].embedding)
            source_pair_similarity = _cosine_similarity(source_faces[0].embedding, source_faces[1].embedding)
            metrics["generated_pair_face_similarity"] = round(generated_pair_similarity, 4)
            metrics["source_pair_face_similarity"] = round(source_pair_similarity, 4)
            if generated_pair_similarity > generated_similarity_max and generated_pair_similarity > source_pair_similarity + 0.12:
                reasons.append("identity_averaging")

        return self._verdict(reasons, metrics, "couple_similarity_checked")

    def _verdict(self, reasons: list[str], metrics: dict[str, float], notes: str) -> dict[str, Any]:
        ordered_reasons: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            if reason not in seen:
                seen.add(reason)
                ordered_reasons.append(reason)
        return {
            "passed": not ordered_reasons,
            "reasons": ordered_reasons,
            "issues": build_structured_qa_issues(
                ordered_reasons,
                source="identity_embedding",
                notes=notes,
                metrics=metrics,
            ),
            "metrics": metrics,
            "notes": notes,
            "source": "identity_embedding",
        }


identity_embedding_service = IdentityEmbeddingService()
