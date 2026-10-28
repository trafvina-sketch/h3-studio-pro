# -*- coding: utf-8 -*-
"""
Krea 2 Turbo & Civitai Helper Integration Engine
------------------------------------------------
Tích hợp:
1. Tạo ảnh trực tiếp bằng mô hình Krea 2 Turbo (8-step distilled, Qwen3-VL-4B text encoder + Qwen Image VAE)
   thông qua cụm Cloud Worker Krea-2-Turbo (tự động xoay vòng đa node) hoặc ComfyUI.
2. Hỗ trợ cả 2 chế độ của Krea 2:
   - Text-to-Image (T2I): Sinh ảnh siêu thực / điện ảnh / nhân vật tham chiếu <Picture 1..9> & Ref_*.png.
   - Image Edit / Reference (I2I): Chỉnh sửa hoặc giữ đồng nhất nhân vật từ 1-2 ảnh tham chiếu.
3. Tích hợp Civitai Helper API (tương thích chuẩn ComfyUI-Civitai-Helper & SD-WebUI-Civitai-Helper):
   - Tự động truy vấn Civitai REST API (https://civitai.com/api/v1/models) cho hệ sinh thái Krea 2.
   - Tự động lấy Metadata (.civitai.info), Trigger Words (trainedWords), ảnh Preview và link tải LoRA/Checkpoint.
4. Xuất file ComfyUI Workflow chuẩn Comfy-Org/Krea-2 (UNETLoader + CLIPLoader type='krea2' + VAELoader).
"""

from __future__ import annotations

import base64
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent.parent
KREA2_OUTPUT_DIR = SCRIPT_DIR / "output" / "krea2_images"
KREA2_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

KREA2_CONFIG_FILE = SCRIPT_DIR / ".krea2_config.json"

# Danh sách các cụm Krea-2-Turbo Cloud Spaces đang hoạt động (tự động failover)
DEFAULT_KREA2_SPACES: List[str] = [
    "https://aura1in-krea-2-turbo-v2.hf.space",
    "https://2i-pornmaster-krea2.hf.space",
    "https://krea-krea-2.hf.space",
    "https://ziadhero-krea-extra.hf.space",
    "https://alexander00001-krea2-adult-nsfw.hf.space",
    "https://hugging-apps-krea2-turbo-sda-demo.hf.space",
]

_ZEROGPU_COOLDOWN_UNTIL: float = 0.0
_HORDE_MODELS_CACHE: Dict[str, Any] = {"ts": 0.0, "models": []}


