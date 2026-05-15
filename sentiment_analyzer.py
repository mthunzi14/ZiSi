"""
sentiment_analyzer.py - ZiSi Bot AI Sentiment Analysis
Dual-mode: Claude API when ANTHROPIC_API_KEY is set, keyword fallback otherwise.
Switches automatically — no code changes needed when key is added.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from config import load_config

log = logging.getLogger("zisi.sentiment")

_MODEL = "claude-sonnet-4-6"
# Use Haiku for batch analysis (20 articles per call) — 10x cheaper than Sonnet,
# still far superior to keyword analysis for crypto sentiment.
_BATCH_MODEL = "claude-haiku-4-5"

# Logged once per process to make the active mode visible in console output
_mode_logged = False

# Lazy-initialised Claude client (only created when API key is present)
_client = None

# Lazy-initialised Gemini client (free tier: 1,500 calls/day, 15 req/min)
_gemini_client = None
_GEMINI_MODEL = "gemini-2.5-flash-lite"

# Lazy-initialised Groq client (free tier: 14,400 req/day)
_groq_client = None
_GROQ_MODEL = "llama-3.3-70b-versatile"
# Set to True after a 401 so we stop retrying this process session
_groq_auth_failed: bool = False

# Lazy-loaded local FinBERT classifier (financial-domain BERT — replaces DistilBERT)
# FinBERT is trained on SEC filings + Financial PhraseBank → accurate for crypto news.
# DistilBERT was trained on movie reviews (SST-2) → wrong domain, wrong labels.
_local_classifier = None
_LOCAL_MODEL_NAME = "ProsusAI/finbert"

# ── Keyword lists ─────────────────────────────────────────────────────────────

_BULLISH_KEYWORDS = [
    "surge", "rally", "institutional", "adoption", "bull", "partnership",
    "approval", "positive", "gains", "breakout", "green", "soar",
    "pump", "resistance", "whale", "fund flow", "rise", "up",
    "record", "all-time high", "ath", "recovery", "rebound", "moon",
    "inflow", "accumulation", "optimistic", "bullish", "support",
]

_BEARISH_KEYWORDS = [
    "crash", "collapse", "ban", "regulation", "crackdown", "downturn",
    "negative", "selling", "exodus", "red", "down", "dump", "plunge",
    "loss", "decline", "fear", "bear", "liquidation", "risk", "hack",
    "exploit", "vulnerability", "lawsuit", "fine", "penalty", "outflow",
    "capitulation", "correction", "sell-off", "selloff", "bearish",
]

# Crypto name → canonical identifier
_CRYPTO_DETECTION = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana",
    "ripple": "ripple", "xrp": "ripple",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "cardano": "cardano", "ada": "cardano",
    "polygon": "polygon", "matic": "polygon",
    "avalanche": "avalanche", "avax": "avalanche",
    "chainlink": "chainlink", "link": "chainlink",
    "polkadot": "polkadot", "dot": "polkadot",
}

_CLAUDE_PROMPT = """Analyze this cryptocurrency news for sentiment.

HEADLINE: {headline}
DESCRIPTION: {description}
CONTENT: {content}

You are a crypto market sentiment expert. Analyze this news and respond with JSON only (no other text):

{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "confidence": 1-10,
  "reasoning": "brief explanation",
  "affected_cryptos": ["bitcoin", "ethereum", ...],
  "market_impact": "HIGH" | "MEDIUM" | "LOW"
}}

Guidelines:
- "bullish": News likely to increase crypto prices/demand
- "bearish": News likely to decrease prices/demand
- "neutral": No clear market impact
- confidence: Your confidence in this assessment (1-10)
- affected_cryptos: Which cryptos does this impact?
- market_impact: How significant is this news?

