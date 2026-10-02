from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    input_device: str = (os.getenv("JARVIS_INPUT_DEVICE") or "").strip()
    output_device: str = (os.getenv("JARVIS_OUTPUT_DEVICE") or "").strip()

    sample_rate: int = _int("JARVIS_SAMPLE_RATE", 44100)
    block_ms: int = _int("JARVIS_BLOCK_MS", 40)

    clap_warmup_s: float = _float("JARVIS_CLAP_WARMUP_S", 1.2)
    clap_spike_ratio: float = _float("JARVIS_CLAP_SPIKE_RATIO", 7.0)
    clap_min_rms: float = _float("JARVIS_CLAP_MIN_RMS", 0.012)
    clap_min_gap_s: float = _float("JARVIS_CLAP_MIN_GAP_S", 0.05)
    clap_max_gap_s: float = _float("JARVIS_CLAP_MAX_GAP_S", 0.35)
    clap_retrigger_ratio: float = _float("JARVIS_CLAP_RETRIGGER_RATIO", 0.55)
    clap_noise_floor_alpha: float = _float("JARVIS_CLAP_NOISE_FLOOR_ALPHA", 0.992)
    clap_quiet_gate_mult: float = _float("JARVIS_CLAP_QUIET_GATE_MULT", 2.2)

    speech_start_timeout_s: float = _float("JARVIS_SPEECH_START_TIMEOUT_S", 8.0)
    speech_max_duration_s: float = _float("JARVIS_SPEECH_MAX_DURATION_S", 15.0)
    speech_silence_s: float = _float("JARVIS_SPEECH_SILENCE_S", 0.80)
    speech_min_rms: float = _float("JARVIS_SPEECH_MIN_RMS", 0.006)
    speech_threshold_multiplier: float = _float(
        "JARVIS_SPEECH_THRESHOLD_MULTIPLIER", 2.2
    )
    speech_startup_grace_s: float = _float("JARVIS_SPEECH_STARTUP_GRACE_S", 0.12)
    speech_pre_roll_s: float = _float("JARVIS_SPEECH_PRE_ROLL_S", 0.60)
    speech_onset_blocks: int = _int("JARVIS_SPEECH_ONSET_BLOCKS", 3)
    speech_min_voiced_s: float = _float("JARVIS_SPEECH_MIN_VOICED_S", 0.28)
    tts_settle_s: float = _float("JARVIS_TTS_SETTLE_S", 0.30)

    stt_provider: str = (os.getenv("JARVIS_STT_PROVIDER") or "local").strip().lower()
    whisper_model: str = (os.getenv("JARVIS_WHISPER_MODEL") or "small").strip()
    whisper_device: str = (os.getenv("JARVIS_WHISPER_DEVICE") or "cpu").strip()
    whisper_compute_type: str = (
        os.getenv("JARVIS_WHISPER_COMPUTE_TYPE") or "int8"
    ).strip()
    whisper_beam_size: int = _int("JARVIS_WHISPER_BEAM_SIZE", 1)
    groq_stt_model: str = (
        os.getenv("JARVIS_GROQ_STT_MODEL") or "whisper-large-v3-turbo"
    ).strip()
    groq_stt_fallback_local: bool = _bool(
        "JARVIS_GROQ_STT_FALLBACK_LOCAL",
        True,
    )
    stt_language: str | None = (
        os.getenv("JARVIS_STT_LANGUAGE") or "fr"
    ).strip() or None
    stt_supported_languages: tuple[str, ...] = tuple(
        part.strip().lower()
        for part in (
            os.getenv("JARVIS_STT_SUPPORTED_LANGUAGES") or "fr,en,ar"
        ).split(",")
        if part.strip()
    )
    stt_language_confidence: float = _float(
        "JARVIS_STT_LANGUAGE_CONFIDENCE", 0.72
    )
    stt_retry_language_probability: float = _float(
        "JARVIS_STT_RETRY_LANGUAGE_PROBABILITY", 0.65
    )
    stt_initial_prompt: str = (
        os.getenv("JARVIS_STT_INITIAL_PROMPT")
        or "Conversation naturelle avec Jarvis. L'utilisateur peut parler "
        "français, anglais ou arabe, et peut changer de langue entre deux phrases."
    ).strip()

    ai_provider: str = (os.getenv("JARVIS_AI_PROVIDER") or "ollama").strip()

    # Model-native agent runtime. This is intentionally separate from the
    # older planner so the voice layer can switch brains without rewrites.
    agent_provider: str = (
        os.getenv("JARVIS_AGENT_PROVIDER") or "ollama"
    ).strip()
    ollama_agent_model: str = (
        os.getenv("JARVIS_OLLAMA_AGENT_MODEL") or "qwen3:4b-instruct"
    ).strip()
    ollama_agent_timeout_s: float = _float(
        "JARVIS_OLLAMA_AGENT_TIMEOUT_S", 90.0
    )
    ollama_agent_num_ctx: int = _int(
        "JARVIS_OLLAMA_AGENT_NUM_CTX", 4096
    )
    ollama_agent_num_predict: int = _int(
        "JARVIS_OLLAMA_AGENT_NUM_PREDICT", 160
    )
    ollama_agent_keep_alive: str = (
        os.getenv("JARVIS_OLLAMA_AGENT_KEEP_ALIVE") or "30m"
    ).strip()
    agent_max_tool_rounds: int = _int("JARVIS_AGENT_MAX_TOOL_ROUNDS", 8)
    agent_history_items: int = _int("JARVIS_AGENT_HISTORY_ITEMS", 8)
    agent_history_turns: int = _int("JARVIS_AGENT_HISTORY_TURNS", 20)
    ms_football_bridge_url: str = (
        os.getenv("JARVIS_MS_FOOTBALL_BRIDGE_URL")
        or "http://127.0.0.1:8765"
    ).strip().rstrip("/")
    ms_football_bridge_token: str = (
        os.getenv("JARVIS_MS_FOOTBALL_BRIDGE_TOKEN") or ""
    ).strip()

    groq_api_key: str = (os.getenv("GROQ_API_KEY") or "").strip()
    groq_base_url: str = (
        os.getenv("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
    ).strip()
    groq_agent_model: str = (
        os.getenv("JARVIS_GROQ_AGENT_MODEL") or "openai/gpt-oss-120b"
    ).strip()
    groq_reasoning_effort: str = (
        os.getenv("JARVIS_GROQ_REASONING_EFFORT") or "low"
    ).strip()
    groq_browser_search: bool = _bool("JARVIS_GROQ_BROWSER_SEARCH", True)

    cerebras_api_key: str = (os.getenv("CEREBRAS_API_KEY") or "").strip()
    cerebras_base_url: str = (
        os.getenv("CEREBRAS_BASE_URL") or "https://api.cerebras.ai/v1"
    ).strip()
    cerebras_secondary_api_key: str = (
        os.getenv("CEREBRAS_SECONDARY_API_KEY") or ""
    ).strip()
    cerebras_secondary_base_url: str = (
        os.getenv("CEREBRAS_SECONDARY_BASE_URL")
        or os.getenv("CEREBRAS_BASE_URL")
        or "https://api.cerebras.ai/v1"
    ).strip()
    cerebras_agent_model: str = (
        os.getenv("JARVIS_CEREBRAS_AGENT_MODEL") or "gpt-oss-120b"
    ).strip()
    cerebras_reasoning_effort: str = (
        os.getenv("JARVIS_CEREBRAS_REASONING_EFFORT") or "low"
    ).strip()
    cerebras_request_timeout_s: float = _float(
        "JARVIS_CEREBRAS_REQUEST_TIMEOUT_S",
        8.0,
    )
    cerebras_fallback_to_groq: bool = _bool(
        "JARVIS_CEREBRAS_FALLBACK_TO_GROQ",
        True,
    )

    openai_api_key: str = (os.getenv("OPENAI_API_KEY") or "").strip()
    openai_base_url: str = (
        os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    ).strip()
    openai_agent_model: str = (
        os.getenv("JARVIS_OPENAI_AGENT_MODEL") or "gpt-6-astra"
    ).strip()
    openai_reasoning_effort: str = (
        os.getenv("JARVIS_OPENAI_REASONING_EFFORT") or "low"
    ).strip()
    openai_web_search: bool = _bool("JARVIS_OPENAI_WEB_SEARCH", True)
    planner_provider: str = (
        os.getenv("JARVIS_PLANNER_PROVIDER") or "ollama"
    ).strip()
    ollama_base_url: str = (
        os.getenv("JARVIS_OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
    ).strip()
    ollama_model: str = (
        os.getenv("JARVIS_OLLAMA_MODEL") or "gemma3:latest"
    ).strip()
    vision_enabled: bool = _bool("JARVIS_VISION_ENABLED", True)
    vision_model: str = (
        os.getenv("JARVIS_VISION_MODEL") or "gemma3:latest"
    ).strip()
    vision_timeout_s: float = _float("JARVIS_VISION_TIMEOUT_S", 20.0)
    vision_max_width: int = _int("JARVIS_VISION_MAX_WIDTH", 1600)
    vision_num_predict: int = _int("JARVIS_VISION_NUM_PREDICT", 420)
    vision_local_only: bool = _bool("JARVIS_VISION_LOCAL_ONLY", True)
    vision_save_evidence: bool = _bool(
        "JARVIS_VISION_SAVE_EVIDENCE",
        False,
    )
    vision_evidence_max_files: int = _int(
        "JARVIS_VISION_EVIDENCE_MAX_FILES",
        30,
    )
    ai_request_timeout_s: float = _float("JARVIS_AI_REQUEST_TIMEOUT_S", 60.0)
    ai_history_messages: int = _int("JARVIS_AI_HISTORY_MESSAGES", 8)

    elevenlabs_api_key: str = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    elevenlabs_voice_id: str = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    elevenlabs_model_id: str = (
        os.getenv("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"
    ).strip()
    elevenlabs_output_format: str = (
        os.getenv("ELEVENLABS_OUTPUT_FORMAT") or "pcm_24000"
    ).strip()

    conversation_followup_timeout_s: float = _float(
        "JARVIS_CONVERSATION_FOLLOWUP_TIMEOUT_S", 20.0
    )
    confirmation_timeout_s: float = _float(
        "JARVIS_CONFIRMATION_TIMEOUT_S", 15.0
    )

    wake_phrase: str = (os.getenv("JARVIS_WAKE_RESPONSE") or "Oui monsieur ?").strip()
    ui_fullscreen: bool = _bool("JARVIS_UI_FULLSCREEN", False)


settings = Settings()
