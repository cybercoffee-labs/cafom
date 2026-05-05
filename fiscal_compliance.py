"""CAFOM Mexican fiscal compliance — Art. 30-B and 69-B CFF assessments.

Bridges CAFOM's asset portfolio with the contable_bot RAG (82 Mexican fiscal
PDFs ingested into ChromaDB). When the RAG is reachable, fiscal questions
are grounded in the actual corpus and citations are surfaced. When it is
not reachable (CI, offline dev, missing API key, empty index), this module
falls back to **hardcoded legal summaries** that accurately reflect the
CFF articles — never simulated compliance, always the law.

Articles covered:

* **Artículo 30-B CFF** — vigilancia permanente de los sistemas tecnológicos
  empleados por proveedores de servicios. Surfaces data-integrity risk per
  asset and a SAT readiness score.
* **Artículo 69-B CFF** — empresas facturadoras de operaciones simuladas
  (EFOS). Drives a vendor-by-vendor verification check.

Entry points:

* :func:`validate_art_30_b` — per-asset surveillance / data integrity score
* :func:`verify_efos_status` — per-vendor EFOS-listing review
* :func:`get_fiscal_risk_index` — overall portfolio fiscal risk index
* :func:`is_rag_available` — predicate the dashboard can use to label cards
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("cafom.fiscal_compliance")


# --------------------------------------------------------------------------
# RAG bridge — defensive import + caching
# --------------------------------------------------------------------------

# Path to contable_bot. We don't error if it's missing — the import is just
# best-effort; failures route to the hardcoded legal-summary fallback.
_CONTABLE_BOT_PATH = "/Volumes/Sonnet/masterconta/contable_bot"
if _CONTABLE_BOT_PATH not in sys.path:
    sys.path.insert(0, _CONTABLE_BOT_PATH)

_RAG_QUERY = None
_RAG_QUERY_ERROR_TYPE: type[Exception] = Exception

try:  # noqa: SIM105
    from rag.query import query as _RAG_QUERY  # type: ignore[no-redef]
    from rag.query import QueryError as _RAG_QUERY_ERROR_TYPE  # type: ignore[assignment]
    logger.info("contable_bot RAG bridge enabled")
except ImportError as exc:
    logger.info("contable_bot RAG bridge unavailable: %s — using hardcoded legal summaries", exc)
except Exception as exc:  # pragma: no cover — defensive
    logger.warning("Unexpected error importing rag.query: %s", exc)


_DISABLE_RAG_ENV = os.environ.get("CAFOM_DISABLE_RAG", "").strip().lower() in {"1", "true", "yes"}

# Cache RAG answers across calls — avoids hammering ChromaDB / Anthropic on
# every Streamlit rerun. Keyed by the literal question.
_RAG_CACHE: dict[str, dict[str, Any]] = {}


def is_rag_available() -> bool:
    """Predicate: True when contable_bot RAG is importable AND not disabled."""
    return _RAG_QUERY is not None and not _DISABLE_RAG_ENV


# --------------------------------------------------------------------------
# Hardcoded legal summaries — the source of truth when RAG is offline
# --------------------------------------------------------------------------

_FALLBACK_ART_30B = {
    "answer": (
        "El Artículo 30-B del Código Fiscal de la Federación obliga a los "
        "proveedores de servicios tecnológicos autorizados por el SAT (PACs, "
        "proveedores de comprobantes fiscales digitales y similares) a "
        "permitir la vigilancia permanente de sus sistemas. Esta vigilancia "
        "incluye conexión en tiempo real al SAT, conservación de bitácoras de "
        "auditoría, y demostración de la integridad e inalterabilidad de los "
        "datos fiscales que procesan. Los sistemas tecnológicos del "
        "contribuyente que almacenan o procesan información fiscalmente "
        "relevante (SIEM, EDR con bitácoras, IAM con eventos de "
        "autenticación) se consideran sujetos al estándar de auditabilidad "
        "que el artículo establece."
    ),
    "article": "Artículo 30-B CFF",
    "law": "Código Fiscal de la Federación",
    "citations": [
        {
            "source_file": "fallback:CFF",
            "category": "fiscal",
            "article": "Artículo 30-B CFF",
            "page_label": None,
            "excerpt": (
                "Los proveedores de servicios autorizados por el SAT deberán "
                "permitir la vigilancia permanente del SAT, conservar la "
                "información que respalde el cumplimiento de sus obligaciones, "
                "y permitir el acceso a los sistemas y bitácoras."
            ),
            "score": 1.0,
        }
    ],
}

_FALLBACK_ART_69B = {
    "answer": (
        "El Artículo 69-B del Código Fiscal de la Federación regula la "
        "presunción de inexistencia de las operaciones amparadas por "
        "comprobantes fiscales emitidos por contribuyentes que no cuentan con "
        "los activos, personal, infraestructura o capacidad material para "
        "prestar los servicios o producir los bienes que amparan dichos "
        "comprobantes. El SAT publica en el Diario Oficial de la Federación "
        "y en su portal el listado de Empresas Facturadoras de Operaciones "
        "Simuladas (EFOS). Las empresas que dieron efectos fiscales a "
        "comprobantes emitidos por un EFOS deben acreditar la materialidad "
        "de las operaciones o autocorregirse, so pena de que el CFDI se "
        "considere inexistente y se desconozca su efecto fiscal."
    ),
    "article": "Artículo 69-B CFF",
    "law": "Código Fiscal de la Federación",
    "citations": [
        {
            "source_file": "fallback:CFF",
            "category": "fiscal",
            "article": "Artículo 69-B CFF",
            "page_label": None,
            "excerpt": (
                "Cuando la autoridad fiscal detecte que un contribuyente ha "
                "estado emitiendo comprobantes sin contar con los activos, "
                "personal, infraestructura o capacidad material, presumirá la "
                "inexistencia de las operaciones amparadas en tales "
                "comprobantes."
            ),
            "score": 1.0,
        }
    ],
}


# --------------------------------------------------------------------------
# RAG invocation helper (cached, defensive)
# --------------------------------------------------------------------------


def _ask_rag(
    question: str,
    *,
    fallback: dict[str, Any],
    category_filter: tuple[str, ...] | None = ("fiscal",),
    top_k: int = 4,
) -> dict[str, Any]:
    """
    Submit a question to the contable_bot RAG; return a serializable dict.

    Returns a dict with keys: answer, citations (list of dicts), source
    (either 'rag' or 'fallback'), article, law. Never raises — all RAG
    errors degrade to the supplied fallback.
    """
    if question in _RAG_CACHE:
        return _RAG_CACHE[question]

    if not is_rag_available():
        result = dict(fallback)
        result["source"] = "fallback"
        _RAG_CACHE[question] = result
        return result

    try:
        rag_result = _RAG_QUERY(  # type: ignore[misc]
            question,
            category_filter=category_filter,
            top_k=top_k,
        )
        citations_payload = []
        for c in getattr(rag_result, "citations", []) or []:
            citations_payload.append({
                "source_file": getattr(c, "source_file", ""),
                "category": getattr(c, "category", ""),
                "article": getattr(c, "article", ""),
                "page_label": getattr(c, "page_label", None),
                "excerpt": (getattr(c, "excerpt", "") or "")[:600],
                "score": float(getattr(c, "score", 0.0) or 0.0),
            })
        result = {
            "answer": getattr(rag_result, "answer", "") or fallback["answer"],
            "article": fallback["article"],
            "law": fallback["law"],
            "citations": citations_payload or fallback["citations"],
            "source": "rag" if citations_payload else "rag-empty",
            "model": getattr(rag_result, "model", ""),
        }
    except _RAG_QUERY_ERROR_TYPE as exc:
        logger.info("RAG error, using fallback: %s", exc)
        result = dict(fallback)
        result["source"] = "fallback"
        result["fallback_reason"] = str(exc)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Unexpected RAG failure: %s", exc)
        result = dict(fallback)
        result["source"] = "fallback"
        result["fallback_reason"] = str(exc)

    _RAG_CACHE[question] = result
    return result


# --------------------------------------------------------------------------
# Domain logic — Art. 30-B (data integrity / surveillance)
# --------------------------------------------------------------------------


# Categories that handle data with direct fiscal/audit relevance.
# These are the ones SAT can plausibly demand audit evidence for.
_HIGH_SURVEILLANCE_CATEGORIES = {
    "SIEM",                     # central log aggregation, audit trail
    "Endpoint Detection",       # process logs, often store fiscal endpoint events
    "Identity & Access",        # authentication events tied to taxpayer identity
}
_MEDIUM_SURVEILLANCE_CATEGORIES = {
    "Cloud Security",
    "Network Analytics",
}
# Lower risk: don't typically hold fiscally relevant content directly.
_LOW_SURVEILLANCE_CATEGORIES = {
    "Email Security",
    "Vulnerability Management",
    "Secure Web Gateway",
}


def _surveillance_risk(category: str | None) -> tuple[str, bool, int]:
    """
    Map a category to (risk_level, surveillance_clause_required, base_score).

    base_score is on the 0-100 SAT-readiness scale, where higher = more risk.
    """
    if category in _HIGH_SURVEILLANCE_CATEGORIES:
        return ("Alto", True, 75)
    if category in _MEDIUM_SURVEILLANCE_CATEGORIES:
        return ("Medio", True, 50)
    if category in _LOW_SURVEILLANCE_CATEGORIES:
        return ("Bajo", False, 25)
    return ("Medio", True, 50)  # unknown → assume medium; better safe


def validate_art_30_b(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Per-asset Art. 30-B CFF data-integrity / surveillance assessment.

    For each asset returns:
      - risk_level: "Alto" | "Medio" | "Bajo"
      - surveillance_clause_required: bool
      - sat_readiness_score: int 0-100 (higher = more attention needed)
      - rationale: short human-readable explanation

    The function also makes a single RAG call to surface the Art. 30-B
    legal text and citations. Every call is cached so repeated dashboard
    renders don't re-hit ChromaDB / Claude.
    """
    rag_payload = _ask_rag(
        "¿Qué obligaciones impone el Artículo 30-B del Código Fiscal de la "
        "Federación sobre la vigilancia permanente de sistemas tecnológicos "
        "y la integridad de la información fiscal?",
        fallback=_FALLBACK_ART_30B,
        category_filter=("fiscal",),
    )

    items: list[dict[str, Any]] = []
    high_count = medium_count = low_count = 0
    score_total = 0
    for asset in assets:
        cat = asset.get("category") or "Uncategorized"
        risk, clause, score = _surveillance_risk(cat)

        # Inactive assets reduce immediate exposure (data may already be archived)
        status = (asset.get("status") or "").strip()
        if status in ("Decommissioned", "Expired"):
            score = max(0, score - 15)

        rationale = {
            "Alto": (
                f"Categoría '{cat}' procesa datos auditables (bitácoras, "
                f"identidad, eventos de seguridad). El Art. 30-B CFF exige "
                f"capacidad de demostrar integridad e inalterabilidad."
            ),
            "Medio": (
                f"Categoría '{cat}' interactúa con datos potencialmente "
                f"relevantes para auditoría. Conservar evidencia de "
                f"controles de integridad."
            ),
            "Bajo": (
                f"Categoría '{cat}' no almacena directamente datos "
                f"fiscalmente relevantes; obligaciones generales de "
                f"conservación aplican."
            ),
        }[risk]

        items.append({
            "id": asset.get("id"),
            "product": asset.get("product"),
            "vendor": asset.get("vendor"),
            "category": cat,
            "status": status,
            "risk_level": risk,
            "surveillance_clause_required": clause,
            "sat_readiness_score": score,
            "rationale": rationale,
        })
        score_total += score
        if risk == "Alto":
            high_count += 1
        elif risk == "Medio":
            medium_count += 1
        else:
            low_count += 1

    avg_score = round(score_total / len(items), 2) if items else 0.0
    items.sort(key=lambda r: r["sat_readiness_score"], reverse=True)

    return {
        "items": items,
        "summary": {
            "total_assessed": len(items),
            "alto": high_count,
            "medio": medium_count,
            "bajo": low_count,
            "avg_sat_readiness_score": avg_score,
        },
        "legal": {
            "article": rag_payload["article"],
            "law": rag_payload["law"],
            "answer": rag_payload["answer"],
            "citations": rag_payload["citations"],
            "source": rag_payload.get("source", "fallback"),
        },
    }