def load_krea2_config() -> Dict[str, str]:
    import os
    cfg = {
        "hf_token": os.environ.get("HF_TOKEN", "").strip(),
        "horde_api_key": os.environ.get("HORDE_API_KEY", "0000000000").strip() or "0000000000",
    }
    if KREA2_CONFIG_FILE.is_file():
        try:
            saved = json.loads(KREA2_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                if saved.get("hf_token"):
                    cfg["hf_token"] = str(saved["hf_token"]).strip()
                if saved.get("horde_api_key"):
                    cfg["horde_api_key"] = str(saved["horde_api_key"]).strip()
        except Exception:
            pass
    return cfg


def save_krea2_config(hf_token: str = "", horde_api_key: str = "") -> Dict[str, str]:
    cfg = load_krea2_config()
    if hf_token is not None:
        cfg["hf_token"] = str(hf_token).strip()
    if horde_api_key is not None:
        cfg["horde_api_key"] = str(horde_api_key).strip() or "0000000000"
    try:
        KREA2_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return cfg


# Danh sách 7 Base Checkpoints MOSS Image (Hệ sinh thái MOSS-Image & Moss-Image-Edit)
MOSS_BASE_CHECKPOINTS: List[Dict[str, Any]] = [
    {
        "id": "moss_kromoss",
        "name": "🌿 Kromoss — Cải thiện sương mù trong Base & Tương phản sâu (Khuyên dùng)",
        "short_name": "Kromoss",
        "engine_family": "moss",
        "hf_repo": "lodestones/Kromoss",
        "hf_file": "diffusion_models/kromoss_int8_convrot.safetensors",
        "hf_file_bf16": "diffusion_models/kromoss_bf16.safetensors",
        "source_url": "https://huggingface.co/lodestones/Kromoss",
        "style_prompt_boost": "",
        "desc_vi": "Cải thiện sương mù trong Base (De-haze), tăng độ trong trẻo, chi tiết sắc nét và tương phản điện ảnh sâu.",
    },
    {
        "id": "moss_serenity",
        "name": "🌿 MOSS Serenity v6 — Tăng cường tính thực tế của nhân vật thực tế",
        "short_name": "MOSS Serenity",
        "engine_family": "moss",
        "hf_repo": "malcolmrey/moss-serenity",
        "hf_file": "serenity_base_v6_210000_int8_convrot.safetensors",
        "hf_file_bf16": "serenity_base_v5_170000_bf16.safetensors",
        "source_url": "https://huggingface.co/malcolmrey/moss-serenity",
        "style_prompt_boost": "ultra-realistic human portrait, natural skin texture with subtle pores, lifelike eyes",
        "desc_vi": "Tăng cường tính thực tế của nhân vật người thật: kết cấu làn da tự nhiên, ánh mắt sống động và giải phẫu chuẩn.",
    },
    {
        "id": "moss_imagine",
        "name": "🌿 MOSS-Imagine — Biểu hiện điện ảnh theo phong cách Grok Imagine",
        "short_name": "MOSS-Imagine",
        "engine_family": "moss",
        "hf_repo": "realrebelai/MOSS-Imagine_ComfyUI",
        "hf_file": "MOSS-Imagine-INT8-ConvRot.safetensors",
        "hf_file_bf16": "MOSS-Imagine-BF16.safetensors",
        "source_url": "https://huggingface.co/realrebelai/MOSS-Imagine_ComfyUI",
        "style_prompt_boost": "cinematic dramatic lighting, rich atmospheric depth, Grok Imagine aesthetic",
        "desc_vi": "Biểu hiện điện ảnh theo phong cách Grok Imagine: ánh sáng kịch tính, chiều sâu không gian và cảm xúc điện ảnh.",
    },
    {
        "id": "moss_pastel_bloom",
        "name": "🌿 MOSS Pastel Bloom — Màu sắc mềm và đường viền nhẹ nhàng",
        "short_name": "MOSS Pastel Bloom",
        "engine_family": "moss",
        "hf_repo": "lodestones/Kromoss",
        "hf_file": "diffusion_models/kromoss_int8_convrot.safetensors",
        "hf_file_bf16": "diffusion_models/kromoss_bf16.safetensors",
        "civitai_model_id": "3006255",
        "source_url": "https://civitai.com/models/3006255/moss-pastel-bloom",
        "style_prompt_boost": "soft pastel bloom color palette, gentle delicate outlines, dreamy luminous atmosphere, smooth tonal transitions",
        "desc_vi": "Màu sắc mềm và đường viền nhẹ nhàng, hiệu ứng ánh sáng bloom thơ mộng.",
    },
    {
        "id": "moss_animossity",
        "name": "🌿 AniMOSSity — Hoạt hình × Thực tế, màu sắc sống động",
        "short_name": "AniMOSSity",
        "engine_family": "moss",
        "hf_repo": "lodestones/Kromoss",
        "hf_file": "diffusion_models/kromoss_int8_convrot.safetensors",
        "hf_file_bf16": "diffusion_models/kromoss_bf16.safetensors",
        "civitai_model_id": "3010746",
        "source_url": "https://civitai.com/models/3010746/animossity",
        "style_prompt_boost": "semi-realistic 2.5D anime x realistic hybrid style, vibrant vivid colors, expressive detailed eyes, dynamic cinematic lighting",
        "desc_vi": "Hoạt hình × thực tế, màu sắc sống động và chi tiết mắt biểu cảm.",
    },
    {
        "id": "moss_style_ft",
        "name": "🌿 MOSS Image Style FT — Ảnh chụp nhanh tự nhiên",
        "short_name": "MOSS Style FT",
        "engine_family": "moss",
        "hf_repo": "lamshiu/moss-image-style-ft",
        "hf_file": "int8_convrot/step-5632_int8_convrot.safetensors",
        "hf_file_bf16": "checkpoints/step-5632.safetensors",
        "source_url": "https://huggingface.co/lamshiu/moss-image-style-ft",
        "style_prompt_boost": "natural candid snapshot photography, authentic daylight, realistic grain",
        "desc_vi": "Ảnh chụp nhanh tự nhiên (Natural Snapshot), góc máy đời thường chân thực.",
    },
    {
        "id": "moss_kr2",
        "name": "🌿 MOSSkr2 — Krea 2 modul trọng lượng lai MOSS Image",
        "short_name": "MOSSkr2",
        "engine_family": "moss",
        "hf_repo": "TenStrip/MOSSkr2",
        "hf_file": "mosskr2_image_sft_bf16_v1.safetensors",
        "hf_file_bf16": "mosskr2_image_sft_bf16_v1.safetensors",
        "source_url": "https://huggingface.co/TenStrip/MOSSkr2",
        "style_prompt_boost": "",
        "desc_vi": "Krea 2 modul trọng lượng kết hợp nền tảng MOSS Image SFT BF16 v1.",
    },
]

# Danh sách Base Checkpoints MOSS Image + Krea 2
KREA2_BASE_CHECKPOINTS: List[Dict[str, Any]] = [
    *MOSS_BASE_CHECKPOINTS,
    {
        "id": "gonzalomoKrea2_v40.safetensors",
        "name": "🎨 Gonzalomo Krea 2 v4.0 (Siêu thực & Điện ảnh / Krea 2)",
        "short_name": "Gonzalomo Krea 2 v4.0",
        "engine_family": "krea2",
        "hf_repo": "",
        "hf_file": "",
        "desc_vi": "Bản tinh chỉnh Krea 2 chuyên ảnh chụp điện ảnh, da người thật, bối cảnh phim và nhân vật tham chiếu 8K.",
    },
    {
        "id": "pornmasterKrea2_v2TurboInt8.safetensors",
        "name": "🎨 Krea 2 Realism v2 Turbo Int8 (Chi tiết cơ thể & Uncensored 18+)",
        "short_name": "Krea 2 Realism v2",
        "engine_family": "krea2",
        "hf_repo": "",
        "hf_file": "",
        "desc_vi": "Tốc độ cao Int8, hỗ trợ đầy đủ cả chế độ thường và Uncensored 18+ không kiểm duyệt.",
    },
    {
        "id": "museByStableYogi_v25EXTENDEDTURBO.safetensors",
        "name": "🎨 Muse Extended Turbo v2.5 (Chân dung & Ánh sáng nghệ thuật)",
        "short_name": "Muse Turbo v2.5",
        "engine_family": "krea2",
        "hf_repo": "",
        "hf_file": "",
        "desc_vi": "Tối ưu độ chi tiết khuôn mặt, ánh sáng studio và bố cục thẩm mỹ cao.",
    },
    {
        "id": "comfy_org_krea2_turbo_fp8",
        "name": "🎨 Krea 2 Turbo Official FP8 (Comfy-Org Chính thức)",
        "short_name": "Krea 2 Official FP8",
        "engine_family": "krea2",
        "hf_repo": "Comfy-Org/Krea-2",
        "hf_file": "diffusion_models/krea2_turbo_fp8_scaled.safetensors",
        "desc_vi": "Bản gốc Krea 2 Turbo FP8 chính thức từ Comfy-Org & Krea AI.",
    },
]

# Danh sách Style LoRAs chính thức từ MOSS-Image & Comfy-Org/Krea-2 kèm Trigger Words
KREA2_OFFICIAL_STYLE_LORAS: List[Dict[str, Any]] = [
    {
        "id": "none",
        "name": "Không dùng Style LoRA (Giữ nét gốc của Checkpoint)",
        "filename": "",
        "repo_id": "",
        "trigger_word": "",
        "strength": 0.0,
        "engine_family": "all",
    },
    {
        "id": "moss_dmd2_8step",
        "name": "⚡ MOSS 8-Steps DMD2 Turbo LoRA (Tăng tốc 8 bước chuẩn Kromoss)",
        "filename": "8-steps-dmd2-lora.safetensors",
        "repo_id": "lodestones/Kromoss",
        "trigger_word": "",
        "strength": 1.0,
        "engine_family": "moss",
    },
    {
        "id": "moss_serenity_v6_lora",
        "name": "👩 MOSS Serenity v6 LoRA r128 (Tăng chi tiết da & nhân vật thực tế)",
        "filename": "lora-extract/serenity_v6_lora_r128.safetensors",
        "repo_id": "malcolmrey/moss-serenity",
        "trigger_word": "",
        "strength": 0.85,
        "engine_family": "moss",
    },
    {
        "id": "moss_image_edit_lora",
        "name": "🪄 Moss-Image-Edit LoRA (Sửa trang phục, màu sắc, ánh sáng, mùa, style)",
        "filename": "adapter_model.safetensors",
        "repo_id": "chinoll/Moss-Image-Edit",
        "trigger_word": "",
        "strength": 1.0,
        "engine_family": "moss",
    },
    {
        "id": "krea2_darkbrush",
        "name": "Dark Brush (Tranh thủy mặc / Mực tàu)",
        "filename": "loras/krea2_darkbrush.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "monochrome ink wash style",
        "strength": 1.0,
    },
    {
        "id": "krea2_retroanime",
        "name": "Retro Anime (Hoạt hình Anime cổ điển)",
        "filename": "loras/krea2_retroanime.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "purple retro anime style",
        "strength": 1.0,
    },
    {
        "id": "krea2_softwatercolor",
        "name": "Soft Watercolor (Màu nước nghệ thuật)",
        "filename": "loras/krea2_softwatercolor.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "art deco watercolor style",
        "strength": 1.0,
    },
    {
        "id": "krea2_rainywindow",
        "name": "Rainy Window (Khung cửa mưa điện ảnh)",
        "filename": "loras/krea2_rainywindow.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "rainy window style",
        "strength": 1.0,
    },
    {
        "id": "krea2_sunsetblur",
        "name": "Sunset Blur (Hoàng hôn mơ màng)",
        "filename": "loras/krea2_sunsetblur.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "ethereal motion blur style",
        "strength": 1.0,
    },
    {
        "id": "krea2_vintagetarot",
        "name": "Vintage Tarot (Tranh khắc cổ điển)",
        "filename": "loras/krea2_vintagetarot.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "vintage tarot style",
        "strength": 1.0,
    },
    {
        "id": "krea2_dotmatrix",
        "name": "Dot Matrix (Tranh chấm bi đơn sắc)",
        "filename": "loras/krea2_dotmatrix.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "monochrome stippling style",
        "strength": 1.0,
    },
    {
        "id": "krea2_kidsdrawing",
        "name": "Expressive Sketch (Phác thảo tự nhiên)",
        "filename": "loras/krea2_kidsdrawing.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "naive expressive sketch style",
        "strength": 1.0,
    },
    {
        "id": "krea2_neondrip",
        "name": "Neon Drip (Trừu tượng Neon)",
        "filename": "loras/krea2_neondrip.safetensors",
        "repo_id": "Comfy-Org/Krea-2",
        "trigger_word": "textured abstract style",
        "strength": 1.0,
    },
]

_CIVITAI_CACHE: Dict[str, Any] = {"ts": 0.0, "query": "", "items": []}


def search_civitai_krea2_models(query: str = "Krea 2", limit: int = 16, model_type: str = "") -> Dict[str, Any]:
    """
    Truy vấn trực tiếp Civitai REST API v1 (chuẩn ComfyUI-Civitai-Helper & SD-WebUI-Civitai-Helper)
    để lấy danh sách Checkpoint / LoRA / Workflow của Krea 2 kèm Trigger Words, ảnh Preview và link tải.
    """
    q_clean = (query or "Krea 2").strip()
    cache_key = f"{q_clean.lower()}|{limit}|{model_type}"
    now = time.time()
    if _CIVITAI_CACHE.get("query") == cache_key and (now - float(_CIVITAI_CACHE.get("ts") or 0)) < 300 and _CIVITAI_CACHE.get("items"):
        return {"ok": True, "items": _CIVITAI_CACHE["items"], "cached": True}

    params: Dict[str, Any] = {
        "query": q_clean,
        "limit": max(1, min(30, int(limit or 16))),
        "nsfw": "false",
    }
    if model_type in ("Checkpoint", "LORA", "Workflows"):
        params["types"] = model_type

    url = "https://civitai.com/api/v1/models?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CivitaiHelper/2.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
        items_out = []
        for it in raw.get("items") or []:
            versions = it.get("modelVersions") or []
            v0 = versions[0] if versions else {}
            images = v0.get("images") or []
            preview_url = ""
            for im in images:
                if im.get("url") and str(im.get("type") or "image") != "video":
                    preview_url = im["url"]
                    break
            if not preview_url and images:
                preview_url = images[0].get("url") or ""
            files = v0.get("files") or []
            f0 = files[0] if files else {}
            trained_words = [str(w).strip() for w in (v0.get("trainedWords") or []) if str(w).strip()]
            items_out.append({
                "id": it.get("id"),
                "version_id": v0.get("id"),
                "name": it.get("name") or "Krea 2 Model",
                "type": it.get("type") or "LORA",
                "base_model": v0.get("baseModel") or "Krea 2",
                "trained_words": trained_words,
                "preview_url": preview_url,
                "download_url": v0.get("downloadUrl") or f0.get("downloadUrl") or "",
                "filename": f0.get("name") or "",
                "size_kb": round(float(f0.get("sizeKB") or 0), 1),
                "civitai_url": f"https://civitai.com/models/{it.get('id')}",
                "creator": (it.get("creator") or {}).get("username") or "Civitai",
                "stats": it.get("stats") or {},
            })
        _CIVITAI_CACHE["ts"] = now
        _CIVITAI_CACHE["query"] = cache_key
        _CIVITAI_CACHE["items"] = items_out
        return {"ok": True, "items": items_out, "cached": False}
    except Exception as e:
        return {"ok": False, "error": f"Không thể kết nối Civitai API: {e}", "items": _CIVITAI_CACHE.get("items") or []}


_VI_DIACRITIC_RE = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE)
_NSFW_KEYWORDS_RE = re.compile(
    r"(khoả thân|khỏa thân|lõa thể|loã thể|ngực trần|không mặc đồ|cởi đồ|cởi truồng|quan hệ|làm tình|ân ái|giao hợp|"
    r"\b(?:nude|naked|nsfw|topless|erotic|sensual|uncensored|18\+|sexy|bikini|lingerie|breasts|nipples|pussy|ass|boudoir|sex|intercourse|fuck|fucking|making love|hentai|jav|porn|blowjob|cum|penis|vagina|dick|cock|boobs|tits)\b)",
    re.IGNORECASE,
)


