#!/usr/bin/env python3
"""
Create a 9:16 ASMR reel: random-loop video background + word-timed subtitles
works with the minimal `moviepy` build (no .resize / .subclip on CompositeClip)
"""
from datetime import datetime
from pathlib import Path
import re
import asyncio, tempfile, textwrap, os, random
import requests

from moviepy import (
    AudioFileClip,
    VideoFileClip,
    TextClip,
    CompositeVideoClip,
    concatenate_videoclips,
    vfx,
)
from elevenlabs import ElevenLabs
import openai


# ───── UPLOAD-POST CLIENT ─────────────────────────────────
class UploadPostError(Exception):
    """Base exception for Upload-Post API errors"""
    pass

class UploadPostClient:
    BASE_URL = "https://api.upload-post.com/api"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Apikey {self.api_key}",
            "User-Agent": "upload-post-python-client/0.1.0"
        })

    def upload_video(
        self,
        video_path: Path,
        title: str,
        user: str,
        platforms: list,
        description: str = None,
        tags: list = None
    ) -> dict:
        """
        Upload a video to specified social media platforms
        """

        try:
            with video_path.open("rb") as video_file:
                files = {"video": video_file}
                data = {
                    "title": title,
                    "user": user,
                    "platform[]": platforms,
                    # "is_aigc": True
                }
                if description:
                    data["description"] = description
                if tags:
                    # multiple tags via tags[]
                    data.update({"tags[]": tags})

                resp = self.session.post(
                    f"{self.BASE_URL}/upload",
                    files=files,
                    data=data
                )
                resp.raise_for_status()
                return resp.json()
        except requests.RequestException as e:
            raise UploadPostError(f"Upload failed: {e}")
        

# ───── API KEYS ──────────────────────────────────────────
os.environ["OPENAI_API_KEY"] = (
    "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"
)
ELEVEN_API_KEY = "sk_12dfc751c84275171a2c4369631843ef14d2711e07f3f472"
VOICE_ID = "xTB8eataxCKE46gkjKkH"

# ───── FOLDERS / FILES ──────────────────────────────────
VIDEO_DIR = Path(__file__).parent / "lulu" / "videos"
OUTPUT = "asmr_reel.mp4"
FONT = Path(__file__).parent / "NotoSansJP-Medium.ttf"
FONTSIZE, COLOR = 45, "white"
RESO = (480, 854)

# ───── SCRIPT TEXT ──────────────────────────────────────
DEFAULT_PROMPT = """
You are Lulu, a fun and teasing Japanese teacher hosting a daily TikTok challenge called “Guess with Lulu 🎌✨”. The goal is to keep viewers engaged. Each video starts with an exciting hook like “Can YOU guess what this means?”, and promises the answer at the end with a countdown (5… 4… 3… 2… 1…). Choose a playful, kawaii, spicy, or funny Japanese phrase that’s easy to guess. Instead of syllables, give a short and playful **hint**. At the end, reveal the meaning and invite users to comment their guesses.

Use this EXACT format PLEASE:

Day {day} of Guess with Lulu!
I’ll reveal the meaning at the end, so stay with me.

Today’s phrase is:
変な気持ちだね〜？

Again, slowly:
変な気持ちだね〜？

Here is a hint: You say this sentencce when something feels a little off.

Before giving the answer, follow and drop your guess in the comments.

Answer in 3… 2… 1…

It means: “This feels weird.”

See you tomorrow honey.

""".strip()

DUOLINGO_PROMPT = textwrap.dedent(
    """
    I want you to generate me a lesson for japanese similar to this one:
    Change the intro, super brief (Your name is Lulu). The game is always named "Duolingo with Lulu"
    Please be creative for the thing you teach (sometimes horny, sometimes bad words, sometimes good words, or kawaii, or sentences of everyday; really be random, but it should be an easy thing that people know in english), it should be random thing, that could attract the user in the first seconds.
    It should be a playfull lesson. For the syllable parts, maximum 4 groups of syllables to say together, don't overdo with every syllable, group them, so its not too long.
    The last part, should be different, and should allow the viewer to either interact in the comments, or follow, or like the video (choose one randomly), be creative, and kawaii, and sometimes related to the lesson, and keep the outro super brief please, because people will leave.
    Return it exactly in this format, with good punctuation. Please nothing more:

    Day {day} of Duolingo with Lulu until I hit 10K subs!!  
    Today let’s learn: “Where’s my cat?”… in Japanese!  
    猫どこ〜？

    Syllable by syllable:  
    ねこ…  
    どこ〜？

    Say it fast:  
    猫どこ〜？

    Follow me for Day {next_day} if you love your cat too 🐾💕
"""
).strip()

# ───── HELPERS ───────────────────────────────────────────
from moviepy import VideoFileClip, vfx, concatenate_videoclips

def loop_clip_to_duration(path: str, duration: float):
    # 1) Load + Resize (and Crop, if desired), assigning the result
    clip0 = (VideoFileClip(path, audio=False)
             .with_effects([vfx.Resize(RESO)]))
    
    # Optional: compute x1,y1,x2,y2 here, then chain Crop:
    # clip0 = clip0.with_effects([vfx.Crop(x1, y1, x2, y2)])

    # 2) Shorten if already long enough
    if clip0.duration >= duration:
        return clip0.with_duration(duration)

    # 3) Loop until ≥ duration, applying same effects each time
    clips = [clip0]
    elapsed = clip0.duration
    while elapsed < duration:
        clip_i = (VideoFileClip(path, audio=False)
                  .with_effects([vfx.Resize(RESO)]))
        # clip_i = clip_i.with_effects([vfx.Crop(x1, y1, x2, y2)])
        clips.append(clip_i)
        elapsed += clip_i.duration

    # 4) Concatenate and set exact duration
    return concatenate_videoclips(clips).with_duration(duration)