Be concise and analytical. Respond ONLY with JSON."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_claude_available() -> bool:
    """Return True when ANTHROPIC_API_KEY is present and non-empty."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    return bool(key and key.strip())


def _is_gemini_available() -> bool:
    """Return True when GEMINI_API_KEY is set and either Gemini SDK is installed."""
    key = os.getenv("GEMINI_API_KEY", "")
    if not (key and key.strip()):
        return False
    try:
        import google.genai  # noqa: F401  # preferred new SDK
        return True
    except ImportError:
        pass
    try:
        import google.generativeai  # noqa: F401  # deprecated fallback
        return True
    except ImportError:
        return False


def _is_groq_available() -> bool:
    """Return True when GROQ_API_KEY is set, groq is installed, and no 401 was seen."""
    if _groq_auth_failed:
        return False
    key = os.getenv("GROQ_API_KEY", "")
    if not (key and key.strip()):
        return False
    try:
        import groq  # noqa: F401
        return True
    except ImportError:
        return False


def _local_model_available() -> bool:
    """Return True if transformers and torch are installed."""
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _get_gemini_client():
    """
    Lazy-load the Gemini client.
    Tries new google.genai SDK first (supports gemini-1.5-flash via v1 API),
    then falls back to deprecated google.generativeai (v1beta only).
    Returns a wrapper dict so callers don't need to know which SDK is active.
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    api_key = os.getenv("GEMINI_API_KEY", "")

    # ── Attempt 1: new google-genai SDK ──────────────────────────────────────
    # Force api_version="v1" — the default v1beta does NOT expose gemini-1.5-flash.
    try:
        import google.genai as genai
        try:
            # google-genai >= 1.0 supports HttpOptions
            from google.genai import types as _genai_types
            _http_opts = _genai_types.HttpOptions(api_version="v1")
            client = genai.Client(api_key=api_key, http_options=_http_opts)
        except (ImportError, AttributeError, Exception):
            # Older SDK or HttpOptions not available — try keyword arg directly
            try:
                client = genai.Client(api_key=api_key, http_options={"api_version": "v1"})
            except TypeError:
                client = genai.Client(api_key=api_key)  # last resort; may still hit v1beta
        _gemini_client = {"sdk": "new", "client": client}
        log.info("Gemini client initialised via google.genai (v1 API): %s", _GEMINI_MODEL)
        return _gemini_client
    except ImportError:
        pass  # SDK not installed — try deprecated
    except Exception as exc:
        log.warning("Gemini (google.genai) init failed: %s", exc)

    # ── Attempt 2: deprecated google-generativeai SDK ─────────────────────────
    try:
        import google.generativeai as genai
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(_GEMINI_MODEL)
        _gemini_client = {"sdk": "old", "client": model}
        log.info("Gemini client initialised via google.generativeai: %s", _GEMINI_MODEL)
        return _gemini_client
    except Exception as exc:
        log.warning("Gemini init failed (both SDKs): %s", exc)
        return None


def _get_groq_client():
    """Lazy-load the Groq client. Returns None on failure."""
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    try:
        from groq import Groq
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
        log.info("Groq client initialised: %s", _GROQ_MODEL)
        return _groq_client
    except Exception as exc:
        log.warning("Groq init failed: %s", exc)
        return None


def _is_finbert_cached() -> bool:
    """
    Return True only if FinBERT weights are already in the local HuggingFace cache.
    Never triggers a download — that would block the cycle for 3+ minutes on a
    438 MB model.  If the model is not cached, the bot falls through to keyword
    analysis rather than waiting for a slow internet download.
    """
    import os
    cache_root = os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface", "hub",
        "models--ProsusAI--finbert",
    )
    if not os.path.isdir(cache_root):
        return False
    # A valid cache will have a 'snapshots' sub-directory with the actual weights
    snapshots = os.path.join(cache_root, "snapshots")
    if not os.path.isdir(snapshots):
        return False
    # At least one snapshot directory must contain pytorch_model.bin or model.safetensors
    for snap in os.listdir(snapshots):
        snap_path = os.path.join(snapshots, snap)
        if os.path.isdir(snap_path):
            for weight_file in ("pytorch_model.bin", "model.safetensors"):
                if os.path.isfile(os.path.join(snap_path, weight_file)):
                    return True
    return False


def _get_local_classifier():
    """Lazy-load the FinBERT sentiment classifier. Returns None if unavailable.

    FinBERT (ProsusAI/finbert) is fine-tuned on financial news (SEC filings +
    Financial PhraseBank) — correct domain for crypto trading.  Labels returned
    are lowercase: 'positive', 'negative', 'neutral'.

    IMPORTANT: Never auto-downloads the model (438 MB).  If not already cached,
    returns None immediately so the keyword fallback is used instead.
    To pre-download FinBERT run once manually:
      python -c "from transformers import pipeline; pipeline('sentiment-analysis', model='ProsusAI/finbert')"
    """
    global _local_classifier
    if _local_classifier is not None:
        return _local_classifier
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        log.warning("transformers/torch not installed — pip install transformers torch")
        return None

    # Guard: only load if already cached — never trigger a 438 MB download mid-cycle
    if not _is_finbert_cached():
        log.warning(
            "[FINBERT] Model not cached — skipping to keyword fallback. "
            "Pre-download with: python -c \"from transformers import pipeline; "
            "pipeline('sentiment-analysis', model='ProsusAI/finbert')\""
        )
        return None

    try:
        from transformers import pipeline
        device = 0 if torch.cuda.is_available() else -1
        _local_classifier = pipeline(
            "sentiment-analysis",
            model=_LOCAL_MODEL_NAME,
            device=device,
        )
        log.info("Local sentiment model loaded: %s (financial domain)", _LOCAL_MODEL_NAME)
        return _local_classifier
    except Exception as exc:
        log.warning("Local model load failed (%s) — falling back to keywords", exc)
        return None


