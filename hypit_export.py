"""
Hypit render backend for the fast image-to-video pipeline.

Python still owns the "brain" (script, voiceover, music). This module turns those
materials into a Hypit project (SVML author source, SVS recipes, SVRun, Runtime
Profile, assets) and renders it with the `hypit` CLI:

    hypit build runs/final.svrun --follow
    hypit get <build-id> --output final.video --to <output.mp4>

What Hypit adds over MoviePy: WhisperX word-level alignment of each voiceover
sentence, karaoke captions anchored to words, Ken Burns motion and Chromium
rendering. See https://github.com/hypit-ai/hypit (license: free for your own
organization's use; multi-tenant SaaS or resale needs a commercial license).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image, ImageOps

CANVAS_W, CANVAS_H = 1080, 1920
FRAME_RATE = 30
# Gaps must land on whole frames at FRAME_RATE (250ms would be 7.5 frames).
SENTENCE_GAP = "8f"

# Latin faces in @hypit/fonts-open load only the basic Latin subset, which lacks
# Vietnamese diacritics. Noto Sans SC ships Fontsource's complete Unicode-range
# shards, including the Vietnamese range (U+1EA0-1EF9), so it renders both.
CAPTION_FONT_FAMILY = "noto-sans-sc"
CAPTION_FONT_WEIGHT = 800


class HypitError(RuntimeError):
    pass


def find_hypit(explicit: Optional[str] = None) -> Optional[str]:
    """Locate the hypit executable: explicit path, $HYPIT_BIN, then PATH."""
    candidate = explicit or os.environ.get("HYPIT_BIN")
    if candidate:
        return candidate if Path(candidate).exists() or shutil.which(candidate) else None
    return shutil.which("hypit")


def escape_script_text(text: str) -> str:
    """Escape SVML Script syntax starters (\\ @ < > |) and collapse whitespace."""
    text = " ".join(str(text).split())
    return re.sub(r"([\\@<>|])", r"\\\1", text)


def group_sentences(num_sentences: int, num_images: int) -> List[List[int]]:
    """Split sentence indices into contiguous, evenly sized groups (one per image)."""
    groups = max(1, min(num_sentences, num_images))
    base, extra = divmod(num_sentences, groups)
    out, start = [], 0
    for g in range(groups):
        size = base + (1 if g < extra else 0)
        out.append(list(range(start, start + size)))
        start += size
    return out


def _normalize_image(src: Path, dst: Path) -> tuple:
    """Bake EXIF rotation into the pixels so the declared extent matches what renders."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.save(dst, "JPEG", quality=95)
        return im.size