async def transcribe_words(wav: str, client) -> list:
    with open(wav, "rb") as f:
        r = await client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = r.model_dump().get("words", [])
    max_duration = 1

    grouped = []
    i = 0
    while i < len(words):
        word = words[i]
        word_duration = word["end"] - word["start"]

        # Case 1: single long word, add it alone
        if word_duration > max_duration:
            grouped.append({
                "text": word["word"],
                "start": word["start"],
                "end": word["end"]
            })
            i += 1
            continue

        # Case 2: try grouping with following words under max_duration
        buffer = [word]
        start_time = word["start"]
        end_time = word["end"]
        i += 1

        while i < len(words):
            next_word = words[i]
            new_end = next_word["end"]
            duration = new_end - start_time

            if duration > max_duration:
                break  # overflow would happen if we add this word

            buffer.append(next_word)
            end_time = new_end
            i += 1

        sentence = " ".join(w["word"] for w in buffer).strip()
        grouped.append({
            "text": sentence,
            "start": buffer[0]["start"],
            "end": buffer[-1]["end"]
        })

    return grouped



# ───── MAIN ─────────────────────────────────────────────
async def main():
    # prepare clients
    eleven = ElevenLabs(api_key=ELEVEN_API_KEY)
    oai = openai.AsyncOpenAI()

    start_date = datetime(2025, 5, 26)
    today = datetime.now()
    duolingo_day = (today - start_date).days + 1

    print(duolingo_day)

    # Pick prompt based on time
    if 3 <= today.hour < 10:
        prompt = DUOLINGO_PROMPT.format(day=duolingo_day, next_day=duolingo_day + 1)
    else:
        prompt = DEFAULT_PROMPT.format(day=duolingo_day)

    response = await oai.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
    )
    script_text = response.choices[0].message.content.strip()

    print(script_text)

    # 1-- TTS
    print("🎤  Synthesising speech …")
    tts_stream = eleven.text_to_speech.convert(
        voice_id=VOICE_ID,
        output_format="mp3_44100_128",
        text=script_text,
        model_id="eleven_multilingual_v2",
    )
    wav_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    with open(wav_path, "wb") as f:
        for chunk in tts_stream:
            f.write(chunk)

    # 2-- Whisper
    print("📝  Transcribing …")
    words = await transcribe_words(wav_path, oai)
    if not words:
        raise RuntimeError("Whisper returned zero word-timestamps.")

    # 3-- pick / loop background
    audio_clip = AudioFileClip(wav_path)
    duration = audio_clip.duration
    candidates = [
        os.path.join(VIDEO_DIR, x)
        for x in os.listdir(VIDEO_DIR)
        if x.lower().endswith((".mp4", ".mov", ".mkv"))
    ]
    if not candidates:
        raise RuntimeError("No background videos found in:", VIDEO_DIR)
    bg_path = random.choice(candidates)
    print("🎞   Using background:", os.path.basename(bg_path))
    bg_clip = loop_clip_to_duration(bg_path, duration).with_position("center")

    # 4-- build subtitle layers
    print("✏️   Building subtitles …")
    subtitles = [
        TextClip(
            text=txt["text"],
            font=FONT,
            font_size=FONTSIZE,
            color=(255, 255, 255),          # white fill (inside)
            stroke_color=(255, 0, 150),     # neon pink stroke (outside)
            stroke_width=3,             # just a subtle border
            method="caption",
            size=RESO,
        )
        .with_start(txt["start"])
        .with_duration(txt["end"] - txt["start"])
        .with_position(("center", 200))   # ← centre-down shift
        for txt in words
    ]

    # 5-- compose & export
    print("🎬  Rendering video …")
    final = CompositeVideoClip([bg_clip, *subtitles]).with_audio(audio_clip)
    final.write_videofile(
        OUTPUT,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="4000k",
        preset="medium",
    )
    print("✅  Saved ➜", OUTPUT)

    # publish video
    print("<3 Publish the video …")

    publish_client = UploadPostClient(api_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6ImZlenphbmkucmF6aUBnbWFpbC5jb20iLCJleHAiOjQ5MDA2Nzk0MzEsImp0aSI6IjRjMzEzODc0LTMzOTktNGVhMi05NzMzLWViMDEwMzU1ODU4NCJ9.C6QIQdmSAs4iXAyGVb20eSXmUDCQh2J-AM1ZYoq35TE")

    title_match = re.search(r'[“"](.*?)[”"]', script_text)
    title = "'" + title_match.group(1) + "'" + " in Japanese 💖" if title_match else "Japanese ASMR Lesson"
    hashtags_line = [
        "japanese","anime","asmr","kawaii","love","fyp","learnjapanese",
        "otaku","weeb","cute","asmrvideo","animegirl","bilingual",
        "languagelearning","asmrcommunity","trending","viral","fun",
        "foryoupage","tiktokjapanese"
    ]
    title += "\n#" + " #".join(hashtags_line)
    print(title)

    response = publish_client.upload_video(
        video_path=Path(OUTPUT),
        title=title,
        user="japanwithlulu",
        platforms=["tiktok", "instagram"]
    )

if __name__ == "__main__":
    asyncio.run(main())