def _log_mode_once() -> None:
    """Emit the active mode once so it shows up clearly in startup logs."""
    global _mode_logged
    if _mode_logged:
        return
    _mode_logged = True
    if _is_claude_available():
        log.info("Sentiment mode: Claude API (%s) [PRIORITY 1]", _MODEL)
    elif _is_gemini_available():
        log.info("Sentiment mode: Gemini Flash (%s) free tier [PRIORITY 2]", _GEMINI_MODEL)
    elif _is_groq_available():
        log.info("Sentiment mode: Groq Llama 3.3 70B (%s) free tier [PRIORITY 3]", _GROQ_MODEL)
    elif _local_model_available():
        log.info("Sentiment mode: FinBERT LOCAL (%s) | Cost: $0 [PRIORITY 4]", _LOCAL_MODEL_NAME)
    else:
        log.info("Sentiment mode: KEYWORD FALLBACK — set GEMINI_API_KEY or GROQ_API_KEY for AI analysis")


def _get_client():
    """Lazy-load the Anthropic client (only called when key is present)."""
    global _client
    if _client is not None:
        return _client
    try:
        import anthropic  # lazy import — keeps module loadable without the package
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        _client = anthropic.Anthropic(api_key=api_key)
        return _client
    except ImportError:
        log.error("anthropic package not installed — run: pip install anthropic")
        raise


def _detect_cryptos(text: str) -> list[str]:
    """
    Return deduplicated list of canonical crypto identifiers found in text.
    Defaults to ['bitcoin', 'ethereum'] if nothing detected.
    """
    found = set()
    text_lower = text.lower()
    for token, canonical in _CRYPTO_DETECTION.items():
        if re.search(r"\b" + re.escape(token) + r"\b", text_lower):
            found.add(canonical)
    return sorted(found) if found else ["bitcoin", "ethereum"]


def _impact_from_confidence(confidence: int) -> str:
    if confidence >= 7:
        return "HIGH"
    if confidence >= 5:
        return "MEDIUM"
    return "LOW"


# ── Keyword-based analysis ────────────────────────────────────────────────────

def analyze_sentiment_with_keywords(
    headline: str,
    description: str,
    content: str,
) -> dict:
    """
    Keyword-based sentiment analysis — no API key required.

    Scoring: confidence = min(10, 5 + keyword_count_for_winning_side)
    Tied counts → neutral with confidence 3.
    """
    full_text = " ".join([
        headline or "",
        description or "",
        (content or "")[:1500],
    ]).lower()

    bullish_count = sum(1 for kw in _BULLISH_KEYWORDS if kw in full_text)
    bearish_count = sum(1 for kw in _BEARISH_KEYWORDS if kw in full_text)

    if bullish_count > bearish_count:
        sentiment = "bullish"
        confidence = min(10, 5 + bullish_count)
        reasoning = f"{bullish_count} bullish keyword(s) vs {bearish_count} bearish"
    elif bearish_count > bullish_count:
        sentiment = "bearish"
        confidence = min(10, 5 + bearish_count)
        reasoning = f"{bearish_count} bearish keyword(s) vs {bullish_count} bullish"
    else:
        sentiment = "neutral"
        confidence = 3
        reasoning = f"No clear signal ({bullish_count} bullish, {bearish_count} bearish)"

    affected = _detect_cryptos(full_text)

    log.debug(
        "  [KW] %s → %s (%d/10) | +%d/-%d keywords",
        (headline or "")[:60], sentiment.upper(), confidence, bullish_count, bearish_count,
    )

    return {
        "headline": headline,
        "sentiment": sentiment,
        "confidence": int(confidence),
        "reasoning": reasoning,
        "affected_cryptos": affected,
        "market_impact": _impact_from_confidence(confidence),
        "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        "model_used": "keyword_fallback",
    }


# ── Local model (FinBERT) ────────────────────────────────────────────────────

