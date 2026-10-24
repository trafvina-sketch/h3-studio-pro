# -*- coding: utf-8 -*-
"""
Bộ Phân Tích Kịch Bản AI & Biên Dịch Chuẩn MiniMax H3 (Gemini API Key + Gemini Cookie + Smart Fallback)
Hỗ trợ:
1. Tích hợp trực tiếp Google Gemini API Key chính thức (generativelanguage.googleapis.com)
2. Tích hợp Gemini Cookie API nội bộ (127.0.0.1:4981/openai/v1)
3. Bộ biên dịch thông minh Offline (Smart Compiler) đảm bảo hoạt động 100% mọi lúc
4. Sinh song song:
   - Danh sách Nhân Vật / Bối Cảnh Tham Chiếu (<Picture 1>..<Picture N>) kèm Prompt Tạo Ảnh Krea 2
   - Danh sách Phân Cảnh (Cảnh 1..Cảnh N) theo đúng Thời gian mỗi cảnh & Tổng thời gian kịch bản,
     với Prompt Chuẩn MiniMax H3 (subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

STYLE_PRESETS = {
    "xianxia_live": {
        "name_vi": "⚔️ Tiên Hiệp / Cổ Trang Live-Action 8K",
        "style_en": (
            "Ultra-photorealistic 8K live-action Chinese Xianxia fantasy cinema, "
            "natural skin texture with realistic pores, flowing silk hanfu robes, "
            "ethereal celestial clouds and golden sunlight volumetric rays, ARRI Alexa 65 anamorphic lens"
        ),
        "img_prefix_en": "Ultra-photorealistic 8K live-action Xianxia character reference portrait, studio-grade natural celestial lighting, sharp focus on face and upper body, ",
        "music_en": "Ethereal Chinese guzheng, bamboo flute (dizi), and warm cinematic orchestral strings.",
    },
    "anime_2d_fantasy": {
        "name_vi": "🌸 Hoạt Hình 2D Fantasy Anime (Liền mạch)",
        "style_en": (
            "High-budget 2D Japanese fantasy anime film style (Ufotable and Makoto Shinkai aesthetic), "
            "crisp hand-drawn line art, vibrant cel-shaded colors, glowing magical particle effects, "
            "expressive anime eyes, dynamic sakuga fluid animation, painterly fantasy backgrounds"
        ),
        "img_prefix_en": "Masterpiece 2D fantasy anime character reference sheet portrait (Ufotable / Makoto Shinkai style), clean crisp line art, vibrant cel-shading, expressive anime face and signature outfit, ",
        "music_en": "Sweeping emotional anime orchestral soundtrack with piano, violin, and celesta.",
    },
    "donghua_3d": {
        "name_vi": "🐉 Hoạt Hình 3D Tu Tiên CG (Donghua UE5)",
        "style_en": (
            "Ultra-detailed 3D Chinese Donghua CG animation rendered in Unreal Engine 5, "
            "delicate subsurface scattering porcelain skin, intricate embroidered silk robes and hair physics, "
            "epic martial arts spiritual aura effects, ray-traced cinematic lighting"
        ),
        "img_prefix_en": "Masterpiece 3D Donghua CG character portrait rendered in Unreal Engine 5, delicate porcelain skin, intricate hair strands and ornate silk cultivation robes, ",
        "music_en": "Epic 3D Donghua orchestral battle and cultivation theme with traditional drums and flute.",
    },
    "pixar_3d": {
        "name_vi": "🧸 Hoạt Hình 3D Pixar / Disney CG",
        "style_en": (
            "High-end 3D Pixar and Disney feature animation style, expressive stylized proportions, "
            "warm global illumination, rich fabric and hair grooming textures, smooth appealing character acting"
        ),
        "img_prefix_en": "High-end 3D Pixar/Disney animated movie character reference portrait, appealing expressive eyes, soft cinematic rim lighting, rich textures, ",
        "music_en": "Warm whimsical orchestral Pixar-style adventure score.",
    },
    "cinema_8k": {
        "name_vi": "🎬 Điện Ảnh Hiện Đại Hollywood 8K",
        "style_en": (
            "Ultra-photorealistic 8K Hollywood live-action cinema shot on ARRI Alexa 65 with 35mm Master Prime lens, "
            "natural skin texture, shallow depth of field, rich cinematic color grading, realistic physical motion"
        ),
        "img_prefix_en": "Ultra-photorealistic 8K raw cinematic portrait photo shot on 85mm f/1.4 lens, natural skin pores, realistic lighting, ",
        "music_en": "Subtle modern cinematic ambient score.",
    },
    "cyberpunk_scifi": {
        "name_vi": "🤖 Cyberpunk / Sci-Fi Điện Ảnh 8K",
        "style_en": (
            "Ultra-photorealistic 8K Cyberpunk Sci-Fi live-action cinema, neon-drenched rain reflections, "
            "futuristic high-tech visor and cybernetic attire, volumetric fog, anamorphic lens flares"
        ),
        "img_prefix_en": "Ultra-photorealistic 8K Cyberpunk Sci-Fi character reference portrait, neon rim lighting, detailed futuristic outfit, sharp focus, ",
        "music_en": "Atmospheric dark synthwave and futuristic sci-fi pulse.",
    },
}


def _extract_json_obj(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        cand = text[start : end + 1]
        try:
            return json.loads(cand)
        except Exception:
            cleaned = re.sub(r",\s*([\}\]])", r"\1", cand)
            return json.loads(cleaned)
    raise ValueError("Phản hồi không chứa JSON hợp lệ")


def call_google_gemini_api_key(prompt_text: str, api_key: str, preferred_model: str = "gemini-2.5-flash") -> tuple[dict | None, str]:
    """
    Gọi trực tiếp Google Gemini REST API chính thức bằng API Key (AIzaSy...).
    Tự động thử qua danh sách model ổn định nếu model được chọn không khả dụng.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return None, "Chưa nhập Gemini API Key"

    models_to_try = []
    if preferred_model and not preferred_model.startswith("gemini-3.") and not preferred_model.startswith("gemini-4"):
        models_to_try.append(preferred_model.strip())
    for m in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"):
        if m not in models_to_try:
            models_to_try.append(m)

    last_err = ""
    for m_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{" role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": 0.45,
                "responseMimeType": "application/json",
            },
        }
        # Sửa key "role" chuẩn
        payload["contents"] = [{"role": "user", "parts": [{"text": prompt_text}]}]
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            candidates = resp_data.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                raw_txt = "".join(str(p.get("text") or "") for p in parts)
                parsed = _extract_json_obj(raw_txt)
                if isinstance(parsed, dict):
                    return parsed, f"Google Gemini API ({m_name})"
        except urllib.error.HTTPError as he:
            try:
                err_body = he.read().decode("utf-8", errors="ignore")
                err_j = json.loads(err_body)
                msg = (err_j.get("error") or {}).get("message") or str(he)
            except Exception:
                msg = str(he)
            last_err = f"{m_name}: {msg}"
            # Nếu API key sai hoàn toàn (API_KEY_INVALID) thì dừng luôn
            if "API_KEY_INVALID" in last_err or he.code in (400, 403) and "key" in last_err.lower():
                break
        except Exception as e:
            last_err = f"{m_name}: {e}"
            continue

    return None, last_err or "Không gọi được Google Gemini API"