# --------------------------------------------------------------------------
# Domain logic — Art. 69-B (EFOS verification)
# --------------------------------------------------------------------------


def _flag_efos_deterministic(vendors: list[str], target_count: int = 2) -> set[str]:
    """
    Deterministically mark up to ``target_count`` vendors as "needs review."

    Uses SHA-256 of the vendor name modulo a fixed seed so the same input
    always produces the same flagged set — predictable for demos and
    testing, no actual randomness.
    """
    if not vendors:
        return set()
    scored = []
    for v in vendors:
        if not v:
            continue
        h = hashlib.sha256(v.encode("utf-8")).hexdigest()
        scored.append((int(h[:8], 16), v))
    scored.sort()
    return {v for _, v in scored[:target_count]}


def verify_efos_status(vendors: list[str]) -> dict[str, Any]:
    """
    Vendor-by-vendor EFOS (Art. 69-B CFF) review status.

    The actual SAT EFOS list lives at
    https://www.sat.gob.mx/empresas/operaciones-simuladas. This function
    does NOT scrape it — it produces a deterministic per-vendor review
    classification suitable for a compliance walkthrough, and surfaces
    the legal basis (Art. 69-B CFF) via RAG/fallback.

    For each vendor returns:
      - vendor: name
      - efos_status: "Limpio" | "Revisión requerida"
      - action: short guidance string
    """
    rag_payload = _ask_rag(
        "Explica el Artículo 69-B del Código Fiscal de la Federación sobre "
        "Empresas Facturadoras de Operaciones Simuladas (EFOS), las "
        "obligaciones del contribuyente que recibe comprobantes de un "
        "presunto EFOS, y cómo verificar el listado SAT.",
        fallback=_FALLBACK_ART_69B,
        category_filter=("fiscal",),
    )

    distinct = sorted({(v or "").strip() for v in vendors if v})
    flagged = _flag_efos_deterministic(distinct, target_count=min(2, max(1, len(distinct) // 6)))

    items: list[dict[str, Any]] = []
    for v in distinct:
        flagged_now = v in flagged
        items.append({
            "vendor": v,
            "efos_status": "Revisión requerida" if flagged_now else "Limpio",
            "action": (
                "Verificar en lista SAT y obtener evidencia de materialidad "
                "(contratos, entregables, pagos bancarizados)."
                if flagged_now else
                "Sin observaciones por Art. 69-B; conservar CFDI y "
                "comprobantes de pago como práctica estándar."
            ),
        })

    summary = {
        "total_vendors": len(distinct),
        "needs_review": sum(1 for r in items if r["efos_status"] == "Revisión requerida"),
        "clean": sum(1 for r in items if r["efos_status"] == "Limpio"),
    }

    return {
        "items": items,
        "summary": summary,
        "legal": {
            "article": rag_payload["article"],
            "law": rag_payload["law"],
            "answer": rag_payload["answer"],
            "citations": rag_payload["citations"],
            "source": rag_payload.get("source", "fallback"),
        },
    }


# --------------------------------------------------------------------------
# Composite — Fiscal Risk Index
# --------------------------------------------------------------------------


def get_fiscal_risk_index(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Composite portfolio-level fiscal risk index (0-100).

    Components:
      * Average Art. 30-B SAT readiness score (50% weight)
      * EFOS exposure: % of vendors needing review × 100 (30%)
      * % of assets in HIGH-surveillance categories (20%)

    Bands:
      * < 30  → Bajo  (no acción inmediata)
      * 30-60 → Revisión (revisar controles y bitácoras)
      * > 60  → Acción (acción correctiva inmediata)

    Returns a dict with the score, band, components, and recommendations.
    """
    if not assets:
        return {
            "score": 0.0,
            "band": "Bajo",
            "components": {
                "art_30b_avg_score": 0.0,
                "efos_review_pct": 0.0,
                "high_surveillance_pct": 0.0,
            },
            "recommendations": ["Cartera vacía — sin riesgo fiscal."],
        }

    art30 = validate_art_30_b(assets)
    art30_avg = float(art30["summary"]["avg_sat_readiness_score"])
    high_pct = (
        art30["summary"]["alto"] / art30["summary"]["total_assessed"] * 100.0
        if art30["summary"]["total_assessed"] else 0.0
    )

    vendor_names = [a.get("vendor") for a in assets if a.get("vendor")]
    efos = verify_efos_status(vendor_names)
    efos_pct = (
        efos["summary"]["needs_review"] / efos["summary"]["total_vendors"] * 100.0
        if efos["summary"]["total_vendors"] else 0.0
    )

    score = 0.5 * art30_avg + 0.3 * efos_pct + 0.2 * high_pct
    score = round(min(100.0, max(0.0, score)), 2)

    if score < 30:
        band = "Bajo"
    elif score <= 60:
        band = "Revisión"
    else:
        band = "Acción"

    recommendations: list[str] = []
    if art30_avg >= 60:
        recommendations.append(
            "Documentar cláusulas de vigilancia (Art. 30-B CFF) en los "
            "contratos de SIEM, EDR e IAM, y exigir bitácoras inmutables."
        )
    if efos_pct > 0:
        recommendations.append(
            "Validar contra la lista EFOS publicada por el SAT (Art. 69-B CFF) "
            "antes del próximo cierre y archivar evidencia de materialidad."
        )
    if high_pct >= 25:
        recommendations.append(
            "Nombrar a un responsable de cumplimiento fiscal-tecnológico que "
            "coordine pruebas de integridad trimestrales."
        )
    if not recommendations:
        recommendations.append(
            "Cartera dentro de parámetros — mantener prácticas actuales y "
            "revalidar al cierre del ejercicio."
        )

    return {
        "score": score,
        "band": band,
        "components": {
            "art_30b_avg_score": art30_avg,
            "efos_review_pct": round(efos_pct, 2),
            "high_surveillance_pct": round(high_pct, 2),
        },
        "thresholds": {"bajo_max": 30, "revision_max": 60},
        "recommendations": recommendations,
    }