def analyze_with_local_model(articles: list[dict]) -> list[dict]:
    """
    Analyze articles with FinBERT running locally (financial-domain BERT).

    FinBERT labels: 'positive' / 'negative' / 'neutral' (lowercase).
    Free alternative to API — ~97% accuracy on financial news.
    Falls back to keyword analysis if transformers/torch not installed.
    """
    classifier = _get_local_classifier()
    if classifier is None:
        results = []
        for art in articles:
            r = analyze_sentiment_with_keywords(
                art.get("title", ""), art.get("description", ""), art.get("content", "")
            )
            r.setdefault("source", art.get("source", ""))
            results.append(r)
        return results

    log.info("[SENTIMENT] FinBERT: analyzing %d articles | Cost: $0", len(articles))
    results = []

    for i, art in enumerate(articles):
        try:
            title = art.get("title", "")
            description = art.get("description", "") or ""
            text = f"{title}. {description}"[:512]

            raw = classifier(text)[0]
            # FinBERT returns lowercase labels: 'positive', 'negative', 'neutral'
            label = raw["label"].lower()
            score = raw["score"]
            confidence = max(1, min(10, int(score * 10)))

            if label == "positive":
                sentiment = "bullish"
            elif label == "negative":
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            # FinBERT 'neutral' label is reliable — don't override it
            # Only suppress very low-confidence directional calls
            if label in ("positive", "negative") and confidence < 5:
                sentiment = "neutral"

            affected = _detect_cryptos(f"{title} {description}")

            results.append({
                "headline": title,
                "sentiment": sentiment,
                "confidence": confidence,
                "reasoning": f"FinBERT: {label} ({score:.1%})",
                "affected_cryptos": affected,
                "market_impact": _impact_from_confidence(confidence),
                "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                "model_used": _LOCAL_MODEL_NAME,
                "source": art.get("source", ""),
            })

            if (i + 1) % 5 == 0:
                log.debug("[SENTIMENT] Processed %d/%d articles (FinBERT)", i + 1, len(articles))

        except Exception as exc:
            log.warning("[SENTIMENT] FinBERT error on article %d: %s", i + 1, exc)
            r = analyze_sentiment_with_keywords(
                art.get("title", ""), art.get("description", ""), art.get("content", "")
            )
            r.setdefault("source", art.get("source", ""))
            results.append(r)

    log.info(
        "[SENTIMENT] FinBERT analysis complete: %d/%d articles | Cost: $0",
        len(results), len(articles),
    )
    return results


# ── Gemini Flash batch analysis ───────────────────────────────────────────────

def analyze_articles_with_gemini(articles: list[dict]) -> list[dict]:
    """
    Analyze ALL articles using Gemini Flash, chunked in batches of 20.
    Free tier: 1,500 calls/day, 15 req/min — ample for ZiSi (≈4 calls/hour).
    Supports both google.genai (new) and google.generativeai (deprecated) SDKs.
    Returns same dict shape as all other analysis functions.
    """
    wrapper = _get_gemini_client()
    if wrapper is None:
        return []

    _CHUNK = 20  # Gemini context-safe batch size (mirrors Groq)
    all_results: list[dict] = []

    for chunk_start in range(0, len(articles), _CHUNK):
        chunk = articles[chunk_start: chunk_start + _CHUNK]

        articles_text = "\n\n".join(
            f"Article {i+1}: {art.get('title', '')}. {(art.get('description') or '')[:200]}"
            for i, art in enumerate(chunk)
        )
        prompt = _BATCH_PROMPT.format(articles_text=articles_text)

        try:
            sdk = wrapper.get("sdk", "old")
            client = wrapper["client"]

            if sdk == "new":
                response = client.models.generate_content(
                    model=_GEMINI_MODEL, contents=prompt
                )
                raw_text = response.text.strip()
            else:
                response = client.generate_content(prompt)
                raw_text = response.text.strip()

            # Strip markdown fences
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            batch_results = json.loads(raw_text)
            if not isinstance(batch_results, list):
                raise ValueError("Expected JSON array from Gemini")

            for art, res in zip(chunk, batch_results):
                affected = [c.lower() for c in res.get("affected_cryptos", [])]
                if not affected:
                    affected = _detect_cryptos(
                        f"{art.get('title', '')} {art.get('description', '')}"
                    )
                conf = int(res.get("confidence", 5))
                all_results.append({
                    "headline": art.get("title", ""),
                    "sentiment": str(res.get("sentiment", "neutral")).lower(),
                    "confidence": conf,
                    "reasoning": res.get("reasoning", ""),
                    "affected_cryptos": affected,
                    "market_impact": _impact_from_confidence(conf),
                    "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                    "model_used": _GEMINI_MODEL,
                    "source": art.get("source", ""),
                })

        except json.JSONDecodeError as exc:
            log.warning(
                "[SENTIMENT-BATCH] Gemini chunk %d-%d returned non-JSON — skipping: %s",
                chunk_start + 1, chunk_start + len(chunk), exc,
            )
        except Exception as exc:
            log.warning(
                "[SENTIMENT-BATCH] Gemini chunk %d-%d failed — will try fallback: %s",
                chunk_start + 1, chunk_start + len(chunk), exc,
            )

    log.info(
        "[SENTIMENT-BATCH] Gemini Flash analyzed %d/%d articles | Cost: $0",
        len(all_results), len(articles),
    )
    return all_results