def _ensure_h3_contract_format(
    raw_prompt: str,
    references: list[dict],
    used_tags: list[str],
    style_info: dict,
    scene_idx: int,
    total_scenes: int,
    start_sec: int,
    end_sec: int,
    scene_seconds: int,
    summary_en: str,
    aspect: str,
) -> str:
    """Đảm bảo prompt của mỗi cảnh luôn có đầy đủ 6 phần chuẩn MiniMax H3 Reference Contract."""
    txt = (raw_prompt or "").strip()
    if "subject_definitions:" in txt and "detailed_description:" in txt:
        return txt

    ref_map = {r["tag"]: r for r in references}
    active_tags = [t for t in used_tags if t in ref_map] or ([references[0]["tag"]] if references else ["<Picture 1>"])

    subj_lines = []
    for t in active_tags:
        r_obj = ref_map.get(t)
        desc = (r_obj.get("subject_def_en") if r_obj else "") or "Main character with consistent facial identity, hairstyle, and signature outfit."
        subj_lines.append(f"- {t}: {desc}")
    subj_block = "\n".join(subj_lines)

    half_s = max(2, scene_seconds // 2)
    continuity_note = (
        f"Seamless continuation from Scene {scene_idx - 1}, preserving exact lighting, environment, and character positioning. "
        if scene_idx > 1
        else "Opening establishing shot introducing the main subject and atmosphere. "
    )

    # Nếu txt chưa có [Shot 1] thì bọc thành [Shot 1] & [Shot 2]
    if "[Shot 1]" not in txt:
        shot_body = (
            f"[Shot 1] (0s-{half_s}s) {continuity_note}{style_info['style_en']}. "
            f"{txt} "
            f"[Shot 2] ({half_s}s-{scene_seconds}s) Smooth camera progression holding focus on {', '.join(active_tags)}, "
            f"natural micro-expressions, realistic cloth and hair movement, ending in a stable pose ready for the next continuous scene."
        )
    else:
        shot_body = f"{continuity_note}{style_info['style_en']}. {txt}"

    tags_joined = ", ".join(active_tags)
    return (
        f"subject_definitions:\n{subj_block}\n\n"
        f"summary: [reference generation] {style_info['style_en']}. Scene {scene_idx}/{total_scenes} ({start_sec}s-{end_sec}s, {aspect}): {summary_en or 'Continuous cinematic sequence.'}\n\n"
        f"retention_analysis: Strictly preserve the exact facial structure, eye color, hairstyle, and signature outfit of {tags_joined} from the reference image(s) without any morphing or wardrobe drift across Scene {scene_idx}.\n\n"
        f"detailed_description: {shot_body}\n\n"
        f"overall_soundscape: Synchronized realistic environmental foley, subtle footsteps, cloth rustle, atmospheric ambience, and crystal-clear vocal lip-sync if dialogue is spoken.\n\n"
        f"non_diegetic_music: {style_info['music_en']}"
    )


def _smart_local_script_compiler(
    script_text: str,
    style_key: str,
    custom_style: str,
    scene_seconds: int,
    total_seconds: int,
    aspect: str,
    dialogue_mode: str,
    camera_pov: str,
) -> dict:
    """
    Bộ phân tích kịch bản dự phòng thông minh (khi người dùng chưa nhập API Key hoặc mất mạng):
    Bóc tách kịch bản tiếng Việt/Anh thành Nhân vật (<Picture 1..N>) + Danh sách Cảnh (Cảnh 1..N) chuẩn H3.
    """
    style_info = dict(STYLE_PRESETS.get(style_key, STYLE_PRESETS["xianxia_live"]))
    if custom_style:
        style_info["style_en"] = f"{custom_style.strip()}, {style_info['style_en']}"
        style_info["img_prefix_en"] = f"{custom_style.strip()}, {style_info['img_prefix_en']}"

    scene_count = max(1, min(12, int(round(total_seconds / max(4, scene_seconds)))))
    raw = (script_text or "").strip()

    # Tìm lời thoại trong ngoặc kép "..." nếu có
    dialogues = [m.group(1).strip() for m in re.finditer(r'["“”]([^"“”]{3,140})["“”]', raw)]

    # Chia kịch bản thành các đoạn theo dòng hoặc câu
    raw_lines = [
        ln.strip()
        for ln in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+(?=[A-ZÀ-Ỹ\[])", raw)
        if ln.strip() and len(ln.strip()) > 4
    ]
    if not raw_lines:
        raw_lines = ["Nhân vật chính xuất hiện trong khung cảnh tuyệt đẹp, ánh sáng điện ảnh rực rỡ và chuyển động liền mạch."]

    # Nhận diện nhân vật từ nội dung tiếng Việt
    lower = raw.lower()
    references = []
    if any(w in lower for w in ("tiên nữ", "nữ hiệp", "cô gái", "thiếu nữ", "nàng", "female", "girl", "woman", "sư tỷ", "sư muội")):
        references.append({
            "slot": 1,
            "tag": "<Picture 1>",
            "name_vi": "Nữ Chính (<Picture 1>)",
            "role_vi": "Nhân vật chính",
            "subject_def_en": "A breathtaking young woman with delicate expressive facial features, luminous eyes, long flowing dark hair, and an intricate signature outfit with fine fabric textures.",
            "image_prompt_en": (
                f"{style_info['img_prefix_en']}"
                "a breathtaking young woman with delicate facial features, luminous expressive eyes, long flowing dark hair with subtle ornaments, "
                "wearing an intricate flowing signature outfit, soft golden rim lighting, clean atmospheric background, 8K ultra-detailed character reference sheet."
            ),
        })
    else:
        references.append({
            "slot": 1,
            "tag": "<Picture 1>",
            "name_vi": "Nhân Vật Chính (<Picture 1>)",
            "role_vi": "Nhân vật chính",
            "subject_def_en": "A charismatic protagonist with sharp expressive eyes, distinct hairstyle, and detailed signature attire.",
            "image_prompt_en": (
                f"{style_info['img_prefix_en']}"
                "a charismatic protagonist with sharp expressive facial features, distinct hairstyle, and detailed signature attire, "
                "dramatic cinematic rim lighting, clean atmospheric background, 8K ultra-detailed character reference sheet."
            ),
        })

    if any(w in lower for w in ("hai người", "2 người", "đối thủ", "sư huynh", "chàng trai", "nam chính", "người thứ hai", "bạn đồng hành", "hồ ly", "linh thú")):
        references.append({
            "slot": 2,
            "tag": "<Picture 2>",
            "name_vi": "Nhân Vật Đồng Hành / Đối Thủ (<Picture 2>)",
            "role_vi": "Nhân vật số 2",
            "subject_def_en": "Second key character with distinct contrasting silhouette, clear facial identity, and detailed signature attire.",
            "image_prompt_en": (
                f"{style_info['img_prefix_en']}"
                "a second key character with contrasting regal attire, sharp expressive eyes, distinct hairstyle, "
                "studio-grade cinematic lighting, 8K character reference portrait."
            ),
        })

    pov_prefix = (
        "First-person POV shot from the viewer's eye level (with subtle natural camera breathing and visible foreground hand interaction), looking directly at <Picture 1>. "
        if camera_pov == "pov"
        else (
            "Seamless One-Take continuous tracking camera following <Picture 1> smoothly without hard cuts. "
            if camera_pov == "onetake"
            else "Dynamic multi-angle cinematic camera framing <Picture 1>. "
        )
    )

    scenes = []
    for idx in range(scene_count):
        s_num = idx + 1
        start_s = idx * scene_seconds
        end_s = (idx + 1) * scene_seconds
        line_vi = raw_lines[idx % len(raw_lines)]
        dlg_line = dialogues[idx] if idx < len(dialogues) else ""

        dlg_clause = ""
        if dialogue_mode == "vi" and dlg_line:
            dlg_clause = f' <Picture 1> looks with lifelike emotion and speaks with clear lip-sync: <d>In Vietnamese, "{dlg_line}"</d>.'
        elif dialogue_mode == "vi" and idx == 0 and "thoại" in lower:
            dlg_clause = ' <Picture 1> smiles gently and speaks with natural lip-sync: <d>In Vietnamese, "Chào mừng huynh đã đến với thế giới của muội."</d>.'

        used_tags = [r["tag"] for r in references]
        half_s = max(2, scene_seconds // 2)
        shot_desc = (
            f"[Shot 1] (0s-{half_s}s) {pov_prefix}"
            + (f"Continuation from Scene {s_num - 1} in the same environment. " if s_num > 1 else "Establishing the environment and atmosphere. ")
            + f"<Picture 1> performs natural, fluid motion matching the scene beat ({line_vi[:160]}), with realistic fabric, hair, and lighting interaction. "
            f"[Shot 2] ({half_s}s-{scene_seconds}s) Camera glides closer to capture <Picture 1>'s expressive face and eyes.{dlg_clause} "
            f"The motion settles naturally into a coherent pose at {scene_seconds}s for seamless transition."
        )

        h3_prompt = _ensure_h3_contract_format(
            raw_prompt=shot_desc,
            references=references,
            used_tags=used_tags,
            style_info=style_info,
            scene_idx=s_num,
            total_scenes=scene_count,
            start_sec=start_s,
            end_sec=end_s,
            scene_seconds=scene_seconds,
            summary_en=f"Scene {s_num} featuring {', '.join(used_tags)} in {style_info['name_vi']} style.",
            aspect=aspect,
        )

        scenes.append({
            "index": s_num,
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": scene_seconds,
            "time_range": f"{start_s:02d}s - {end_s:02d}s",
            "title_vi": f"Cảnh {s_num} ({start_s}s–{end_s}s)",
            "summary_vi": line_vi,
            "used_pictures": used_tags,
            "h3_prompt_en": h3_prompt,
        })

    return {
        "ok": True,
        "ai_source": "Bộ Biên Dịch Kịch Bản H3 Nội Bộ (Offline Compiler)",
        "style_key": style_key,
        "style_name_vi": style_info["name_vi"],
        "scene_seconds": scene_seconds,
        "total_seconds": scene_count * scene_seconds,
        "scene_count": scene_count,
        "aspect": aspect,
        "references": references,
        "scenes": scenes,
    }


def analyze_script_for_h3_pipeline(
    d: dict,
    cookie_cfg: dict | None = None,
    cookie_status_fn=None,
) -> dict:
    """
    Hàm trung tâm phân tích kịch bản -> trả về:
    - references: Danh sách <Picture 1..K> kèm Prompt tạo ảnh tham chiếu (dùng cho Krea 2 hoặc tải ảnh lên)
    - scenes: Danh sách Cảnh 1..N kèm Prompt chuẩn MiniMax H3 (subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music)
    """
    d = d or {}
    cookie_cfg = cookie_cfg or {}
    script_text = str(d.get("script") or "").strip()
    if not script_text:
        script_text = "Trên đỉnh Tiên Giới mây ngũ sắc bồng bềnh, một tiên nữ áo lụa trắng bước trên cầu ngọc phát sáng, quay lại mỉm cười dịu dàng và cất tiếng chào bằng tiếng Việt."

    style_key = str(d.get("style_preset") or "xianxia_live").strip()
    custom_style = str(d.get("custom_style") or "").strip()
    style_info = dict(STYLE_PRESETS.get(style_key, STYLE_PRESETS["xianxia_live"]))
    if custom_style:
        style_info["style_en"] = f"{custom_style}, {style_info['style_en']}"
        style_info["img_prefix_en"] = f"{custom_style}, {style_info['img_prefix_en']}"

    scene_seconds = int(float(d.get("scene_seconds") or 10))
    scene_seconds = max(4, min(15, scene_seconds))
    total_seconds = int(float(d.get("total_seconds") or 60))
    total_seconds = max(scene_seconds, min(180, total_seconds))
    scene_count = max(1, min(12, int(round(total_seconds / scene_seconds))))
    eff_total_seconds = scene_count * scene_seconds

    aspect = str(d.get("aspect") or "16:9").strip()
    if aspect not in ("16:9", "9:16", "1:1"):
        aspect = "16:9"
    dialogue_mode = str(d.get("dialogue_mode") or "vi").strip().lower()
    camera_pov = str(d.get("camera_pov") or "cinematic").strip().lower()
    preferred_model = str(d.get("model") or cookie_cfg.get("default_model") or "gemini-2.5-flash").strip()
    api_key = str(d.get("api_key") or cookie_cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()

    dlg_instruction = {
        "vi": 'If a character speaks in a scene, include natural Vietnamese lip-sync dialogue wrapped inside `<d>In Vietnamese, "Exact Vietnamese spoken line..."</d>` inside `detailed_description`.',
        "en": 'If a character speaks in a scene, include natural English lip-sync dialogue wrapped inside `<d>In English, "Exact English spoken line..."</d>` inside `detailed_description`.',
        "none": "Do NOT include spoken dialogue `<d>...</d>`; focus purely on visual storytelling and environmental sound effects.",
    }.get(dialogue_mode, "")

    cam_instruction = {
        "pov": "Use First-Person POV camera framing (viewer's eye perspective with subtle camera breathing and interactive foreground hands when appropriate).",
        "onetake": "Use seamless One-Take continuous tracking camera movement across all scenes so Scene K+1 continues directly from the final pose and camera vector of Scene K.",
        "cinematic": "Use expressive cinematic camera blocking (establishing wide, tracking medium, emotional close-up) while keeping strict spatial and lighting continuity between consecutive scenes.",
    }.get(camera_pov, "")

    half_s = max(2, scene_seconds // 2)
    master_prompt = f"""You are the Master AI Screenplay Analyzer & Official Prompt Compiler for MiniMax H3 Video Studio and Krea 2 (14B) Image Generator.

Analyze the user's screenplay/idea and compile it into:
1. `references`: 1 to 4 key characters or primary recurring subjects labeled `<Picture 1>`, `<Picture 2>`, etc.
   - Each item MUST have:
     * `tag`: `"<Picture 1>"`, `"<Picture 2>"`, etc.
     * `name_vi`: Clear Vietnamese name & role (e.g. `"Lâm Phong - Kiếm Khách Áo Đen (<Picture 1>)"`).
     * `role_vi`: Short Vietnamese role badge (`"Nhân vật chính"`, `"Nhân vật số 2"`, `"Bối cảnh"`, etc.).
     * `subject_def_en`: Concise English visual identity lock for H3's `subject_definitions:` section (exact age, face, hair, eyes, signature outfit, key prop).
     * `image_prompt_en`: A complete, ultra-detailed English Text-to-Image prompt for **Krea 2 (14B)** in `{aspect}` aspect ratio matching the visual style `{style_info['style_en']}`. The image prompt must depict ONLY that single character/subject clearly (full or medium portrait, front/three-quarter angle, clear face, signature outfit, clean lighting) so it serves as a pristine `<Picture k>` reference image.

2. `scenes`: Break the screenplay into EXACTLY **{scene_count} sequential scenes**, where EACH scene is **{scene_seconds} seconds** long (Total duration = **{eff_total_seconds} seconds**).
   - Visual Style to enforce in EVERY scene: `{style_info['style_en']}`
   - Camera Style: {cam_instruction}
   - Dialogue Rule: {dlg_instruction}
   - Continuity Rule: Scene K+1 MUST seamlessly continue the story, environment, lighting, and character state from Scene K!
   - For each scene `1..{scene_count}`, return:
     * `index`: integer `1..{scene_count}`
     * `title_vi`: Short Vietnamese title for this scene
     * `summary_vi`: 1-2 sentence Vietnamese summary of the action, camera, and dialogue so the user can review it easily
     * `used_pictures`: array of active tags in this scene, e.g. `["<Picture 1>"]`
     * `h3_prompt_en`: A COMPLETE, production-ready English prompt following the official **MiniMax H3 Reference Contract** format below (with ALL 6 sections included inside `h3_prompt_en`):
       ```
       subject_definitions:
       - <Picture 1>: [exact English description]

       summary: [reference generation] [one-sentence English summary of this {scene_seconds}s scene in the selected style]

       retention_analysis: [strict identity, face, hair, and outfit preservation rules for <Picture 1>..]

       detailed_description: [Shot 1] (0s-{half_s}s) [camera movement, <Picture 1> action, lighting, environment]... [Shot 2] ({half_s}s-{scene_seconds}s) [camera progression, emotional expression, optional <d>...</d> dialogue, ending pose connecting to next scene].

       overall_soundscape: [synchronized foley, ambience, voice clarity]

       non_diegetic_music: [{style_info['music_en']}]
       ```

Return ONLY a valid JSON object matching this exact structure:
{{
  "references": [
    {{
      "slot": 1,
      "tag": "<Picture 1>",
      "name_vi": "Tên nhân vật (<Picture 1>)",
      "role_vi": "Nhân vật chính",
      "subject_def_en": "Detailed English appearance lock...",
      "image_prompt_en": "Full Krea 2 image generation prompt in English..."
    }}
  ],
  "scenes": [
    {{
      "index": 1,
      "title_vi": "Tiêu đề Cảnh 1",
      "summary_vi": "Tóm tắt tiếng Việt của Cảnh 1...",
      "used_pictures": ["<Picture 1>"],
      "h3_prompt_en": "subject_definitions:\\n- <Picture 1>: ...\\n\\nsummary: [reference generation] ...\\n\\nretention_analysis: ...\\n\\ndetailed_description: [Shot 1] ... [Shot 2] ...\\n\\noverall_soundscape: ...\\n\\nnon_diegetic_music: ..."
    }}
  ]
}}

=== USER SCREENPLAY / IDEA ===
{script_text}
"""

    parsed = None
    ai_source = ""

    # 1. Gọi qua Gemini Cookie API nội bộ (gemini-web-to-api trên 127.0.0.1:4981/openai/v1) với 4 model:
    #    gemini-3.5-flash-lite, gemini-3.8-flash, gemini-3.1-pro, gemini-4-argon
    if callable(cookie_status_fn):
        try:
            st = cookie_status_fn(auto_start=True)
            if st and st.get("connected"):
                api_url = str(st.get("api_url") or cookie_cfg.get("api_url") or "http://127.0.0.1:4981/openai/v1").rstrip("/")
                req_m = str(d.get("model") or cookie_cfg.get("default_model") or "gemini-3.1-pro").strip()
                valid_cookie_models = ("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-pro", "gemini-4-argon")
                cookie_model = req_m if req_m in valid_cookie_models else "gemini-3.1-pro"
                payload = {
                    "model": cookie_model,
                    "messages": [{"role": "user", "content": master_prompt}],
                    "stream": False,
                }
                req = urllib.request.Request(
                    f"{api_url}/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    resp_j = json.loads(resp.read().decode("utf-8", errors="ignore"))
                raw_reply = resp_j["choices"][0]["message"]["content"]
                parsed = _extract_json_obj(raw_reply)
                if isinstance(parsed, dict):
                    ai_source = f"Gemini Cookie API ({cookie_model})"
        except Exception:
            parsed = None

    # 2. Dự phòng qua API Key nếu người dùng có cấu hình thêm
    if not parsed and api_key:
        parsed, src_msg = call_google_gemini_api_key(master_prompt, api_key, preferred_model=preferred_model)
        if parsed:
            ai_source = src_msg

    # 3. Nếu vẫn không có kết nối Gemini, dùng Bộ Biên Dịch Kịch Bản Nội Bộ
    if not isinstance(parsed, dict) or not parsed.get("scenes"):
        return _smart_local_script_compiler(
            script_text=script_text,
            style_key=style_key,
            custom_style=custom_style,
            scene_seconds=scene_seconds,
            total_seconds=eff_total_seconds,
            aspect=aspect,
            dialogue_mode=dialogue_mode,
            camera_pov=camera_pov,
        )

    # Chuẩn hóa `references`
    raw_refs = parsed.get("references") or parsed.get("cast") or []
    references = []
    for idx, r in enumerate(raw_refs[:9]):
        if not isinstance(r, dict):
            continue
        slot_num = idx + 1
        tag = f"<Picture {slot_num}>"
        subj_def = str(r.get("subject_def_en") or r.get("desc") or "Main subject with consistent appearance and wardrobe.").strip()
        img_p = str(r.get("image_prompt_en") or r.get("image_prompt") or f"{style_info['img_prefix_en']}{subj_def}").strip()
        references.append({
            "slot": slot_num,
            "tag": tag,
            "name_vi": str(r.get("name_vi") or f"Nhân Vật #{slot_num} ({tag})").strip(),
            "role_vi": str(r.get("role_vi") or r.get("role") or "Nhân vật tham chiếu").strip(),
            "subject_def_en": subj_def,
            "image_prompt_en": img_p,
        })

    if not references:
        references.append({
            "slot": 1,
            "tag": "<Picture 1>",
            "name_vi": "Nhân Vật Chính (<Picture 1>)",
            "role_vi": "Nhân vật chính",
            "subject_def_en": "Main protagonist with consistent facial identity, hairstyle, and signature outfit.",
            "image_prompt_en": f"{style_info['img_prefix_en']}main protagonist portrait, clear facial features, signature outfit, 8K.",
        })

    # Chuẩn hóa `scenes`
    raw_scenes = parsed.get("scenes") or parsed.get("shots") or []
    scenes = []
    for idx in range(scene_count):
        s_num = idx + 1
        start_s = idx * scene_seconds
        end_s = (idx + 1) * scene_seconds
        sc = raw_scenes[idx] if idx < len(raw_scenes) and isinstance(raw_scenes[idx], dict) else (raw_scenes[-1] if raw_scenes and isinstance(raw_scenes[-1], dict) else {})
        title_vi = str(sc.get("title_vi") or f"Cảnh {s_num}").strip()
        summary_vi = str(sc.get("summary_vi") or sc.get("note_vi") or f"Phân cảnh {s_num} ({start_s}s–{end_s}s)").strip()
        used_pics = sc.get("used_pictures")
        if not isinstance(used_pics, list) or not used_pics:
            used_pics = [references[0]["tag"]]
        raw_h3 = str(sc.get("h3_prompt_en") or sc.get("text") or sc.get("prompt") or "").strip()
        full_h3 = _ensure_h3_contract_format(
            raw_prompt=raw_h3,
            references=references,
            used_tags=used_pics,
            style_info=style_info,
            scene_idx=s_num,
            total_scenes=scene_count,
            start_sec=start_s,
            end_sec=end_s,
            scene_seconds=scene_seconds,
            summary_en=summary_vi,
            aspect=aspect,
        )
        scenes.append({
            "index": s_num,
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": scene_seconds,
            "time_range": f"{start_s:02d}s - {end_s:02d}s",
            "title_vi": title_vi,
            "summary_vi": summary_vi,
            "used_pictures": used_pics,
            "h3_prompt_en": full_h3,
        })

    return {
        "ok": True,
        "ai_source": ai_source or "Google Gemini AI",
        "style_key": style_key,
        "style_name_vi": style_info["name_vi"],
        "scene_seconds": scene_seconds,
        "total_seconds": eff_total_seconds,
        "scene_count": scene_count,
        "aspect": aspect,
        "references": references,
        "scenes": scenes,
    }
