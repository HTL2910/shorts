"""
Automated Video Generator - Fast Image-to-Video & Gemini AI
Vertical 9:16 Shorts / Reels / TikTok Generator

Capabilities:
1. Fast Image-to-Video: Provide a few images + script/topic -> auto-sync voiceover + subtitles + music.
2. AI Script Generation: Topic -> Gemini 3.6 Flash -> punchy multi-sentence sales script.
3. Auto Subtitle Sync: Auto-measures sentence TTS duration; zero manual timestamping needed.
4. Smart 9:16 Framing: Dynamic center-crop preserving aspect ratio (no stretched images).
5. 100% Self-Contained: Uses Pillow for styling (zero ImageMagick dependency).
6. Legacy SRT Mode: Backward compatible with existing SRT + image prompt JSONs.
"""

import json
import re
import os
import sys
import shutil
import argparse
from pathlib import Path
from typing import List, Dict, Tuple, Union
from datetime import datetime
import time

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import numpy as np
from moviepy.editor import (
    AudioFileClip,
    ImageClip,
    CompositeVideoClip,
    CompositeAudioClip,
)
from pydub import AudioSegment
from pydub.generators import Sine, Triangle, Sawtooth
from gtts import gTTS

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def load_env(env_path: str = ".env"):
    """Load key-value pairs from .env into os.environ if not already set."""
    path = Path(env_path)
    if not path.is_file():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val


load_env()


def split_into_sentences(text_or_list: Union[str, List[str]]) -> List[str]:
    """Cleanly split input script into individual sentences."""
    if isinstance(text_or_list, list):
        # Flatten and filter
        out = []
        for item in text_or_list:
            item_str = str(item).strip()
            if item_str:
                out.append(item_str)
        return out

    # String input: split by punctuation or newline
    raw_sentences = re.split(r"(?<=[.!?\n])\s+", str(text_or_list).strip())
    cleaned = [s.strip() for s in raw_sentences if s.strip()]
    return cleaned if cleaned else [str(text_or_list).strip()]