# ── Groq batch analysis ───────────────────────────────────────────────────────

def analyze_articles_with_groq(articles: list[dict]) -> list[dict]:
    """
    Analyze articles in batches of 20 using Groq + Llama 3.3 70B.
    Free tier: 14,400 req/day — very fast inference.
    Handles >20 articles by splitting into multiple API calls.
    Returns same dict shape as all other analysis functions.
    """
    client = _get_groq_client()
    if client is None:
        return []

    _CHUNK = 20  # Groq context-safe batch size
    all_results: list[dict] = []

    for chunk_start in range(0, len(articles), _CHUNK):
        chunk = articles[chunk_start: chunk_start + _CHUNK]

        articles_text = "\n\n".join(
            f"Article {i+1}: {art.get('title', '')}. {(art.get('description') or '')[:200]}"
            for i, art in enumerate(chunk)
        )
        prompt = _BATCH_PROMPT.format(articles_text=articles_text)

        try:
            completion = client.chat.completions.create(
                model=_GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
                temperature=0.1,
            )
            raw_text = completion.choices[0].message.content.strip()

            # Strip markdown fences
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            batch_results = json.loads(raw_text)
            if not isinstance(batch_results, list):
                raise ValueError("Expected JSON array from Groq")

            for art, res in zip(chunk, batch_results):
                affected = [c.lower() for c in res.get("affected_cryptos", [])]
                if not affected:
                    affected = _detect_cryptos(
                        f"{art.get('title', '')} {art.get('description', '')}"
                    )
                conf = int(res.get("confidence", 5))
                all_results.append({
                    "headline": art.get("title", ""),
                    "sentiment": str(res.get("sentiment", "neutral")).lower(),
                    "confidence": conf,
                    "reasoning": res.get("reasoning", ""),
                    "affected_cryptos": affected,
                    "market_impact": _impact_from_confidence(conf),
                    "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                    "model_used": _GROQ_MODEL,
                    "source": art.get("source", ""),
                })

        except json.JSONDecodeError as exc:
            log.warning(
                "[SENTIMENT-BATCH] Groq chunk %d-%d returned non-JSON — skipping: %s",
                chunk_start + 1, chunk_start + len(chunk), exc,
            )
        except Exception as exc:
            err_str = str(exc)
            # 401 = invalid/expired API key — stop retrying for this session
            if "401" in err_str or "invalid_api_key" in err_str.lower() or "Invalid API Key" in err_str:
                global _groq_auth_failed
                _groq_auth_failed = True
                log.error(
                    "[SENTIMENT-BATCH] Groq 401 — API key invalid or expired. "
                    "Update GROQ_API_KEY in .env at https://console.groq.com. "
                    "Skipping Groq for this session.",
                )
                break  # don't retry remaining chunks
            log.warning(
                "[SENTIMENT-BATCH] Groq chunk %d-%d failed — skipping: %s",
                chunk_start + 1, chunk_start + len(chunk), exc,
            )

    log.info(
        "[SENTIMENT-BATCH] Groq Llama 3.3 70B analyzed %d/%d articles | Cost: $0",
        len(all_results), len(articles),
    )
    return all_results


# ── Claude-based analysis ─────────────────────────────────────────────────────