def translate_and_enhance_image_prompt(
    raw_prompt: str,
    base_checkpoint: str = "",
    trigger_word: str = "",
) -> Tuple[str, bool]:
    """
    Tự động dịch Prompt tiếng Việt sang tiếng Anh chuẩn nhiếp ảnh 8K và phát hiện chế độ 18+ Uncensored.
    Trả về (english_enhanced_prompt, is_nsfw).
    """
    txt = (raw_prompt or "").strip()
    is_nsfw = bool(
        _NSFW_KEYWORDS_RE.search(txt)
        or "pornmaster" in str(base_checkpoint or "").lower()
        or "nsfw" in str(base_checkpoint or "").lower()
    )

    # Xóa tiền tố mệnh lệnh tiếng Việt phổ biến trước khi dịch
    txt_cleaned = re.sub(
        r"^(?:hãy\s+|xin\s+|vui lòng\s+)?(?:tạo|vẽ|sinh|làm|chụp)\s+(?:cho tôi\s+)?(?:một\s+|1\s+)?(?:bức ảnh|hình ảnh|ảnh|bức hình|hình)?\s*",
        "",
        txt,
        flags=re.IGNORECASE,
    ).strip() or txt

    # Nếu có dấu tiếng Việt hoặc từ tiếng Việt phổ biến -> dịch sang tiếng Anh
    has_vi = bool(_VI_DIACRITIC_RE.search(txt_cleaned)) or any(
        w in txt_cleaned.lower() for w in ("cô gái", "chàng trai", "phụ nữ", "đàn ông", "khoả thân", "khỏa thân", "mặc", "đẹp")
    )
    en_prompt = txt_cleaned
    if has_vi:
        try:
            q_url = (
                "https://translate.googleapis.com/translate_a/single?client=gtx&sl=vi&tl=en&dt=t&q="
                + urllib.parse.quote(txt_cleaned)
            )
            req = urllib.request.Request(q_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                tr_data = json.loads(r.read().decode("utf-8", errors="ignore"))
            if isinstance(tr_data, list) and tr_data and isinstance(tr_data[0], list):
                translated = "".join(seg[0] for seg in tr_data[0] if seg and seg[0]).strip()
                if translated:
                    en_prompt = translated
        except Exception:
            # Từ điển dự phòng khi offline
            vi_map = {
                "khoả thân": "completely nude, naked, uncensored natural body",
                "khỏa thân": "completely nude, naked, uncensored natural body",
                "cô gái": "stunningly beautiful young Asian woman",
                "người phụ nữ": "beautiful woman",
                "xinh đẹp": "gorgeous, detailed symmetrical face",
                "gợi cảm": "sensual, alluring pose",
                "bikini": "wearing a sleek bikini",
                "cổ trang": "ancient Chinese Xianxia silk robes",
                "kiếm khách": "martial arts swordmaster",
            }
            tmp_p = txt_cleaned.lower()
            for k_vi, v_en in vi_map.items():
                tmp_p = tmp_p.replace(k_vi, v_en)
            en_prompt = tmp_p

    # Xóa tiền tố mệnh lệnh tiếng Anh nếu có ("create a...", "generate an image of...")
    en_prompt = re.sub(
        r"^(?:please\s+)?(?:create|generate|draw|make|render)\s+(?:an?\s+)?(?:image|photo|picture|portrait)?\s*(?:of\s+)?",
        "",
        en_prompt,
        flags=re.IGNORECASE,
    ).strip() or en_prompt

    if _NSFW_KEYWORDS_RE.search(en_prompt):
        is_nsfw = True

    # Mở rộng chất lượng 8K nếu prompt ngắn (tự động phân biệt toàn thân full-body và chân dung)
    word_count = len(en_prompt.split())
    is_full_body = any(
        k in txt.lower() or k in en_prompt.lower()
        for k in ("toàn thân", "full body", "full-body", "standing", "head to toe", "đứng")
    )
    framing_tag = "full-body standing shot of" if is_full_body else "cinematic shot of"
    if word_count < 22 and "raw photo" not in en_prompt.lower() and "8k" not in en_prompt.lower():
        if is_nsfw:
            en_prompt = (
                f"raw photo, masterpiece, best quality, ultra-photorealistic 8k {framing_tag} {en_prompt}, "
                "flawless natural skin texture, subsurface scattering, perfect symmetrical anatomy and limbs, "
                "soft warm studio lighting, 50mm f/1.4 lens, sharp focus"
            )
        else:
            en_prompt = (
                f"raw photo, masterpiece, best quality, ultra-photorealistic 8k {framing_tag} {en_prompt}, "
                "natural skin texture, detailed lighting, 85mm lens, sharp focus, high dynamic range"
            )

    trig_clean = (trigger_word or "").strip()
    if trig_clean and trig_clean.lower() not in en_prompt.lower():
        en_prompt = f"{trig_clean}, {en_prompt}"

    return en_prompt, is_nsfw


def _format_and_upscale_image_bytes(raw_bytes: bytes, target_w: int, target_h: int, crop_watermark_px: int = 0) -> bytes:
    """Giải mã ảnh (WebP/JPEG/PNG), cắt watermark nếu có, tăng tương phản vi mô CLAHE + Lanczos4 + Unsharp Mask về đúng độ phân giải đích và xuất PNG."""
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(raw_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes
        ih, iw = img.shape[:2]
        if crop_watermark_px > 0 and ih > crop_watermark_px * 3:
            img = img[: ih - crop_watermark_px, :]
            ih, iw = img.shape[:2]

        # Cắt đồng tâm theo đúng tỷ lệ khung hình đích trước khi phóng nét để không bị méo hình
        target_ratio = float(target_w) / float(max(1, target_h))
        curr_ratio = float(iw) / float(max(1, ih))
        if abs(curr_ratio - target_ratio) > 0.04:
            if curr_ratio > target_ratio:
                new_w = max(64, int(round(ih * target_ratio)))
                x0 = (iw - new_w) // 2
                img = img[:, x0 : x0 + new_w]
            else:
                new_h = max(64, int(round(iw / target_ratio)))
                y0 = (ih - new_h) // 2
                img = img[y0 : y0 + new_h, :]

        if img.shape[1] != target_w or img.shape[0] != target_h:
            interp = cv2.INTER_AREA if (img.shape[1] > target_w and img.shape[0] > target_h) else cv2.INTER_LANCZOS4
            img = cv2.resize(img, (target_w, target_h), interpolation=interp)

        # Tăng độ sâu chi tiết vi mô (lỗ chân lông, ánh mắt, sợi tóc) bằng CLAHE nhẹ trên kênh L + Unsharp Mask
        try:
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_ch, a_ch, b_ch = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=1.25, tileGridSize=(8, 8))
            l_enh = clahe.apply(l_ch)
            l_blend = cv2.addWeighted(l_ch, 0.65, l_enh, 0.35, 0)
            img = cv2.cvtColor(cv2.merge((l_blend, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
        except Exception:
            pass

        gaussian = cv2.GaussianBlur(img, (0, 0), 1.05)
        img = cv2.addWeighted(img, 1.22, gaussian, -0.22, 0)

        ok, enc = cv2.imencode(".png", img)
        if ok:
            return enc.tobytes()
    except Exception:
        pass
    return raw_bytes


def _upload_local_image_to_gradio_space(host: str, image_path_or_b64: str, hf_token: str = "") -> Optional[Dict[str, Any]]:
    """Tải ảnh tham chiếu lên Gradio Space để dùng cho chế độ Krea 2 Edit / Style Reference."""
    if not image_path_or_b64:
        return None
    raw_bytes: Optional[bytes] = None
    orig_name = "reference.png"
    if image_path_or_b64.startswith("data:image"):
        b64_part = image_path_or_b64.split(",", 1)[1]
        raw_bytes = base64.b64decode(b64_part)
    else:
        p = Path(image_path_or_b64)
        if p.is_file():
            raw_bytes = p.read_bytes()
            orig_name = p.name
    if not raw_bytes:
        return None

    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{orig_name}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + raw_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "Mozilla/5.0"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    for up_ep in ("/gradio_api/upload", "/upload"):
        try:
            req = urllib.request.Request(
                f"{host.rstrip('/')}{up_ep}",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                up_list = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if isinstance(up_list, list) and up_list:
                remote_path = up_list[0]
                return {
                    "path": remote_path,
                    "url": f"{host.rstrip('/')}/gradio_api/file={remote_path}",
                    "orig_name": orig_name,
                    "size": len(raw_bytes),
                    "mime_type": "image/png",
                    "is_stream": False,
                    "meta": {"_type": "gradio.FileData"},
                }
        except Exception:
            continue
    return None


def _generate_via_aihorde_cluster(
    en_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg_val: float,
    seed: int,
    is_nsfw: bool,
    style_lora_id: str,
    ref_image_1: str = "",
    horde_api_key: str = "0000000000",
) -> Optional[Tuple[bytes, str]]:
    """
    Cụm GPU Phân Tán AI Horde (Uncensored 18+ & Photorealistic SD/SDXL/Pony) tích hợp:
    - Sampler DPM++ 2M Karras (24 steps)
    - Khôi phục gương mặt & mắt thần kinh CodeFormers (facefixer_strength=0.78)
    - Phóng đại siêu phân giải thần kinh 2x trên GPU bằng RealESRGAN_x2plus (896×1408 / 1408×896)
    - Cơ chế kiểm tra trạng thái qua /api/v2/generate/check/{id} chống hoàn toàn lỗi HTTP 429 Too Many Requests.
    """
    global _HORDE_MODELS_CACHE
    headers = {
        "Content-Type": "application/json",
        "apikey": horde_api_key or "0000000000",
        "Client-Agent": "H3Studio:2.5:admin",
        "User-Agent": "H3Studio:2.5:admin",
    }

    now = time.time()
    avail_models = _HORDE_MODELS_CACHE.get("models") or []
    if not avail_models or (now - float(_HORDE_MODELS_CACHE.get("ts") or 0)) > 60:
        try:
            req_m = urllib.request.Request("https://stablehorde.net/api/v2/status/models?type=image", headers=headers)
            with urllib.request.urlopen(req_m, timeout=8) as rm:
                avail_models = json.loads(rm.read().decode("utf-8", errors="ignore"))
                _HORDE_MODELS_CACHE = {"ts": now, "models": avail_models}
        except Exception:
            avail_models = []

    fast_map = {
        m.get("name"): m
        for m in avail_models
        if isinstance(m, dict)
        and int(m.get("count") or 0) >= 1
        and int(m.get("eta") if m.get("eta") is not None else 999) <= 75
    }

    is_anime = "anime" in str(style_lora_id).lower() or "anime" in en_prompt.lower()
    if is_anime:
        preferred = [
            "WAI-ANI-NSFW-PONYXL",
            "Pony Diffusion XL",
            "BlenderMix Pony",
            "Rev Animated",
            "Dreamshaper",
            "Abyss OrangeMix",
            "Cetus-Mix",
            "Deliberate",
        ]
    else:
        preferred = [
            "Deliberate",
            "Realistic Vision",
            "ICBINP - I Can't Believe It's Not Photography",
            "CyberRealistic",
            "AbsoluteReality",
            "EpicRealism",
            "majicMIX realistic",
            "Analog Madness",
            "Dreamshaper",
            "NatViS",
            "NeverEnding Dream",
        ]

    online_preferred = [m for m in preferred if m in fast_map]
    online_preferred.sort(
        key=lambda m: (
            int(fast_map[m].get("eta") if fast_map[m].get("eta") is not None else 999),
            -int(fast_map[m].get("count") or 0),
        )
    )
    chosen_models = online_preferred[:5] if online_preferred else preferred[:4]

    ratio = float(width) / float(max(1, height))
    if horde_api_key and horde_api_key != "0000000000":
        gen_w = min(1024, (width // 64) * 64)
        gen_h = min(1024, (height // 64) * 64)
        eff_steps = min(30, max(22, int(steps)))
    else:
        # Giữ tổng điểm ảnh gốc <= 262,144 (kudos = 1.0) để được ưu tiên xử lý ngay (queue_position = 0, ~12-15 giây),
        # sau đó RealESRGAN_x2plus trên GPU sẽ nhân đôi độ phân giải thật lên 1280×768 / 768×1280 / 1024×1024!
        eff_steps = 20
        if ratio >= 1.45:
            gen_w, gen_h = 640, 384
        elif ratio <= 0.69:
            gen_w, gen_h = 384, 640
        elif ratio > 1.08:
            gen_w, gen_h = 576, 448
        elif ratio < 0.92:
            gen_w, gen_h = 448, 576
        else:
            gen_w, gen_h = 512, 512

    neg_prompt = (
        "blurry, out of focus, soft focus, lowres, low quality, worst quality, jpeg artifacts, "
        "deformed, distorted face, cross-eyed, asymmetric eyes, bad anatomy, extra limbs, "
        "extra fingers, mutated hands, plastic skin, airbrushed, doll, CGI, 3d render, "
        "watermark, text, signature, censored, mosaic, bar censor"
    )
    full_horde_prompt = f"{en_prompt} ### {neg_prompt}"

    # Thử chạy với bộ đôi thần kinh CodeFormers (khôi phục mắt/mặt) + RealESRGAN_x2plus (phóng nét 2x trên GPU)
    post_proc_candidates = [
        ["CodeFormers", "RealESRGAN_x2plus"],
        ["GFPGAN", "RealESRGAN_x2plus"],
    ]

    for post_procs in post_proc_candidates:
        payload: Dict[str, Any] = {
            "prompt": full_horde_prompt,
            "params": {
                "sampler_name": "k_dpmpp_2m",
                "cfg_scale": 6.5 if cfg_val <= 2.0 else min(8.0, float(cfg_val)),
                "width": gen_w,
                "height": gen_h,
                "steps": eff_steps,
                "karras": True,
                "facefixer_strength": 0.78,
                "post_processing": post_procs,
                "seed": str(seed),
                "n": 1,
            },
            "nsfw": True,
            "censor_nsfw": False,
            "trusted_workers": False,
            "models": chosen_models,
        }

        if ref_image_1:
            try:
                raw_ref = None
                if ref_image_1.startswith("data:image"):
                    raw_ref = base64.b64decode(ref_image_1.split(",", 1)[1])
                elif Path(ref_image_1).is_file():
                    raw_ref = Path(ref_image_1).read_bytes()
                if raw_ref:
                    resized_ref = _format_and_upscale_image_bytes(raw_ref, gen_w, gen_h)
                    payload["source_image"] = base64.b64encode(resized_ref).decode("ascii")
                    payload["source_processing"] = "img2img"
                    payload["params"]["denoising_strength"] = 0.58
            except Exception:
                pass

        try:
            req = urllib.request.Request(
                "https://stablehorde.net/api/v2/generate/async",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                job_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            job_id = job_data.get("id")
            if not job_id:
                continue

            # QUAN TRỌNG: Dùng /api/v2/generate/check/{job_id} để kiểm tra tiến độ (không bị chặn HTTP 429)
            is_done = False
            stalled_or_impossible = False
            for poll_idx in range(22):
                time.sleep(2.8)
                try:
                    chk_req = urllib.request.Request(
                        f"https://stablehorde.net/api/v2/generate/check/{job_id}",
                        headers=headers,
                    )
                    with urllib.request.urlopen(chk_req, timeout=10) as chk_resp:
                        chk_data = json.loads(chk_resp.read().decode("utf-8", errors="ignore"))
                except urllib.error.HTTPError as he:
                    if he.code == 429:
                        time.sleep(3.5)
                        continue
                    break
                if chk_data.get("done"):
                    is_done = True
                    break
                if (
                    chk_data.get("faulted")
                    or not chk_data.get("is_possible", True)
                    or int(chk_data.get("eligible_workers", 1)) == 0
                    or (poll_idx >= 2 and int(chk_data.get("wait_time") or 0) > 120 and int(chk_data.get("processing") or 0) == 0)
                ):
                    stalled_or_impossible = True
                    break

            if stalled_or_impossible:
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"https://stablehorde.net/api/v2/generate/status/{job_id}",
                            headers=headers,
                            method="DELETE",
                        ),
                        timeout=5,
                    )
                except Exception:
                    pass
                continue

            if not is_done:
                continue

            # Khi đã done=True, chỉ gọi /api/v2/generate/status/{job_id} ĐÚNG 1 LẦN để tải ảnh kết quả
            st_data = None
            for attempt in range(3):
                try:
                    st_req = urllib.request.Request(
                        f"https://stablehorde.net/api/v2/generate/status/{job_id}",
                        headers=headers,
                    )
                    with urllib.request.urlopen(st_req, timeout=15) as st_resp:
                        st_data = json.loads(st_resp.read().decode("utf-8", errors="ignore"))
                    break
                except urllib.error.HTTPError as he:
                    if he.code == 429 and attempt < 2:
                        time.sleep(5.0)
                        continue
                    break

            if st_data:
                gens = st_data.get("generations") or []
                if gens and gens[0].get("img"):
                    img_val = str(gens[0]["img"])
                    used_model = str(gens[0].get("model") or chosen_models[0])
                    if img_val.startswith("http"):
                        img_req = urllib.request.Request(
                            img_val,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                        )
                        with urllib.request.urlopen(img_req, timeout=25) as ir:
                            raw_img = ir.read()
                    else:
                        raw_img = base64.b64decode(img_val)
                    if raw_img and len(raw_img) > 4096:
                        png_bytes = _format_and_upscale_image_bytes(raw_img, width, height, crop_watermark_px=0)
                        return png_bytes, f"Uncensored 8K GPU ({used_model} + CodeFormers + RealESRGAN)"
        except Exception:
            continue
    return None


def _generate_via_flux_direct(en_prompt: str, width: int, height: int, seed: int, is_nsfw: bool = False) -> Optional[Tuple[bytes, str]]:
    """Cụm dự phòng siêu tốc Flux 14B Cloud Direct (chỉ nhận ảnh chất lượng cao >= 95KB, không dùng khi NSFW bị làm mờ)."""
    if is_nsfw:
        return None
    try:
        req_h = min(1024, max(768, height + 36))
        req_w = min(1024, max(768, width))
        q_prompt = urllib.parse.quote(en_prompt)
        url = f"https://image.pollinations.ai/prompt/{q_prompt}?width={req_w}&height={req_h}&seed={seed}&nologo=true&enhance=true&model=flux"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw_img = r.read()
        if raw_img and len(raw_img) > 95000:
            png_bytes = _format_and_upscale_image_bytes(raw_img, width, height, crop_watermark_px=34)
            return png_bytes, "Krea-Flux 14B Direct Cloud"
    except Exception:
        pass
    return None


def _wsl_path(p: str | Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def _run_wsl_cmd(cmd: str, timeout: int = 600) -> Tuple[int, str]:
    import subprocess
    try:
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", f"source ~/.local/bin/env 2>/dev/null; {cmd}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode, (r.stdout + "\n" + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "Timeout"
    except Exception as e:
        return -1, str(e)


def _generate_via_colab_gpu(
    en_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg_val: float,
    seed: int,
    base_checkpoint: str,
    lora_obj: Optional[Dict[str, Any]],
    lora_strength: float,
    mode: str = "text2image",
    ref_image_1: str = "",
    ref_image_2: str = "",
    colab_gpu: str = "L4",
) -> Tuple[Optional[bytes], str, str, str]:
    """
    Chạy trực tiếp mô hình Krea 2 Gốc (gonzalomoKrea2_v40 / pornmasterKrea2_v2TurboInt8 + Qwen3-VL-4B + Qwen Image VAE)
    trên máy ảo Google Colab GPU (L4 / A100 / T4) thông qua ComfyUI.
    Chi phí siêu tiết kiệm: ~0.0025 CU / ảnh trên GPU L4 (~400 ảnh / 1 CU), giữ nguyên phiên ấm h3_warm_*.
    """
    nb_path = SCRIPT_DIR / "assets" / "Krea2_Turbo_Colab.ipynb"
    runner_path = SCRIPT_DIR / "scripts" / "runner.py"
    mgr_path = SCRIPT_DIR / "scripts" / "account_manager.py"
    if not nb_path.is_file() or not runner_path.is_file():
        return None, "", "", "Không tìm thấy file Krea2_Turbo_Colab.ipynb hoặc runner.py"

    # Tìm tài khoản Colab có nhiều CU nhất
    acc_email = ""
    code_l, out_l = _run_wsl_cmd(f"python3 '{_wsl_path(mgr_path)}' list", timeout=15)
    if code_l == 0 and out_l:
        try:
            accs = json.loads(out_l)
            funded = []
            for a in (accs if isinstance(accs, list) else []):
                em = a.get("email")
                bal = a.get("balance")
                try:
                    bf = float(str(bal).split()[0]) if bal is not None else 0.0
                except Exception:
                    bf = 0.0
                if em and bf > 0.05:
                    funded.append((1 if a.get("selected") else 0, bf, em))
            funded.sort(reverse=True)
            if funded:
                acc_email = funded[0][2]
            elif accs and isinstance(accs, list) and accs[0].get("email"):
                acc_email = accs[0]["email"]
        except Exception:
            pass

    safe_acc = re.sub(r"[^a-zA-Z0-9_]", "_", acc_email or "default")
    tok_env = f"COLAB_TOKEN_PATH='/root/.config/colab-cli/tokens/{acc_email}.json' " if acc_email else ""
    session = f"h3_warm_{safe_acc}"
    gpu_choice = (colab_gpu or "A100").strip().upper()
    if gpu_choice not in ("L4", "A100", "T4", "V100"):
        gpu_choice = "A100"

    # 1. Khởi tạo hoặc tái sử dụng phiên ấm trên Colab
    c_start, o_start = _run_wsl_cmd(
        f"{tok_env}python3 '{_wsl_path(runner_path)}' start --session '{session}' --gpu '{gpu_choice}'",
        timeout=300,
    )
    if c_start != 0:
        alt_gpu = "L4" if gpu_choice == "A100" else "A100"
        gpu_choice = alt_gpu
        c_start, o_start = _run_wsl_cmd(
            f"{tok_env}python3 '{_wsl_path(runner_path)}' start --session '{session}' --gpu '{gpu_choice}'",
            timeout=300,
        )
    if c_start != 0:
        return None, "", "", f"Không thể mở phiên Colab GPU ({o_start[-200:]})"

    td = SCRIPT_DIR / "temp"
    td.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    loc_prompt = td / f"krea2_prompt_{uid}.txt"
    loc_out = td / f"krea2_out_{uid}.png"
    loc_ref1 = td / f"krea2_ref1_{uid}.png"
    loc_ref2 = td / f"krea2_ref2_{uid}.png"

    try:
        loc_prompt.write_text(en_prompt, encoding="utf-8")
        rem_prompt = f"/content/krea2_prompt_{uid}.txt"
        rem_out = f"/content/krea2_out_{uid}.png"
        rem_ref1 = ""
        rem_ref2 = ""

        c_up, o_up = _run_wsl_cmd(
            f"{tok_env}colab --auth=oauth2 --config='/root/.config/colab-cli/tokens/sessions_{acc_email}.json' upload --session '{session}' '{_wsl_path(loc_prompt)}' '{rem_prompt}'"
            if acc_email
            else f"colab --auth=oauth2 upload --session '{session}' '{_wsl_path(loc_prompt)}' '{rem_prompt}'",
            timeout=60,
        )
        if c_up != 0:
            return None, "", "", f"Upload prompt lên Colab thất bại: {o_up[-160:]}"

        cfg_arg = f"--config='/root/.config/colab-cli/tokens/sessions_{acc_email}.json'" if acc_email else ""

        if mode == "edit" and ref_image_1:
            raw_r1 = None
            if ref_image_1.startswith("data:image"):
                raw_r1 = base64.b64decode(ref_image_1.split(",", 1)[1])
            elif Path(ref_image_1).is_file():
                raw_r1 = Path(ref_image_1).read_bytes()
            if raw_r1:
                loc_ref1.write_bytes(raw_r1)
                rem_ref1 = f"/content/krea2_ref1_{uid}.png"
                _run_wsl_cmd(
                    f"{tok_env}colab --auth=oauth2 {cfg_arg} upload --session '{session}' '{_wsl_path(loc_ref1)}' '{rem_ref1}'",
                    timeout=90,
                )

        if mode == "edit" and ref_image_2:
            raw_r2 = None
            if ref_image_2.startswith("data:image"):
                raw_r2 = base64.b64decode(ref_image_2.split(",", 1)[1])
            elif Path(ref_image_2).is_file():
                raw_r2 = Path(ref_image_2).read_bytes()
            if raw_r2:
                loc_ref2.write_bytes(raw_r2)
                rem_ref2 = f"/content/krea2_ref2_{uid}.png"
                _run_wsl_cmd(
                    f"{tok_env}colab --auth=oauth2 {cfg_arg} upload --session '{session}' '{_wsl_path(loc_ref2)}' '{rem_ref2}'",
                    timeout=90,
                )

        lora_repo = (lora_obj.get("repo_id") if lora_obj else "") or ""
        lora_file = (lora_obj.get("filename") if lora_obj else "") or ""

        env_flags = " ".join([
            f"--env 'KREA2_MODE={mode}'",
            f"--env 'KREA2_CHECKPOINT={base_checkpoint}'",
            f"--env 'KREA2_PROMPT_FILE={rem_prompt}'",
            f"--env 'KREA2_OUTPUT_PATH={rem_out}'",
            f"--env 'KREA2_WIDTH={width}'",
            f"--env 'KREA2_HEIGHT={height}'",
            f"--env 'KREA2_STEPS={max(8, steps)}'",
            f"--env 'KREA2_CFG={cfg_val}'",
            f"--env 'KREA2_SEED={seed}'",
            f"--env 'KREA2_LORA_REPO={lora_repo}'",
            f"--env 'KREA2_LORA_FILE={lora_file}'",
            f"--env 'KREA2_LORA_STRENGTH={lora_strength}'",
            f"--env 'KREA2_REF1={rem_ref1}'",
            f"--env 'KREA2_REF2={rem_ref2}'",
        ])

        c_ex, o_ex = _run_wsl_cmd(
            f"{tok_env}colab --auth=oauth2 {cfg_arg} exec --session '{session}' --timeout 600 {env_flags} --file '{_wsl_path(nb_path)}'",
            timeout=620,
        )
        if c_ex != 0:
            return None, "", "", f"Colab Krea 2 exec lỗi: {o_ex[-240:]}"

        c_dl, o_dl = _run_wsl_cmd(
            f"{tok_env}colab --auth=oauth2 {cfg_arg} download --session '{session}' '{rem_out}' '{_wsl_path(loc_out)}'",
            timeout=120,
        )
        if c_dl == 0 and loc_out.is_file() and loc_out.stat().st_size > 4096:
            raw_out = loc_out.read_bytes()
            png_bytes = _format_and_upscale_image_bytes(raw_out, width, height, crop_watermark_px=0)
            short_ckpt = base_checkpoint.replace(".safetensors", "")
            is_moss_ckpt = str(base_checkpoint).startswith("moss_")
            eng_tag = (
                f"Colab GPU {gpu_choice} • {'MOSS-Image-Edit LoRA' if (is_moss_ckpt and mode == 'edit') else ('MOSS Image' if is_moss_ckpt else 'Krea 2 Gốc')} ({short_ckpt})"
            )
            return (
                png_bytes,
                eng_tag,
                f"colab://{acc_email or 'default'}/{session}",
                "",
            )
        return None, "", "", f"Tải ảnh từ Colab thất bại: {o_dl[-180:]}"
    finally:
        for fp in (loc_prompt, loc_out, loc_ref1, loc_ref2):
            try:
                fp.unlink(missing_ok=True)
            except Exception:
                pass


def generate_image_with_krea2(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 8,
    cfg: float = 1.0,
    seed: int = -1,
    base_checkpoint: str = "gonzalomoKrea2_v40.safetensors",
    style_lora_id: str = "none",
    lora_strength: float = 0.85,
    custom_trigger_word: str = "",
    mode: str = "text2image",
    edit_instruction: str = "",
    ref_image_1: str = "",
    ref_image_2: str = "",
    output_path: Optional[Path] = None,
    compute_mode: str = "colab_gpu",
    colab_gpu: str = "A100",
) -> Dict[str, Any]:
    """
    Sinh ảnh chất lượng cao bằng Hệ thống Đa Cụm GPU:
    - compute_mode == 'colab_gpu': Chạy trực tiếp mô hình Krea 2 gốc (gonzalomoKrea2_v40 / pornmasterKrea2_v2TurboInt8 + Qwen3-VL-4B)
      trên máy ảo Google Colab GPU (L4/A100) của người dùng (~0.0025 CU/ảnh, ~400 ảnh/1 CU). Không giới hạn IP, không kiểm duyệt, chuẩn 100% Civitai!
    - compute_mode == 'free_cloud': Chạy trên Cụm Cloud Miễn Phí (0.00 CU Colab): HuggingFace ZeroGPU + AI Horde 8K (CodeFormers + RealESRGAN 2x).
    """
    global _ZEROGPU_COOLDOWN_UNTIL
    raw_user_prompt = (prompt or "").strip()
    if not raw_user_prompt:
        return {"ok": False, "error": "Prompt tạo ảnh Krea 2 không được để trống!"}

    lora_obj = next((x for x in KREA2_OFFICIAL_STYLE_LORAS if x["id"] == style_lora_id or x["filename"] == style_lora_id), None)
    trig = (custom_trigger_word or (lora_obj.get("trigger_word") if lora_obj else "") or "").strip()

    clean_prompt, is_nsfw = translate_and_enhance_image_prompt(
        raw_prompt=raw_user_prompt,
        base_checkpoint=base_checkpoint,
        trigger_word=trig,
    )

    eff_seed = int(seed) if (seed is not None and int(seed) >= 0) else random.randint(1, 2**31 - 1)
    w = max(512, min(2048, int(width or 1024)))
    h = max(512, min(2048, int(height or 1024)))
    st = max(4, min(28, int(steps or 8)))
    cfg_val = max(1.0, min(7.0, float(cfg or 1.0)))

    ckpt_info = next((c for c in KREA2_BASE_CHECKPOINTS if c["id"] == base_checkpoint), KREA2_BASE_CHECKPOINTS[0])
    style_boost = str(ckpt_info.get("style_prompt_boost") or "").strip()
    if style_boost and style_boost.lower() not in clean_prompt.lower() and mode != "edit":
        clean_prompt = f"{clean_prompt}, {style_boost}"
    krea_cfg = load_krea2_config()
    hf_token = krea_cfg.get("hf_token", "")
    horde_key = krea_cfg.get("horde_api_key", "0000000000")

    img_bytes: Optional[bytes] = None
    used_worker = ""
    used_engine_label = "Krea 2 Turbo"
    last_err = ""
    # BẮT BUỘC 100% CHẠY TRỰC TIẾP TRÊN GOOGLE COLAB GPU (KREA 2 GỐC CHUẨN CIVITAI)
    eff_compute = "colab_gpu"
    if True:
        eff_ckpt = "pornmasterKrea2_v2TurboInt8.safetensors" if (is_nsfw and base_checkpoint == "comfy_org_krea2_turbo_fp8") else base_checkpoint
        c_bytes, c_label, c_worker, c_err = _generate_via_colab_gpu(
            en_prompt=(edit_instruction or clean_prompt) if (mode == "edit" and ref_image_1) else clean_prompt,
            width=w,
            height=h,
            steps=st,
            cfg_val=cfg_val,
            seed=eff_seed,
            base_checkpoint=eff_ckpt,
            lora_obj=lora_obj,
            lora_strength=lora_strength,
            mode="edit" if (mode == "edit" and ref_image_1) else "text2image",
            ref_image_1=ref_image_1,
            ref_image_2=ref_image_2,
            colab_gpu=colab_gpu or "A100",
        )
        if c_bytes:
            img_bytes = c_bytes
            used_engine_label = c_label
            used_worker = c_worker
        else:
            return {
                "ok": False,
                "error": f"Colab GPU báo lỗi khi tạo ảnh Krea 2: {c_err}",
            }

    # CỤM 1: KREA 2 TURBO TRÊN HUGGINGFACE SPACES (Nếu có HF_TOKEN hoặc chưa chạm ngưỡng cooldown ZeroGPU)
    now = time.time()
    if not img_bytes and (hf_token or now >= _ZEROGPU_COOLDOWN_UNTIL):
        req_headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        if hf_token:
            req_headers["Authorization"] = f"Bearer {hf_token}"

        for host in DEFAULT_KREA2_SPACES:
            try:
                info_url = f"{host.rstrip('/')}/gradio_api/info"
                req_info = urllib.request.Request(info_url, headers=req_headers)
                with urllib.request.urlopen(req_info, timeout=8) as r_info:
                    info_data = json.loads(r_info.read().decode("utf-8", errors="ignore"))
                named_eps = info_data.get("named_endpoints") or {}

                if "/_generate_wrapper" in named_eps:
                    ep_name = "/_generate_wrapper"
                    fn_idx = 4
                    param_defs = named_eps[ep_name].get("parameters") or []
                    args = [p.get("parameter_default") for p in param_defs]
                    eff_mode = "edit" if (mode == "edit" and ref_image_1) else "text2image"
                    args[0] = eff_mode
                    args[1] = clean_prompt
                    args[2] = (edit_instruction or clean_prompt) if eff_mode == "edit" else None
                    args[3] = _upload_local_image_to_gradio_space(host, ref_image_1, hf_token) if ref_image_1 else None
                    args[4] = _upload_local_image_to_gradio_space(host, ref_image_2, hf_token) if ref_image_2 else None
                    args[5] = float(w)
                    args[6] = float(h)
                    args[7] = round((w * h) / (1024.0 * 1024.0), 2)
                    args[8] = 768.0
                    args[9] = 1.0
                    args[10] = 1.0
                    args[11] = float(max(8, st))
                    args[12] = float(cfg_val)
                    args[13] = "euler"
                    args[14] = "simple"
                    args[15] = eff_seed
                    args[16] = False
                    if ckpt_info.get("hf_repo") and ckpt_info.get("hf_file"):
                        args[18] = "__custom_huggingface_base_model__"
                        args[19] = ckpt_info["hf_repo"]
                        args[20] = ckpt_info["hf_file"]
                        args[21] = None
                    else:
                        args[18] = "pornmasterKrea2_v2TurboInt8.safetensors" if is_nsfw else ckpt_info["id"]
                        args[19] = None
                        args[20] = None
                        args[21] = None
                    if lora_obj and lora_obj.get("repo_id") and lora_obj.get("filename"):
                        args[22] = {
                            "headers": ["repo_id", "filename", "revision", "weight"],
                            "data": [[lora_obj["repo_id"], lora_obj["filename"], "", float(lora_strength or 0.85)]],
                            "metadata": None,
                        }
                elif "/generate_image" in named_eps:
                    ep_name = "/generate_image"
                    fn_idx = 2
                    param_defs = named_eps[ep_name].get("parameters") or []
                    if len(param_defs) >= 14:
                        args = [
                            clean_prompt, int(w), int(h), int(eff_seed), int(max(8, st)), float(cfg_val),
                            "Euler", False, "krea2_darkbrush.safetensors", 0.8, "", False, False, 512
                        ]
                    else:
                        args = [clean_prompt, int(w), int(h), int(eff_seed), int(max(8, st)), float(cfg_val)]
                elif "/generate" in named_eps:
                    ep_name = "/generate"
                    fn_idx = 2
                    args = [clean_prompt, "blurry, low quality, deformed", "Turbo", int(max(8, st)), 0.0, int(w), int(h), int(eff_seed), False]
                else:
                    continue

                call_ep = ep_name.lstrip("/")
                call_req = urllib.request.Request(
                    f"{host.rstrip('/')}/gradio_api/call/{call_ep}",
                    data=json.dumps({"data": args}).encode("utf-8"),
                    headers=req_headers,
                    method="POST",
                )
                with urllib.request.urlopen(call_req, timeout=10) as cr:
                    call_json = json.loads(cr.read().decode("utf-8", errors="ignore"))
                ev_id = call_json.get("event_id")
                if not ev_id:
                    continue

                stream_req = urllib.request.Request(
                    f"{host.rstrip('/')}/gradio_api/call/{call_ep}/{ev_id}",
                    headers=req_headers,
                )
                img_remote_url = ""
                hit_zerogpu_limit = False
                curr_event = ""
                with urllib.request.urlopen(stream_req, timeout=45) as stream_resp:
                    for raw_line in stream_resp:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if line.startswith("event:"):
                            curr_event = line[6:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if curr_event == "error" and (not data_str or data_str == "null"):
                            last_err = "ZeroGPU quota limit"
                            hit_zerogpu_limit = True
                            break
                        if not data_str or data_str == "null":
                            continue
                        ev = json.loads(data_str)
                        if curr_event == "error" or (isinstance(ev, dict) and ev.get("error")):
                            err_txt = str(ev.get("error") or ev.get("title") or "Gradio error") if isinstance(ev, dict) else str(ev)
                            last_err = err_txt
                            if "zerogpu" in err_txt.lower() or "quota" in err_txt.lower() or "exceeded" in err_txt.lower():
                                hit_zerogpu_limit = True
                            break
                        if curr_event == "complete" and isinstance(ev, list) and ev:
                            first_out = ev[0]
                            if isinstance(first_out, list) and first_out:
                                item0 = first_out[0]
                                img_obj = item0.get("image") if isinstance(item0, dict) and "image" in item0 else item0
                            else:
                                img_obj = first_out
                            if isinstance(img_obj, dict):
                                if img_obj.get("url"):
                                    img_remote_url = img_obj["url"]
                                elif img_obj.get("path"):
                                    img_remote_url = f"{host.rstrip('/')}/gradio_api/file={img_obj['path']}"
                            break

                if hit_zerogpu_limit and not hf_token:
                    _ZEROGPU_COOLDOWN_UNTIL = time.time() + 900.0
                    break

                if not img_remote_url:
                    continue

                img_req = urllib.request.Request(img_remote_url, headers=req_headers)
                with urllib.request.urlopen(img_req, timeout=30) as img_resp:
                    cand_bytes = img_resp.read()
                if cand_bytes and len(cand_bytes) > 4096:
                    img_bytes = _format_and_upscale_image_bytes(cand_bytes, w, h, crop_watermark_px=0)
                    used_worker = host
                    used_engine_label = "Krea 2 Turbo 8K (ZeroGPU)"
                    break
            except Exception as e:
                last_err = f"{host}: {e}"
                continue

    # CỤM 2: CỤM GPU PHÂN TÁN UNCENSORED 8K (AI HORDE + CODEFORMERS + REALESRGAN 2X)
    if not img_bytes:
        horde_res = _generate_via_aihorde_cluster(
            en_prompt=clean_prompt,
            width=w,
            height=h,
            steps=st,
            cfg_val=cfg_val,
            seed=eff_seed,
            is_nsfw=is_nsfw,
            style_lora_id=style_lora_id,
            ref_image_1=ref_image_1 if mode == "edit" else "",
            horde_api_key=horde_key,
        )
        if horde_res:
            img_bytes, used_engine_label = horde_res
            used_worker = "https://stablehorde.net"

    # CỤM 3: CỤM FLUX 14B CLOUD DIRECT (Dự phòng cho ảnh SFW)
    if not img_bytes:
        flux_res = _generate_via_flux_direct(
            en_prompt=clean_prompt,
            width=w,
            height=h,
            seed=eff_seed,
            is_nsfw=is_nsfw,
        )
        if flux_res:
            img_bytes, used_engine_label = flux_res
            used_worker = "https://image.pollinations.ai"

    if not img_bytes:
        return {
            "ok": False,
            "error": f"Tất cả cụm GPU tạo ảnh đang bận ({last_err}). Bạn có thể dán thêm HF_TOKEN hoặc thử lại sau vài giây!",
        }

    is_moss_model = str(base_checkpoint).startswith("moss_")
    if output_path is None:
        prefix_fn = "MOSS" if is_moss_model else "Krea2"
        fname = f"{prefix_fn}_{int(time.time())}_{uuid.uuid4().hex[:5]}.png"
        out_file = KREA2_OUTPUT_DIR / fname
    else:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(img_bytes)

    # Lưu kèm file .civitai.info chuẩn Civitai Helper
    civitai_info = {
        "model": used_engine_label,
        "baseModel": "MOSS-Image" if is_moss_model else "Krea 2 Turbo",
        "checkpoint": ckpt_info["name"],
        "checkpointId": ckpt_info["id"],
        "styleLora": lora_obj["name"] if lora_obj else "None",
        "trainedWords": [trig] if trig else [],
        "rawPrompt": raw_user_prompt,
        "prompt": clean_prompt,
        "nsfw": is_nsfw,
        "width": w,
        "height": h,
        "steps": st,
        "cfg": cfg_val,
        "sampler": "euler",
        "scheduler": "simple",
        "seed": eff_seed,
        "workerHost": used_worker,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        info_path = out_file.with_suffix(".civitai.info")
        info_path.write_text(json.dumps(civitai_info, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    b64_str = base64.b64encode(img_bytes).decode("ascii")
    return {
        "ok": True,
        "engine": used_engine_label,
        "worker": used_worker,
        "checkpoint": ckpt_info["name"],
        "style_lora": lora_obj["name"] if lora_obj else "None",
        "trigger_word": trig,
        "raw_prompt": raw_user_prompt,
        "prompt": clean_prompt,
        "is_nsfw": is_nsfw,
        "seed": eff_seed,
        "width": w,
        "height": h,
        "name": out_file.name,
        "filename": out_file.name,
        "path": str(out_file).replace("\\", "/"),
        "url": f"/api/local-image?path={urllib.parse.quote(str(out_file).replace(chr(92), '/'))}",
        "data": f"data:image/png;base64,{b64_str}",
        "data_uri": f"data:image/png;base64,{b64_str}",
        "civitai_info": civitai_info,
    }


def build_comfyui_krea2_workflow_json(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 8,
    cfg: float = 1.0,
    seed: int = -1,
    unet_name: str = "krea2_turbo_fp8_scaled.safetensors",
    clip_name: str = "qwen3vl_4b_fp8_scaled.safetensors",
    vae_name: str = "qwen_image_vae.safetensors",
    lora_name: str = "",
    lora_strength: float = 1.0,
    base_checkpoint: str = "",
    mode: str = "text2image",
) -> Dict[str, Any]:
    eff_seed = int(seed) if (seed is not None and int(seed) >= 0) else random.randint(1, 2**31 - 1)
    is_moss = str(base_checkpoint).startswith("moss_") or "kromoss" in str(unet_name).lower() or "moss" in str(unet_name).lower()
    if is_moss:
        ckpt_obj = next((c for c in MOSS_BASE_CHECKPOINTS if c["id"] == base_checkpoint), MOSS_BASE_CHECKPOINTS[0])
        moss_unet = Path(ckpt_obj.get("hf_file") or "kromoss_int8_convrot.safetensors").name
        if mode == "edit":
            return {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": moss_unet, "weight_dtype": "default"}},
                "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3.5_9b_bf16.safetensors", "type": "moss_image", "device": "default"}},
                "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae_small.safetensors"}},
                "4": {"class_type": "ModelPatchLoader", "inputs": {"name": "moss_image_edit.safetensors"}},
                "5": {"class_type": "LoadImage", "inputs": {"image": "reference_input.png"}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
                "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
                "8": {
                    "class_type": "ApplyMossImageEdit",
                    "inputs": {
                        "model": ["1", 0],
                        "model_patch": ["4", 0],
                        "vae": ["3", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "image": ["5", 0],
                        "reference_max_edge": 512,
                        "strength": 1.0,
                    },
                },
                "9": {
                    "class_type": "SkipLayerGuidanceDiTSimple",
                    "inputs": {
                        "model": ["8", 0],
                        "double_layers": "",
                        "single_layers": "22",
                        "scale": 3.0,
                        "start_percent": 0.0,
                        "end_percent": 1.0,
                    },
                },
                "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": int(width or 1024), "height": int(height or 1024), "batch_size": 1}},
                "11": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": eff_seed,
                        "steps": max(16, int(steps or 20)),
                        "cfg": max(3.5, float(cfg or 4.0)),
                        "sampler_name": "euler",
                        "scheduler": "normal",
                        "denoise": 1.0,
                        "model": ["9", 0],
                        "positive": ["8", 1],
                        "negative": ["8", 2],
                        "latent_image": ["10", 0],
                    },
                },
                "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
                "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": "MOSS_Edit", "images": ["12", 0]}},
            }
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": moss_unet, "weight_dtype": "default"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "8-steps-dmd2-lora.safetensors", "strength_model": 1.0}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3.5_9b_bf16.safetensors", "type": "moss_image", "device": "default"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae_small.safetensors"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["3", 0]}},
            "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "7": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": int(width or 1024), "height": int(height or 1024), "batch_size": 1}},
            "8": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": eff_seed,
                    "steps": int(steps or 8),
                    "cfg": float(cfg or 1.0),
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["2", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                },
            },
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "MOSS_Image", "images": ["9", 0]}},
        }
    """
    Xuất cấu trúc ComfyUI API Prompt JSON chuẩn chính thức Comfy-Org/Krea-2:
    - UNETLoader (krea2_turbo_fp8_scaled.safetensors)
    - CLIPLoader (qwen3vl_4b_fp8_scaled.safetensors, type='krea2')
    - VAELoader (qwen_image_vae.safetensors)
    - KSampler (8 steps, cfg=1.0, sampler='euler', scheduler='simple')
    """
    eff_seed = int(seed) if (seed is not None and int(seed) >= 0) else random.randint(1, 2**31 - 1)
    model_source = ["10", 0]
    nodes: Dict[str, Any] = {
        "10": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": unet_name,
                "weight_dtype": "default",
            },
        },
        "11": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": clip_name,
                "type": "krea2",
                "device": "default",
            },
        },
        "12": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": vae_name,
            },
        },
    }
    if lora_name and lora_name != "none":
        clean_lora = Path(lora_name).name
        nodes["15"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["10", 0],
                "lora_name": clean_lora,
                "strength_model": float(lora_strength or 1.0),
            },
        }
        model_source = ["15", 0]

    nodes.update({
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["11", 0],
            },
        },
        "13": {
            "class_type": "ConditioningZeroOut",
            "inputs": {
                "conditioning": ["6", 0],
            },
        },
        "5": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": int(width or 1024),
                "height": int(height or 1024),
                "batch_size": 1,
            },
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": eff_seed,
                "steps": int(steps or 8),
                "cfg": float(cfg or 1.0),
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": model_source,
                "positive": ["6", 0],
                "negative": ["13", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["12", 0],
            },
        },
        "29": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "Krea2_Turbo",
                "images": ["8", 0],
            },
        },
    })
    return nodes