def build_svml(sentences: Sequence[str], image_extents: Sequence[tuple], language: str,
               has_music: bool, music_gain: float) -> str:
    groups = group_sentences(len(sentences), len(image_extents))

    # Script: one Segment per sentence (each is one voiceover Take). Selection imgK
    # spans the sentences shown over image K. The first opens at Program start
    # (left-absorbing), the last closes at Program end (right-absorbing), and
    # adjacent ones meet at the next sentence's first word, so no frame is empty.
    script_lines = []
    for g, idxs in enumerate(groups):
        open_marker = f"@{{~img{g + 1}}}" if g == 0 else f"@{{img{g + 1}}}"
        script_lines.append(f"    {open_marker}")
        for i in idxs:
            script_lines.append(f"    <s{i + 1}>{escape_script_text(sentences[i])}</s{i + 1}>")
        script_lines.append(f"    @{{/img{g + 1}~}}")

    takes = []
    for i in range(len(sentences)):
        n = i + 1
        takes.append(f"""  <asset:Audio id="vo{n}" src="./assets/vo_{n:02d}.wav"/>
  <pipeline:Normalize id="vo{n}-media" source={{vo{n}}}
    video="none" audio="default" span-authority="audio" clock={{clock}}/>
  <whisperx:SemanticTake id="vo{n}-take" narrative={{story}}
    segment={{story.segment.s{n}}} media={{vo{n}-media.media}} language="{language}"/>""")

    timeline_takes = []
    for i in range(len(sentences)):
        at = "" if i == 0 else f' at="previous.end+{SENTENCE_GAP}"'
        timeline_takes.append(f"    <time:Take source={{vo{i + 1}-take.take}}{at}/>")

    images, items = [], []
    for g, (w, h) in enumerate(image_extents[: len(groups)]):
        n = g + 1
        images.append(f"""  <asset:Image id="img{n}" src="./assets/img_{n:02d}.jpg"/>
  <space:Extent id="img{n}-extent" width="{w}" height="{h}"/>""")
        # Alternate a slow push-in and pull-out (Ken Burns) between images.
        z_start, z_end = ("1", "1.12") if g % 2 == 0 else ("1.12", "1")
        items.append(f"""    <media-track:Item id="slide{n}" image={{img{n}}} extent={{img{n}-extent}}
      frame={{full-frame}} during={{story.selection.img{n}}} appearance={{recipes.media.slide}}>
      <media-track:Sampling at="start" zoom="{z_start}" easing="ease-in-out"/>
      <media-track:Sampling at="end" zoom="{z_end}"/>
    </media-track:Item>""")

    music_decl = music_track = music_film = ""
    if has_music:
        music_decl = """  <asset:Audio id="music" src="./assets/music.wav"/>
  <pipeline:Normalize id="music-media" source={music}
    video="none" audio="default" span-authority="audio" clock={clock}/>
"""
        music_track = f"""  <audio-track:Track id="music-bed" timeline={{speech.timeline}}>
    <audio-track:Item source={{music-media.media}} during="program"
      playback="loop-end" gain="{music_gain}" fade-in="600ms" fade-out="800ms"/>
  </audio-track:Track>
"""
        music_film = "    <film:Track source={music-bed.audio}/>\n"

    nl = "\n"
    return f"""<?svml using="@hypit/markup@1"?>
<!-- Generated by hypit_export.py; edit the JSON input and re-export instead. -->
<svml>
  <import from="@hypit/script@1"/>
  <import as="asset" from="@hypit/media@1"/>
  <import as="program" from="@hypit/program-space@1"/>
  <import as="pipeline" from="@hypit/media-pipeline@1"/>
  <import as="whisperx" from="@hypit/whisperx@1"/>
  <import as="time" from="@hypit/timeline-author@1"/>
  <import as="sound" from="@hypit/sound@1"/>
  <import as="audio-track" from="@hypit/audio-track@1"/>
  <import as="media-track" from="@hypit/media-track@1"/>
  <import as="caption-fine" from="@hypit/caption-fine@1"/>
  <import as="fonts" from="@hypit/fonts-open@1"/>
  <import as="space" from="@hypit/spatial@1"/>
  <import as="film" from="@hypit/film@1"/>
  <import as="render" from="@hypit/render-hyperframes@1"/>
  <import as="recipes" source="./recipes.svs"/>

  <script id="story">
{nl.join(script_lines)}
  </script>

  <space:Canvas id="vertical" width="{CANVAS_W}" height="{CANVAS_H}"/>
  <space:Frame id="full-frame" within={{vertical}}
    left="0%" top="0%" right="100%" bottom="100%"/>
  <program:Clock id="clock" frame-rate="{FRAME_RATE}"/>

{nl.join(takes)}

  <time:Timeline id="speech" clock={{clock}}>
{nl.join(timeline_takes)}
  </time:Timeline>

  <sound:Style id="voice-style"/>
  <sound:Track id="voice" timeline={{speech.timeline}}>
    <sound:Use style={{voice-style}}/>
  </sound:Track>

{music_decl}{music_track}
{nl.join(images)}
  <media-track:Track id="slides" timeline={{speech.timeline}} canvas={{vertical}}>
{nl.join(items)}
  </media-track:Track>

  <fonts:Stack id="caption-font" family="{CAPTION_FONT_FAMILY}" weight="{CAPTION_FONT_WEIGHT}" style="normal"/>
  <caption-fine:Style id="caption-style" recipe={{recipes.caption.main}} font={{caption-font}}/>
  <caption-fine:Track id="captions" document={{story.caption}} timeline={{speech.timeline}}>
    <caption-fine:Use style={{caption-style}}/>
  </caption-fine:Track>

  <film:Film id="main" canvas={{vertical}} timeline={{speech.timeline}} appearance={{recipes.film.vertical}}>
    <film:Track source={{slides.visual}}/>
    <film:Track source={{voice.audio}}/>
    <film:Track source={{captions.track}}/>
{music_film}  </film:Film>

  <render:Video id="final" composition={{main.composition}} timeline={{speech.timeline}}/>
</svml>
"""


RECIPES_SVS = """<?svml using="@hypit/svs@1"?>

<sheet version="1" id="recipes">
  film.vertical {
    background: #000000;
  }

  media.slide {
    stack-order: 10;
    fit: cover;
  }

  caption.main {
    stack-order: 70;
    x: 0.5;
    y: 0.78;
    width: 0.88;
    height: 0.2;
    anchor-x: center;
    anchor-y: center;
    align: center;
    block-align: center;
    inline-size: fixed;
    wrap: word;
    max-lines: 2;
    max-words-per-line: 4;
    line-height: 1.12;
    word-gap: 14;
    size: 72;
    fill: #FFFFFF;
    stroke-color: #000000;
    stroke-width: 4;
    shadow-color: #000000;
    shadow-opacity: 0.8;
    shadow-x: 0;
    shadow-y: 4;
    shadow-blur: 8;
    background: #00000000;
    padding: "0";
    radius: 0;
    karaoke: current;
    karaoke-transition: step;
    active-fill: #FFD54A;
    active-response: pop;
    active-response-frames: 5;
    active-scale: 1.08;
    cue-enter: pop;
    cue-enter-frames: 4;
    cue-exit: fade;
    cue-exit-frames: 3;
    lead-frames: 2;
    tail-frames: 6;
    handoff: cut;
  }
</sheet>
"""

RUN_SVRUN = """<?svml using="@hypit/run-markup@1"?>
<svrun version="1">
  <author source="../main.svml"/>
  <target output="final.video"/>
</svrun>
"""