def analyze_sentiment_with_claude(
    headline: str,
    description: str,
    content: str,
) -> dict:
    """
    Claude API sentiment analysis (premium mode).
    Falls back to neutral dict on any API/parse error — never crashes.
    """
    content_trimmed = (content or "")[:1500]
    prompt = _CLAUDE_PROMPT.format(
        headline=headline or "(no headline)",
        description=description or "(no description)",
        content=content_trimmed or "(no content)",
    )

    try:
        client = _get_client()
        message = client.messages.create(
            model=_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = message.content[0].text.strip()

        # Strip markdown code fences if Claude wraps the JSON
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        result = json.loads(raw_text)

        return {
            "headline": headline,
            "sentiment": result.get("sentiment", "neutral").lower(),
            "confidence": int(result.get("confidence", 0)),
            "reasoning": result.get("reasoning", ""),
            "affected_cryptos": [c.lower() for c in result.get("affected_cryptos", [])],
            "market_impact": result.get("market_impact", "LOW").upper(),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "model_used": _MODEL,
        }

    except json.JSONDecodeError as exc:
        log.error("Claude returned non-JSON response: %s", exc)
    except Exception as exc:
        log.error("Claude API error: %s", exc)

    # Neutral fallback — does not propagate exceptions
    return {
        "headline": headline,
        "sentiment": "neutral",
        "confidence": 0,
        "reasoning": "Analysis unavailable",
        "affected_cryptos": [],
        "market_impact": "LOW",
        "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        "model_used": _MODEL,
    }


# ── Batch analysis (cost-optimised) ──────────────────────────────────────────

_BATCH_PROMPT = """Analyze each article for cryptocurrency trading sentiment.

For EACH article respond with one JSON object. Return a JSON ARRAY of objects:
[
  {{"article": 1, "sentiment": "bullish", "confidence": 8, "affected_cryptos": ["bitcoin"], "reasoning": "brief"}}
]

Rules:
- sentiment: "bullish" | "bearish" | "neutral"
- confidence: integer 1-10
- affected_cryptos: list of crypto names mentioned
- reasoning: one short sentence
- ONLY output valid JSON array, no other text

ARTICLES:
{articles_text}"""


def analyze_articles_batch(articles: list[dict]) -> list[dict]:
    """
    Analyze up to 20 articles in a single call.

    Priority chain (automatic fallthrough on any failure):
      1. Claude API     — best quality, requires ANTHROPIC_API_KEY credits
      2. Gemini Flash   — frontier quality, FREE (1,500 calls/day), requires GEMINI_API_KEY
      3. Groq Llama 3.3 — very fast + accurate, FREE (14,400/day),  requires GROQ_API_KEY
      4. FinBERT local  — financial-domain BERT, FREE, requires transformers+torch
      5. Keyword        — pure rule-based, always available

    All modes return the same dict shape — downstream code is unaffected.
    """
    if not articles:
        return []

    _log_mode_once()

    def _apply_source_quality(results: list[dict], arts: list[dict]) -> list[dict]:
        """
        Tag each result with the article's source_quality score and apply a
        small confidence boost for Tier-1 sources (quality >= 0.90).
        This does NOT penalise lower-quality sources — it only rewards the best.
        """
        for res, art in zip(results, arts):
            sq = float(art.get("source_quality", 0.70))
            res["source_quality"] = round(sq, 3)
            # Small boost (+1) for premium sources (Reuters, Bloomberg, CoinDesk)
            if sq >= 0.90:
                res["confidence"] = min(10, res.get("confidence", 5) + 1)
        return results

    # ── Keyword fallback (last resort) ────────────────────────────────────
    def _keyword_fallback(reason: str) -> list[dict]:
        log.info("[SENTIMENT-BATCH] Keyword fallback (%s)", reason)
        results = []
        for art in articles:
            r = analyze_sentiment_with_keywords(
                headline=art.get("title", ""),
                description=art.get("description", ""),
                content=art.get("content", ""),
            )
            r.setdefault("source", art.get("source", ""))
            results.append(r)
        return _apply_source_quality(results, articles)

    # ── Priority 1: Claude API ─────────────────────────────────────────────
    if _is_claude_available():
        log.info("[SENTIMENT-BATCH] Priority 1: Claude API (%s) | %d articles", _BATCH_MODEL, len(articles))
        articles_text = "\n\n".join(
            f"Article {i+1}: {art.get('title', '')}. {(art.get('description') or '')[:200]}"
            for i, art in enumerate(articles[:20])
        )
        prompt = _BATCH_PROMPT.format(articles_text=articles_text)
        try:
            client = _get_client()
            message = client.messages.create(
                model=_BATCH_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = message.content[0].text.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            batch_results = json.loads(raw_text)
            if not isinstance(batch_results, list):
                raise ValueError("Expected JSON array")

            results: list[dict] = []
            for art, res in zip(articles, batch_results):
                affected = [c.lower() for c in res.get("affected_cryptos", [])]
                if not affected:
                    affected = _detect_cryptos(
                        f"{art.get('title', '')} {art.get('description', '')}"
                    )
                conf = int(res.get("confidence", 5))
                entry = {
                    "headline": art.get("title", ""),
                    "sentiment": str(res.get("sentiment", "neutral")).lower(),
                    "confidence": conf,
                    "reasoning": res.get("reasoning", ""),
                    "affected_cryptos": affected,
                    "market_impact": _impact_from_confidence(conf),
                    "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                    "model_used": _BATCH_MODEL,
                    "source": art.get("source", ""),
                }
                results.append(entry)

            log.info("[SENTIMENT-BATCH] Claude: %d/%d articles analyzed", len(results), len(articles))
            return _apply_source_quality(results, articles)

        except json.JSONDecodeError as exc:
            log.warning("[SENTIMENT-BATCH] Claude non-JSON → trying Gemini: %s", exc)
        except Exception as exc:
            log.warning("[SENTIMENT-BATCH] Claude failed → trying Gemini: %s", exc)

    # ── Priority 2: Gemini Flash (free tier) ──────────────────────────────
    if _is_gemini_available():
        log.info("[SENTIMENT-BATCH] Priority 2: Gemini Flash (%s) | %d articles", _GEMINI_MODEL, len(articles))
        results = analyze_articles_with_gemini(articles)
        if results:
            return _apply_source_quality(results, articles[:len(results)])
        log.warning("[SENTIMENT-BATCH] Gemini returned empty → trying Groq")

    # ── Priority 3: Groq + Llama 3.3 70B (free tier) ─────────────────────
    if _is_groq_available():
        log.info("[SENTIMENT-BATCH] Priority 3: Groq Llama 3.3 70B | %d articles", len(articles))
        results = analyze_articles_with_groq(articles)
        if results:
            return _apply_source_quality(results, articles[:len(results)])
        log.warning("[SENTIMENT-BATCH] Groq returned empty → trying FinBERT")

    # ── Priority 4: FinBERT local (financial-domain, free) ───────────────
    if _local_model_available():
        log.info("[SENTIMENT-BATCH] Priority 4: FinBERT local | %d articles", len(articles))
        results = analyze_with_local_model(articles)
        return _apply_source_quality(results, articles[:len(results)])

    # ── Priority 5: Keyword fallback ──────────────────────────────────────
    return _keyword_fallback(
        "set GEMINI_API_KEY (free) or GROQ_API_KEY (free) or install transformers+torch"
    )


# ── Public router ─────────────────────────────────────────────────────────────

def analyze_sentiment(headline: str, description: str, content: str) -> dict:
    """
    Analyze a single news article for crypto sentiment.

    Priority chain (same as analyze_articles_batch):
      1. Claude API     — requires ANTHROPIC_API_KEY
      2. Gemini Flash   — free, requires GEMINI_API_KEY
      3. Groq Llama 3.3 — free, requires GROQ_API_KEY
      4. FinBERT local  — free, requires transformers+torch
      5. Keyword        — always available

    All modes return the same dict shape — downstream code is unaffected.
    """
    _log_mode_once()

    # Batch-route via a single-element list for unified logic
    article = {"title": headline, "description": description, "content": content}
    results = analyze_articles_batch([article])
    if results:
        return results[0]
    return analyze_sentiment_with_keywords(headline, description, content)


# ── Signal filtering (unchanged) ──────────────────────────────────────────────

def filter_high_confidence_signals(
    sentiment_analyses: list[dict],
    threshold: Optional[int] = None,
) -> list[dict]:
    """
    Return only articles whose confidence meets the SIGNAL_THRESHOLD.

    Args:
        sentiment_analyses: List of dicts returned by analyze_sentiment().
        threshold: Override the config value (useful for testing).
    Returns:
        Filtered list; excludes neutral-confidence (0) fallbacks.
    """
    if threshold is None:
        threshold = load_config()["SIGNAL_THRESHOLD"]

    kept = [s for s in sentiment_analyses if s.get("confidence", 0) >= threshold]
    log.info(
        "Signal filter: %d/%d articles meet threshold %d/10",
        len(kept),
        len(sentiment_analyses),
        threshold,
    )
    return kept


# ── Sentiment score conversion (unchanged) ────────────────────────────────────

def classify_confluence(score: float) -> str:
    """Map a 0–1 confluence score to a human-readable level."""
    if score >= 0.80:
        return "VERY_HIGH"
    elif score >= 0.65:
        return "HIGH"
    elif score >= 0.50:
        return "MEDIUM"
    elif score >= 0.35:
        return "LOW"
    return "VERY_LOW"


def calculate_confluence_score(
    signal_data: dict,
    market_context: dict,
    market_category: str = None,
) -> dict:
    """
    Multi-signal confidence scoring combining sentiment, price action,
    volume, macro context, and source agreement into a single 0–1 score.

    Weights shift by market_category (70/30 strategy):
      POLITICS  — sentiment 50%, price 20%, volume 15%, macro 5%,  agreement 10%
      BTC / ETH — sentiment 30%, price 30%, volume 20%, macro 10%, agreement 10%
      default   — sentiment 30%, price 25%, volume 20%, macro 15%, agreement 10%

    Args:
        signal_data:      Sentiment analysis result dict.
        market_context:   Dict with optional keys:
                          price_confirmation (-1 to +1),
                          volume_confirmation (0–1),
                          macro_alignment (0–1),
                          agreement_ratio (0–1).
        market_category:  "BTC" | "ETH" | "POLITICS" | None.
    Returns:
        Dict with confluence_score, breakdown dict, level string, and market.
    """
    # Market-specific weight selection (must sum to 1.0)
    cat = (market_category or "").upper()
    if cat == "POLITICS":
        # Emotional market — sentiment is the dominant edge
        w_sent, w_price, w_vol, w_macro, w_agree = 0.50, 0.20, 0.15, 0.05, 0.10
    elif cat in ("BTC", "ETH"):
        # Technical market — price confirmation weighted equally with sentiment
        w_sent, w_price, w_vol, w_macro, w_agree = 0.30, 0.30, 0.20, 0.10, 0.10
    else:
        # Default balanced weights
        w_sent, w_price, w_vol, w_macro, w_agree = 0.30, 0.25, 0.20, 0.15, 0.10

    # SIGNAL 1: Sentiment score
    raw_confidence = signal_data.get("confidence", 5)
    if isinstance(raw_confidence, int) and raw_confidence > 1:
        sentiment_score = raw_confidence / 10.0
    else:
        sentiment_score = float(raw_confidence) if raw_confidence else 0.5
    sentiment_contribution = min(sentiment_score, 1.0) * w_sent

    # SIGNAL 2: Price action confirmation (-1 to +1 → 0–1)
    price_conf = float(market_context.get("price_confirmation", 0.0))
    price_contribution = ((price_conf + 1.0) / 2.0) * w_price

    # SIGNAL 3: Volume
    vol_conf = float(market_context.get("volume_confirmation", 0.5))
    volume_contribution = min(vol_conf, 1.0) * w_vol

    # SIGNAL 4: Macro alignment
    macro_align = float(market_context.get("macro_alignment", 0.5))
    macro_contribution = min(macro_align, 1.0) * w_macro

    # SIGNAL 5: Source agreement
    agreement = float(market_context.get("agreement_ratio", 0.7))
    agreement_contribution = min(agreement, 1.0) * w_agree

    total = max(0.0, min(1.0,
        sentiment_contribution + price_contribution + volume_contribution
        + macro_contribution + agreement_contribution
    ))

    log.debug(
        "Confluence %.2f [%s]: sent=%.2f price=%.2f vol=%.2f macro=%.2f agree=%.2f",
        total, cat or "DEFAULT",
        sentiment_contribution, price_contribution,
        volume_contribution, macro_contribution, agreement_contribution,
    )

    return {
        "confluence_score": round(total, 4),
        "breakdown": {
            "sentiment": round(sentiment_contribution, 4),
            "price": round(price_contribution, 4),
            "volume": round(volume_contribution, 4),
            "macro": round(macro_contribution, 4),
            "agreement": round(agreement_contribution, 4),
        },
        "level": classify_confluence(total),
        "market_category": cat or "DEFAULT",
    }


def get_sentiment_score(direction: str, confidence: int) -> float:
    """
    Map sentiment direction + confidence to a single 0-10 score.

    bullish → confidence as-is  (9 → 9.0)
    bearish → 10 - confidence   (8 → 2.0 = strong sell)
    neutral → 5.0               (skip zone)
    """
    direction = (direction or "neutral").lower()
    confidence = max(0, min(10, int(confidence)))

    if direction == "bullish":
        return float(confidence)
    if direction == "bearish":
        return float(10 - confidence)
    return 5.0