class GeminiVideoGenerator:
    def __init__(self, output_dir: str = "output", temp_dir: str = "temp", api_key: str = None):
        self.output_dir = Path(output_dir)
        self.temp_dir = Path(temp_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)

        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.client = None
        if self.api_key and GENAI_AVAILABLE:
            try:
                self.client = genai.Client(api_key=self.api_key)
                print("✓ Gemini API configured (gemini-3.5-flash-lite)")
            except Exception as e:
                print(f"⚠ Gemini client init warning: {e}")
        else:
            print("ℹ Running in standalone mode (no Gemini API key set or genai not installed)")

    # =========================================================================
    # 1. SMART IMAGE PROCESSING (VERTICAL 9:16 CROP & CROSSFADE)
    # =========================================================================

    def prepare_image_for_vertical(
        self, img_path: Union[str, Path], output_path: Union[str, Path], target_size=(1080, 1920)
    ) -> str:
        """
        Smart crop/fit image into 1080x1920 portrait without distortion.
        Auto-handles EXIF orientation from mobile phone photos.
        """
        img_path = Path(img_path)
        output_path = Path(output_path)

        with Image.open(img_path) as im:
            # Respect EXIF rotation
            im = ImageOps.exif_transpose(im).convert("RGB")
            w, h = im.size
            tw, th = target_size

            # Scale to cover entire target canvas
            scale = max(tw / w, th / h)
            nw = int(round(w * scale))
            nh = int(round(h * scale))
            im_resized = im.resize((nw, nh), Image.Resampling.LANCZOS)

            # Center crop
            left = max(0, (nw - tw) // 2)
            top = max(0, (nh - th) // 2)
            right = left + tw
            bottom = top + th

            im_cropped = im_resized.crop((left, top, right, bottom))
            im_cropped.save(str(output_path), quality=95)
            return str(output_path)

    def build_fast_image_clips(self, image_paths: List[str], total_duration: float) -> List[ImageClip]:
        """
        Scale, center-crop, and distribute images evenly across total video duration.
        Applies smooth crossfade transitions between slides.
        """
        if not image_paths:
            raise ValueError("No images provided for video generation.")

        num_images = len(image_paths)
        clip_duration = total_duration / num_images
        clips = []

        transition_dur = 0.4 if (num_images > 1 and clip_duration >= 1.5) else 0.0

        print(f"Distributing {num_images} images over {total_duration:.2f}s (~{clip_duration:.2f}s per image)...")

        for i, img_p in enumerate(image_paths):
            if not os.path.exists(img_p):
                raise FileNotFoundError(f"Image not found: {img_p}")

            cropped_path = self.temp_dir / f"crop_{i:03d}_{Path(img_p).stem}.jpg"
            self.prepare_image_for_vertical(img_p, cropped_path)

            start_t = i * clip_duration
            # Overlap slightly for crossfade
            dur = clip_duration + (transition_dur if i < num_images - 1 else 0.0)

            clip = (
                ImageClip(str(cropped_path))
                .set_start(start_t)
                .set_duration(dur)
            )

            if i > 0 and transition_dur > 0:
                clip = clip.crossfadein(transition_dur)

            clips.append(clip)

        return clips

    # =========================================================================
    # 2. VOICE & SUBTITLE AUTO-SYNC (ZERO MANUAL TIMESTAMPS)
    # =========================================================================

    def generate_script_from_topic(self, topic: str, num_sentences: int = 4) -> List[str]:
        """Use Gemini 3.6 Flash to generate short, engaging video script from a topic."""
        print(f"\n[AI Script] Generating short video script for topic: '{topic}'...")

        if not self.client:
            print("  ⚠ No Gemini API client; using fallback template script.")
            return [
                f"Khám phá ngay {topic} với chất lượng vượt trội.",
                "Thiết kế hiện đại, mang lại trải nghiệm hoàn hảo cho bạn.",
                "Sản phẩm được đông đảo khách hàng tin dùng và đánh giá cao.",
                "Đặt mua ngay hôm nay để nhận ưu đãi tốt nhất!"
            ]

        prompt = f"""Bạn là một chuyên gia sáng tạo kịch bản video ngắn TikTok/Reels/Shorts.
Hãy viết một kịch bản ngắn, cực kỳ hấp dẫn gồm đúng {num_sentences} câu cho chủ đề sau:
"{topic}"

Yêu cầu:
1. Mỗi câu ngắn gọn, súc tích (khoảng 8-16 từ), dễ đọc, cuốn hút người nghe.
2. Câu 1: Hook mở đầu gây chú ý.
3. Câu 2-3: Giới thiệu điểm nổi bật / tính năng / lợi ích cốt lõi.
4. Câu cuối: Kêu gọi hành động (Call to action).
5. Trả về định dạng JSON thuần mảng các chuỗi, ví dụ:
["Câu 1...", "Câu 2...", "Câu 3...", "Câu 4..."]
Tuyệt đối không giải thích, chỉ trả về JSON."""

        for model_name in ["gemini-3.5-flash-lite", "gemini-3.6-flash"]:
            try:
                response = self.client.models.generate_content(
                    model=model_name, contents=[prompt]
                )
                raw_text = response.text.strip().replace("```json", "").replace("```", "").strip()
                sentences = json.loads(raw_text)
                if isinstance(sentences, list) and len(sentences) >= 2:
                    print(f"  ✓ Generated {len(sentences)} sentences via {model_name}:")
                    for idx, s in enumerate(sentences, 1):
                        print(f"    {idx}. {s}")
                    return [s.strip() for s in sentences if s.strip()]
            except Exception as e:
                print(f"  ⚠ Model {model_name} error: {e}")

        return [
            f"Khám phá ngay {topic} với chất lượng vượt trội.",
            "Thiết kế hiện đại, mang lại trải nghiệm hoàn hảo cho bạn.",
            "Sản phẩm được đông đảo khách hàng tin dùng và đánh giá cao.",
            "Đặt mua ngay hôm nay để nhận ưu đãi tốt nhất!"
        ]

    def generate_voiceover_from_sentences(self, sentences: List[str]) -> Tuple[str, List[Dict]]:
        """
        Generate voiceover sentence-by-sentence and calculate exact subtitle timing automatically.
        Returns (audio_path, subtitles_list with start/end in seconds).
        """
        print("\n=== Generating Voiceover & Auto-Sync Timestamps ===")
        combined_audio = AudioSegment.empty()
        subtitles = []
        current_time = 0.0
        pause_between = 0.25  # 250ms pause between sentences

        for i, sent in enumerate(sentences):
            sent = sent.strip()
            if not sent:
                continue

            temp_path = self.temp_dir / f"sent_{i}.mp3"
            # Auto-detect language (Vietnamese diacritics check)
            is_vi = any(c in sent for c in "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ")
            lang = "vi" if is_vi else "en"

            tts = gTTS(text=sent, lang=lang, slow=False)
            tts.save(str(temp_path))

            sent_audio = AudioSegment.from_mp3(str(temp_path))
            duration_sec = len(sent_audio) / 1000.0

            start_t = round(current_time, 2)
            end_t = round(start_t + duration_sec, 2)

            subtitles.append({
                "index": i + 1,
                "start": start_t,
                "end": end_t,
                "text": sent
            })
            print(f"  [{start_t:05.2f}s -> {end_t:05.2f}s] {sent}")

            combined_audio += sent_audio
            combined_audio += AudioSegment.silent(duration=int(pause_between * 1000))
            current_time = end_t + pause_between

            try:
                temp_path.unlink()
            except Exception:
                pass

        audio_path = self.temp_dir / "master_voiceover.wav"
        combined_audio.export(str(audio_path), format="wav")
        print(f"  ✓ Voiceover completed: {current_time:.2f}s total duration.")
        return str(audio_path), subtitles

    def create_subtitle_clip(self, text: str, start: float, end: float) -> ImageClip:
        """
        Create high-contrast, modern vertical video subtitle using Pillow.
        Zero dependency on ImageMagick.
        """
        import textwrap

        w, h = 1080, 320
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Select available system font with broad Unicode / Vietnamese support
        font = None
        for font_path in [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/SFNS.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, 46)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        # Wrap text nicely for 1080 width
        lines = textwrap.wrap(text.strip(), width=28)
        line_height = 58
        total_text_h = len(lines) * line_height
        y = (h - total_text_h) // 2

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (w - text_w) // 2

            pad_x, pad_y = 26, 8
            # Semi-transparent dark pill background for readability on any photo
            draw.rounded_rectangle(
                [x - pad_x, y - pad_y, x + text_w + pad_x, y + text_h + pad_y + 8],
                radius=16,
                fill=(0, 0, 0, 185),
            )
            # Subtle drop shadow
            draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 140))
            # Text foreground
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height

        sub_path = self.temp_dir / f"sub_{int(start * 100)}_{int(end * 100)}.png"
        img.save(sub_path)

        duration = max(0.1, end - start)
        return (
            ImageClip(str(sub_path))
            .set_start(start)
            .set_end(end)
            .set_duration(duration)
            .set_position(("center", 1440))  # TikTok safe zone
        )

    # =========================================================================
    # 3. BACKGROUND MUSIC GENERATION / LOADING
    # =========================================================================

    def generate_music_parameters(self, vo_notes: Dict, duration: float) -> Dict:
        """Use Gemini to generate procedural music parameters or return clean defaults."""
        emotion = vo_notes.get("emotion", "cheerful, energetic")
        if self.client:
            prompt = f"""You are a music composer AI. Suggest parameters for background synth ambient audio.
Emotion: {emotion}, Duration: {duration}s
Provide parameters as JSON:
{{
    "base_frequencies": [220, 330, 440],
    "waveform": "sine",
    "tempo_bpm": 80,
    "volume_db": -32,
    "rhythm_pattern": [1, 0, 0.5, 0, 1, 0, 0.5, 0]
}}
Return ONLY valid JSON."""
            try:
                res = self.client.models.generate_content(model="gemini-3.5-flash-lite", contents=[prompt])
                return json.loads(res.text.strip().replace("```json", "").replace("```", ""))
            except Exception:
                pass

        return {
            "base_frequencies": [220, 277, 330, 440],
            "waveform": "sine",
            "tempo_bpm": 90,
            "volume_db": -30,
            "rhythm_pattern": [1, 0, 0.5, 0, 1, 0, 0.5, 0],
        }

    def generate_background_music(self, vo_notes: Dict, duration: float) -> str:
        """Generate procedural ambient background music."""
        params = self.generate_music_parameters(vo_notes, duration)
        music = AudioSegment.silent(duration=int(duration * 1000))

        wave_generators = {"sine": Sine, "triangle": Triangle, "sawtooth": Sawtooth}
        WaveGen = wave_generators.get(params.get("waveform", "sine"), Sine)

        for freq in params.get("base_frequencies", [220, 330]):
            tone = WaveGen(freq).to_audio_segment(duration=int(duration * 1000))
            tone = tone + params.get("volume_db", -30)
            music = music.overlay(tone)

        music = music.fade_in(2000).fade_out(2500)
        music_path = self.temp_dir / "music.wav"
        music.export(str(music_path), format="wav")
        return str(music_path)

    # =========================================================================
    # 4. FAST PIPELINE ASSEMBLY
    # =========================================================================

    def assemble_fast_video(
        self,
        images: List[str],
        sentences: List[str],
        output_name: str,
        music_mood: str = "cheerful, energetic",
        custom_music_path: str = None,
    ) -> str:
        """Assemble vertical video from images + script sentences rapidly."""
        print(f"\n{'='*60}")
        print(f"🎬 Assembling Fast Video: {output_name}")
        print(f"{'='*60}")

        # 1. Voiceover + auto-synced subtitle boundaries
        vo_path, subtitles = self.generate_voiceover_from_sentences(sentences)
        vo_audio = AudioFileClip(vo_path)
        total_duration = vo_audio.duration
        print(f"Video target duration: {total_duration:.2f}s")

        # 2. Image clips with smart vertical cropping & transitions
        image_clips = self.build_fast_image_clips(images, total_duration)
        video_track = CompositeVideoClip(image_clips, size=(1080, 1920)).set_duration(total_duration)

        # 3. Subtitle overlays
        print("\nRendering subtitle clips...")
        subtitle_clips = []
        for sub in subtitles:
            s_clip = self.create_subtitle_clip(sub["text"], sub["start"], sub["end"])
            subtitle_clips.append(s_clip)

        final_video = CompositeVideoClip([video_track] + subtitle_clips).set_duration(total_duration)

        # 4. Background music
        if custom_music_path and os.path.exists(custom_music_path):
            print(f"Using custom music: {custom_music_path}")
            bg_music = AudioFileClip(custom_music_path)
            if bg_music.duration < total_duration:
                from moviepy.editor import afx
                bg_music = afx.audio_loop(bg_music, duration=total_duration)
            else:
                bg_music = bg_music.subclip(0, total_duration)
            bg_music = bg_music.volumex(0.18)
        else:
            print(f"Generating background music (mood: {music_mood})...")
            music_path = self.generate_background_music({"emotion": music_mood}, total_duration)
            bg_music = AudioFileClip(music_path).volumex(0.20)

        # 5. Composite audio
        final_audio = CompositeAudioClip([vo_audio, bg_music])
        final_video = final_video.set_audio(final_audio)

        # 6. Render MP4
        output_path = self.output_dir / f"{output_name}.mp4"
        print(f"\nExporting video to {output_path} (1080x1920 @ 30fps)...")
        final_video.write_videofile(
            str(output_path),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            threads=8,
            bitrate="6000k",
        )

        print(f"\n✓ Video saved successfully: {output_path}")
        return str(output_path)

    def process_fast(
        self,
        images: List[str],
        script: Union[str, List[str]] = None,
        topic: str = None,
        output_name: str = None,
        music_mood: str = "cheerful, energetic",
        music_path: str = None,
    ) -> str:
        """Main entry point for fast image-to-video workflow."""
        if not images:
            raise ValueError("At least one image path must be provided.")

        # Resolve script
        if script:
            sentences = split_into_sentences(script)
        elif topic:
            sentences = self.generate_script_from_topic(topic, num_sentences=max(3, len(images) * 2))
        else:
            raise ValueError("Either 'script' or 'topic' must be provided.")

        if not output_name:
            output_name = f"fast_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        video_path = self.assemble_fast_video(
            images=images,
            sentences=sentences,
            output_name=output_name,
            music_mood=music_mood,
            custom_music_path=music_path,
        )

        self._cleanup_temp()
        return video_path

    # =========================================================================
    # 5. LEGACY SRT & IMAGE-PROMPT PIPELINE (BACKWARD COMPATIBLE)
    # =========================================================================

    def parse_srt_timing(self, timing_str: str) -> Tuple[float, float]:
        def srt_to_seconds(srt_time):
            h, m, s = srt_time.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)

        start, end = timing_str.split(" --> ")
        return srt_to_seconds(start), srt_to_seconds(end)

    def parse_input(self, json_path: str) -> Dict:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Fast format detection
        if "images" in data:
            return data

        # Legacy SRT format
        subtitles = []
        current_sub = {}

        for line in data.get("srt_content", "").strip().split("\n"):
            line = line.strip()
            if not line:
                if current_sub:
                    subtitles.append(current_sub)
                    current_sub = {}
            elif line.isdigit():
                current_sub["index"] = int(line)
            elif "-->" in line:
                start, end = self.parse_srt_timing(line)
                current_sub["start"] = start
                current_sub["end"] = end
            else:
                current_sub["text"] = current_sub.get("text", "") + " " + line

        if current_sub:
            subtitles.append(current_sub)

        image_prompts = []
        for img_block in re.findall(r"\[IMAGE\](.*?)(?=\[IMAGE\]|\Z)", data.get("image_prompts", ""), re.DOTALL):
            timing_match = re.search(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-\s*(\d{2}:\d{2}:\d{2},\d{3})", img_block)
            if timing_match:
                start, end = self.parse_srt_timing(f"{timing_match.group(1)} --> {timing_match.group(2)}")
                prompt = re.sub(r"\d{2}:\d{2}:\d{2},\d{3}\s*-\s*\d{2}:\d{2}:\d{2},\d{3}", "", img_block)
                prompt = " ".join(prompt.split())
                image_prompts.append({"start": start, "end": end, "prompt": prompt})

        return {
            "subtitles": subtitles,
            "image_prompts": image_prompts,
            "vo_notes": data.get("vo_notes", {}),
            "metadata": data.get("metadata", {}),
        }

    def generate_voiceover(self, subtitles: List[Dict], vo_notes: Dict) -> str:
        """Legacy voiceover generator."""
        audio_path = self.temp_dir / "voiceover.wav"
        combined_audio = AudioSegment.empty()

        for sub in subtitles:
            text = sub["text"].strip()
            if not text:
                continue
            temp_p = self.temp_dir / f"leg_{sub['index']}.mp3"
            is_vi = any(c in text for c in "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
            tts = gTTS(text=text, lang="vi" if is_vi else "en", slow=False)
            tts.save(str(temp_p))
            combined_audio += AudioSegment.from_mp3(str(temp_p))
            combined_audio += AudioSegment.silent(duration=250)
            try:
                temp_p.unlink()
            except Exception:
                pass

        combined_audio.export(str(audio_path), format="wav")
        return str(audio_path)

    def assemble_legacy_video(self, parsed_data: Dict, output_name: str) -> str:
        """Assemble video from legacy SRT and image prompts."""
        print("\n=== Assembling Legacy SRT Video ===")
        vo_path = self.generate_voiceover(parsed_data["subtitles"], parsed_data.get("vo_notes", {}))
        vo_audio = AudioFileClip(vo_path)
        total_duration = vo_audio.duration

        # Look for local sample assets if available
        sample_candidates = [
            Path("sample_ao_hoc_sinh/ao_hoc_sinh_product.jpg"),
            Path("sample_ao_hoc_sinh/nu_sinh_model.jpg"),
        ]
        available_samples = [str(p) for p in sample_candidates if p.is_file()]

        image_clips = []
        for i, img_prompt in enumerate(parsed_data.get("image_prompts", [])):
            duration = img_prompt["end"] - img_prompt["start"]
            if available_samples:
                chosen = available_samples[i % len(available_samples)]
                crop_p = self.temp_dir / f"crop_leg_{i}.jpg"
                self.prepare_image_for_vertical(chosen, crop_p)
                clip = ImageClip(str(crop_p)).set_duration(duration).set_start(img_prompt["start"])
            else:
                # Fallback blank
                im = Image.new("RGB", (1080, 1920), (25, 30, 45))
                fb_p = self.temp_dir / f"fb_{i}.jpg"
                im.save(fb_p)
                clip = ImageClip(str(fb_p)).set_duration(duration).set_start(img_prompt["start"])
            image_clips.append(clip)

        video = CompositeVideoClip(image_clips or [ImageClip(str(available_samples[0])).set_duration(total_duration)], size=(1080, 1920))

        sub_clips = []
        for sub in parsed_data["subtitles"]:
            sub_clips.append(self.create_subtitle_clip(sub["text"], sub["start"], sub["end"]))

        final_video = CompositeVideoClip([video] + sub_clips).set_duration(total_duration)
        music_p = self.generate_background_music(parsed_data.get("vo_notes", {}), total_duration)
        music_audio = AudioFileClip(music_p).volumex(0.20)
        final_video = final_video.set_audio(CompositeAudioClip([vo_audio, music_audio]))

        output_path = self.output_dir / f"{output_name}.mp4"
        final_video.write_videofile(
            str(output_path),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=4,
            bitrate="6000k",
        )
        return str(output_path)

    def process(self, json_input: str, output_name: str = None) -> str:
        """Process JSON input (auto-detects fast image format vs legacy SRT format)."""
        if output_name is None:
            output_name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        print(f"\n{'='*60}")
        print(f"Processing JSON: {json_input}")
        print(f"{'='*60}\n")

        with open(json_input, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "images" in data:
            # Fast format!
            images = data.get("images", [])
            script = data.get("script")
            topic = data.get("topic")
            music_mood = data.get("music_mood") or data.get("vo_notes", {}).get("emotion", "cheerful, energetic")
            music_path = data.get("music_path")

            video_path = self.process_fast(
                images=images,
                script=script,
                topic=topic,
                output_name=output_name,
                music_mood=music_mood,
                music_path=music_path,
            )
        else:
            # Legacy SRT format
            parsed_data = self.parse_input(json_input)
            video_path = self.assemble_legacy_video(parsed_data, output_name)
            self._cleanup_temp()

        return video_path

    def _cleanup_temp(self):
        """Remove temp artifacts."""
        for temp_file in self.temp_dir.glob("*"):
            try:
                temp_file.unlink()
            except Exception:
                pass


# =============================================================================
# CLI & EXAMPLE GENERATION
# =============================================================================

def create_example_fast_json():
    """Create a sample input JSON for the new fast image-to-video format."""
    example = {
        "title": "Áo Sơ Mi Học Sinh Cao Cấp",
        "images": [
            "sample_ao_hoc_sinh/ao_hoc_sinh_product.jpg",
            "sample_ao_hoc_sinh/nu_sinh_model.jpg"
        ],
        "script": [
            "Mùa tựu trường rạng rỡ cùng áo sơ mi học sinh cao cấp.",
            "Chất liệu cotton lụa thoáng mát, chống nhăn suốt cả ngày dài.",
            "Form dáng chuẩn chỉnh, tự tin tỏa sáng mọi khoảnh khắc học đường.",
            "Đặt mua ngay hôm nay để nhận ưu đãi đầu năm học!"
        ],
        "music_mood": "cheerful, energetic, upbeat"
    }

    with open("sample_ao_hoc_sinh/fast_input.json", "w", encoding="utf-8") as f:
        json.dump(example, f, ensure_ascii=False, indent=2)

    print("✓ Created sample_ao_hoc_sinh/fast_input.json")


def main():
    parser = argparse.ArgumentParser(
        description="Fast Image-to-Video Generator (Gemini AI + Vertical 9:16 Shorts/TikTok)"
    )
    parser.add_argument("input_file", nargs="?", default=None, help="Path to input JSON file")
    parser.add_argument("output_name", nargs="?", default=None, help="Name of output video")
    parser.add_argument("--images", nargs="+", default=None, help="List of image files to include in video")
    parser.add_argument("--script", nargs="+", default=None, help="Script text or list of sentences")
    parser.add_argument("--topic", default=None, help="Topic for Gemini AI to auto-generate script")
    parser.add_argument("--music", default=None, help="Path to custom background music audio file")
    parser.add_argument("--mood", default="cheerful, energetic", help="Music mood for procedural audio")
    parser.add_argument("-o", "--output", dest="cli_output", default=None, help="Output video name")
    parser.add_argument("--create-sample", action="store_true", help="Generate sample fast_input.json")

    args = parser.parse_args()

    if args.create_sample:
        create_example_fast_json()
        sys.exit(0)

    generator = GeminiVideoGenerator()

    # Priority 1: Direct CLI arguments (--images + [--script | --topic])
    if args.images:
        out_name = args.cli_output or args.output_name or f"fast_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        video_path = generator.process_fast(
            images=args.images,
            script=args.script,
            topic=args.topic,
            output_name=out_name,
            music_mood=args.mood,
            music_path=args.music,
        )
        print(f"\n🎉 DONE: {video_path}")
        return

    # Priority 2: JSON file
    if args.input_file:
        out_name = args.cli_output or args.output_name
        video_path = generator.process(args.input_file, out_name)
        print(f"\n🎉 DONE: {video_path}")
        return

    # No arguments: show help and create sample
    print("=" * 65)
    print("🎬 FAST IMAGE-TO-VIDEO GENERATOR (9:16 Shorts / Reels / TikTok)")
    print("=" * 65)
    print("\nCách 1: Chạy bằng file JSON nhanh gọn (không cần SRT hay timestamps):")
    print("  python video_generator.py sample_ao_hoc_sinh/fast_input.json [output_name]\n")
    print("Cách 2: Chạy trực tiếp từ dòng lệnh (Gemini tự sinh kịch bản từ topic):")
    print('  python video_generator.py --images img1.jpg img2.jpg --topic "Áo sơ mi học sinh" -o video_ao\n')
    print("Cách 3: Chạy trực tiếp với kịch bản bạn viết sẵn:")
    print('  python video_generator.py --images img1.jpg img2.jpg --script "Câu 1." "Câu 2." -o video_ao\n')

    create_example_fast_json()


if __name__ == "__main__":
    main()