def default_runtime_profile(language: str) -> dict:
    """Fully local profile: local WhisperX, local ffmpeg media, local Chromium renderer."""
    return {
        "format": "hypit.runtime-local@1",
        "dataRoot": ".hypit/runtimes/local",
        "endpoints": {
            "whisperx.local": {
                "use": "@hypit/provider-whisperx-local",
                "config": {"expectedModel": "small", "alignmentLanguages": [language]},
            },
            "media.local": {
                "use": "@hypit/provider-media-local",
                "config": {"defaultConcurrency": 2},
            },
            "hyperframes.local": {
                "use": "@hypit/provider-hyperframes-local",
                "config": {"workers": 4, "defaultConcurrency": 1},
            },
        },
        "bindings": {
            "@hypit/whisperx@1#whisperx-alignment": "whisperx.local",
        },
    }


def export_project(
    project_dir: Path,
    images: Sequence[str],
    sentences: Sequence[str],
    voice_parts: Sequence[str],
    music_path: Optional[str] = None,
    music_gain: float = 0.2,
    language: str = "vi",
    runtime_profile: Optional[str] = None,
) -> Path:
    """Write a self-contained Hypit project and return its directory."""
    if len(sentences) != len(voice_parts):
        raise ValueError("Need exactly one voiceover file per sentence.")
    if not images:
        raise ValueError("At least one image is required.")

    project_dir = Path(project_dir)
    assets = project_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (project_dir / "runs").mkdir(exist_ok=True)

    used_images = list(images)[: max(1, min(len(images), len(sentences)))]
    if len(used_images) < len(images):
        print(f"  ⚠ Hypit: {len(images)} images but {len(sentences)} sentences; "
              f"using the first {len(used_images)} (one image per sentence group).")

    extents = []
    for i, img in enumerate(used_images, 1):
        if not Path(img).is_file():
            raise FileNotFoundError(f"Image not found: {img}")
        extents.append(_normalize_image(Path(img), assets / f"img_{i:02d}.jpg"))

    for i, part in enumerate(voice_parts, 1):
        shutil.copyfile(part, assets / f"vo_{i:02d}.wav")

    has_music = bool(music_path and Path(music_path).is_file())
    if has_music:
        shutil.copyfile(music_path, assets / "music.wav")

    (project_dir / "main.svml").write_text(
        build_svml(sentences, extents, language, has_music, music_gain), encoding="utf-8")
    (project_dir / "recipes.svs").write_text(RECIPES_SVS, encoding="utf-8")
    (project_dir / "runs" / "final.svrun").write_text(RUN_SVRUN, encoding="utf-8")

    profile_path = project_dir / "hypit.runtime.json"
    if runtime_profile:
        shutil.copyfile(runtime_profile, profile_path)
    else:
        profile_path.write_text(json.dumps(default_runtime_profile(language), indent=2) + "\n",
                                encoding="utf-8")

    pkg = project_dir / "package.json"
    if not pkg.exists():
        pkg.write_text(json.dumps({"name": project_dir.name.lower().replace("_", "-"),
                                   "private": True}, indent=2) + "\n", encoding="utf-8")
    return project_dir


def _run(cmd: List[str], cwd: Path) -> str:
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise HypitError(f"{' '.join(cmd)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def render_project(project_dir: Path, output_path: Path, hypit_bin: str = "hypit") -> Path:
    """Check, build and export final.video from an exported Hypit project."""
    project_dir = Path(project_dir).resolve()
    output_path = Path(output_path).resolve()
    run = "runs/final.svrun"
    runtime = ["--runtime", "hypit.runtime.json"]

    _run([hypit_bin, "check", run], project_dir)
    build_out = _run([hypit_bin, "build", run, *runtime, "--follow", "--json"], project_dir)
    build_id = _parse_build_id(build_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([hypit_bin, "get", build_id, *runtime, "--output", "final.video",
          "--to", str(output_path)], project_dir)
    return output_path


def _parse_build_id(stdout: str) -> str:
    """Find the build id in `hypit build --json` output (a JSON document, possibly followed by progress lines)."""
    decoder = json.JSONDecoder()
    start = stdout.find("{")
    while start != -1:
        try:
            doc, _ = decoder.raw_decode(stdout, start)
        except ValueError:
            start = stdout.find("{", start + 1)
            continue
        build = doc.get("build") if isinstance(doc, dict) else None
        if isinstance(build, dict) and isinstance(build.get("id"), str):
            return build["id"]
        found = _find_key(doc, ("buildId", "build_id", "id"))
        if found:
            return found
        start = stdout.find("{", start + 1)
    match = re.search(r"\b(bld_[A-Za-z0-9_]+)", stdout)
    if match:
        return match.group(1)
    raise HypitError(f"Could not find a build id in hypit output:\n{stdout}")


def _find_key(doc, keys) -> Optional[str]:
    if isinstance(doc, dict):
        for key in keys:
            if isinstance(doc.get(key), str):
                return doc[key]
        for value in doc.values():
            found = _find_key(value, keys)
            if found:
                return found
    elif isinstance(doc, list):
        for value in doc:
            found = _find_key(value, keys)
            if found:
                return found
    return None
