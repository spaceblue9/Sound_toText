import os
import shutil
import argparse
import configparser
import whisper
import imageio_ffmpeg
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from tqdm import tqdm
import whisper, os

### pip install -r requirement.txtpip install -r requirement.txt
### pip freeze >> requirements.txt (keep library to requirements.txt)

## เวลาทำไฟล์ exe ต้อง Checkpath ของ whisper แล้วนำไปใส่ใน Pyinstaller
## print(os.path.dirname(whisper.__file__))
## pyinstaller --onefile --icon=video.ico  --copy-metadata imageio --copy-metadata imageio-ffmpeg --hidden-import=imageio --hidden-import=imageio_ffmpeg --add-data "C:\Users\user\Desktop\MP4toText\.venv\Lib\site-packages\whisper;whisper" transcribe_whisper.py 

# ==============================================================================
# 🔧 ค่าตั้งต้น (แก้ได้)
# ==============================================================================

DEFAULT_INPUT_FILE = "input.mp3"
DEFAULT_OUTPUT_TEXT = "output.txt"
DEFAULT_MODEL_SIZE = "small"      # tiny, base, small, medium, large
CHUNK_SEC = 10                    # ความยาวแต่ละ chunk (วินาที)

DEFAULT_CONFIG_FILE = "config.ini"
DEFAULT_LANGUAGE = "auto"         # "auto" = ให้โมเดลตรวจเอง, หรือใส่รหัสเช่น "th", "en", "ja"
DEFAULT_TASK = "transcribe"       # "transcribe" = ถอดเสียง, "translate" = แปลเป็นอังกฤษ


# ==============================================================================
# ⚙️ Utility
# ==============================================================================

def sec_to_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02}:{s:02}"


def setup_ffmpeg(ffmpeg_name: str = "ffmpeg.exe") -> None:
    source_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    target_ffmpeg = ffmpeg_name

    if not os.path.exists(target_ffmpeg):
        shutil.copy(source_ffmpeg, target_ffmpeg)
        print(f"✅ สร้างไฟล์ {target_ffmpeg} สำเร็จ!")
    else:
        print(f"🔧 พบไฟล์ {target_ffmpeg} แล้ว พร้อมใช้งาน")


# ==============================================================================
# 📁 อ่าน config.ini
# ==============================================================================

def load_config(config_path: str) -> configparser.ConfigParser | None:
    if not os.path.exists(config_path):
        return None
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")
    return config


def get_proxy_from_config(config: configparser.ConfigParser | None) -> dict:
    proxy_conf = {
        "use_proxy": False,
        "host": None,
        "port": None,
        "user": None,
        "password": None,
    }
    if config is None or "proxy" not in config:
        return proxy_conf

    section = config["proxy"]
    proxy_conf["use_proxy"] = section.getboolean("use_proxy", fallback=False)
    proxy_conf["host"] = section.get("host", fallback=None)
    proxy_conf["port"] = section.get("port", fallback=None)
    proxy_conf["user"] = section.get("user", fallback=None)
    proxy_conf["password"] = section.get("pass", fallback=None)
    return proxy_conf


def get_transcribe_settings_from_config(config: configparser.ConfigParser | None) -> dict:
    """
    อ่านค่า [transcribe] จาก config.ini ถ้ามี
    ตัวอย่างใน config.ini:
    [transcribe]
    input = myaudio.mp3
    output = result.txt
    model = medium
    language = th
    task = transcribe
    chunk_sec = 15
    """
    conf = {
        "input": DEFAULT_INPUT_FILE,
        "output": DEFAULT_OUTPUT_TEXT,
        "model": DEFAULT_MODEL_SIZE,
        "language": DEFAULT_LANGUAGE,
        "task": DEFAULT_TASK,
        "chunk_sec": CHUNK_SEC,
    }
    if config is None or "transcribe" not in config:
        return conf

    section = config["transcribe"]
    conf["input"] = section.get("input", fallback=conf["input"])
    conf["output"] = section.get("output", fallback=conf["output"])
    conf["model"] = section.get("model", fallback=conf["model"])
    conf["language"] = section.get("language", fallback=conf["language"])
    conf["task"] = section.get("task", fallback=conf["task"])
    conf["chunk_sec"] = section.getint("chunk_sec", fallback=conf["chunk_sec"])
    return conf


# ==============================================================================
# 🌐 Proxy
# ==============================================================================

def setup_proxy_from_values(
    user: str | None,
    password: str | None,
    host: str | None,
    port: str | None
) -> None:
    if not host or not port:
        print("🌐 ไม่ได้ตั้งค่า Proxy (host/port ไม่ครบ)")
        return

    user_part = ""
    if user and password:
        user_part = f"{user}:{password}@"

    proxy_url = f"http://{user_part}{host}:{port}"
    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    #print(f"🔒 ตั้งค่า Proxy เรียบร้อย: {proxy_url}")
    print(f"🔒 ตั้งค่า Proxy เรียบร้อย: ")


def setup_proxy(args, config: configparser.ConfigParser | None) -> None:
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        print("🌐 ใช้ Proxy จาก Environment variable (HTTP_PROXY/HTTPS_PROXY)")
        return

    proxy_conf = get_proxy_from_config(config)
    if proxy_conf["use_proxy"]:
        print("🌐 ใช้ Proxy จาก config.ini")
        host = args.proxy_host or proxy_conf["host"]
        port = args.proxy_port or proxy_conf["port"]
        user = args.proxy_user or proxy_conf["user"]
        password = args.proxy_pass or proxy_conf["password"]
        setup_proxy_from_values(user=user, password=password, host=host, port=port)
        return

    if not (args.use_proxy or args.proxy_host or args.proxy_user or args.proxy_pass or args.proxy_port):
        print("🌐 ไม่ได้ใช้ Proxy")
        return

    print("🌐 ใช้ Proxy จาก command-line arguments")
    setup_proxy_from_values(
        user=args.proxy_user,
        password=args.proxy_pass,
        host=args.proxy_host,
        port=args.proxy_port,
    )


# ==============================================================================
# 🎬 แปลงไฟล์และถอดเสียง
# ==============================================================================

def extract_audio(input_path: str) -> str:
    ext = os.path.splitext(input_path)[1].lower()

    if ext == ".mp4":
        audio_temp_path = "temp_audio.mp3"
        print("🎬 กำลังแยกเสียงจากวิดีโอ...")
        video = VideoFileClip(input_path)
        video.audio.write_audiofile(audio_temp_path, codec="mp3", logger=None)
        video.close()
        return audio_temp_path

    if ext == ".mp3":
        return input_path

    raise ValueError("รองรับเฉพาะไฟล์ .mp4 และ .mp3 เท่านั้น")


def transcribe_file(
    input_file: str,
    output_text: str,
    model_size: str = "small",
    chunk_sec: int = 10,
    language: str = "auto",
    task: str = "transcribe",
) -> None:
    if not os.path.exists(input_file):
        print(f"❌ ไม่พบไฟล์: {input_file}")
        return

    try:
        audio_path = extract_audio(input_file)
    except ValueError as e:
        print(f"❌ {e}")
        return

    print(f"📦 โหลดโมเดล Whisper ({model_size})...")
    model = whisper.load_model(model_size)

    audio_clip = AudioFileClip(audio_path)
    duration_sec = audio_clip.duration

    print(f"⏱️ ความยาวไฟล์เสียง: {sec_to_time(duration_sec)}")

    # ตั้งค่า language/task
    transcribe_kwargs = {"task": task}
    if language and language.lower() != "auto":
        transcribe_kwargs["language"] = language  # ใช้รหัสภาษา เช่น "th", "en"

    print(f"🌍 โหมดภาษา: language={language}, task={task}")
    print("🚀 เริ่มถอดเสียง...")

    extracted_text = []

    with tqdm(total=duration_sec, unit="sec", desc="📊 Progress") as pbar:
        for start in range(0, int(duration_sec), chunk_sec):
            end = min(start + chunk_sec, int(duration_sec))

            segment = audio_clip.subclipped(start, end)
            temp_chunk_path = "chunk_temp.mp3"
            segment.write_audiofile(
                temp_chunk_path,
                codec="mp3",
                logger=None
            )

            result = model.transcribe(temp_chunk_path, **transcribe_kwargs)
            extracted_text.append(result.get("text", ""))

            pbar.update(end - start)
            pbar.set_postfix_str(f"{sec_to_time(end)} / {sec_to_time(duration_sec)}")

            if os.path.exists(temp_chunk_path):
                os.remove(temp_chunk_path)

    audio_clip.close()

    final_text = " ".join(extracted_text).strip()
    with open(output_text, "w", encoding="utf-8") as f:
        f.write(final_text)

    print(f"\n✅ เสร็จสมบูรณ์! บันทึกไฟล์เรียบร้อยที่: {output_text}")

    if input_file.lower().endswith(".mp4") and os.path.exists("temp_audio.mp3"):
        os.remove("temp_audio.mp3")


# ==============================================================================
# 🖥️ argparse
# ==============================================================================

def parse_args():
    desc_text = (
        "โปรแกรมถอดเสียงจากไฟล์ .mp3 / .mp4 ด้วย Whisper (รองรับหลายภาษา)\n"
        "อ่านค่า Proxy/ค่าพื้นฐานได้จาก Environment, config.ini และ command-line\n"
        "ลำดับ Proxy: Env > config.ini > command-line\n"
        "ภาษา: --lang auto (ตรวจเอง), หรือกำหนดรหัสภาษาเช่น th, en\n"
        "งาน: --task transcribe (ถอดเสียง), --task translate (แปลเป็นอังกฤษ)\n"
    )

    parser = argparse.ArgumentParser(
        description=desc_text,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    general = parser.add_argument_group("ทั่วไป")
    general.add_argument(
        "-i", "--input",
        help="ไฟล์อินพุต (.mp3 หรือ .mp4)",
    )
    general.add_argument(
        "-o", "--output",
        help="ไฟล์ .txt สำหรับบันทึกข้อความ",
    )
    general.add_argument(
        "-m", "--model",
        help="ขนาดโมเดล Whisper (tiny, base, small, medium, large)",
    )
    general.add_argument(
        "--chunk",
        type=int,
        help="ความยาวของแต่ละ chunk (วินาที)",
    )
    general.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help="ไฟล์ config.ini ค่าตั้งต้น: %(default)s",
    )
    general.add_argument(
        "--lang",
        help="ภาษาที่พูดในไฟล์เสียง (รหัส ISO-639-1 เช่น th, en, ja) หรือ 'auto' ให้ตรวจเอง",
    )
    general.add_argument(
        "--task",
        choices=["transcribe", "translate"],
        help="โหมดงาน: transcribe=ถอดเสียง, translate=แปลเป็นอังกฤษ",
    )

    proxy = parser.add_argument_group("Proxy (Env / config.ini / args)")
    proxy.add_argument(
        "--use-proxy",
        action="store_true",
        help="บังคับใช้ Proxy จาก command-line (กรณีไม่ใช้ config.ini หรือ Env)",
    )
    proxy.add_argument(
        "--proxy-host",
        help="ที่อยู่ Proxy (เช่น proxy.company.com)",
    )
    proxy.add_argument(
        "--proxy-port",
        help="พอร์ต Proxy (เช่น 8080)",
    )
    proxy.add_argument(
        "--proxy-user",
        help="ชื่อผู้ใช้ Proxy",
    )
    proxy.add_argument(
        "--proxy-pass",
        help="รหัสผ่าน Proxy (หากมีอักขระพิเศษ เช่น @ # ให้แปลงเป็นรหัส URL เช่น %%40)",
    )

    return parser.parse_args()


# ==============================================================================
# ▶️ main
# ==============================================================================

def main():
    print("--- Create by : Sarawut Sittharod ---")
    print("--- ⚙️ กำลังตั้งค่าระบบ ---")

    args = parse_args()

    config = load_config(args.config)
    if config:
        print(f"📁 โหลด config จาก: {args.config}")
        base_conf = get_transcribe_settings_from_config(config)
    else:
        print(f"📁 ไม่พบไฟล์ config: {args.config} (ข้าม)")
        base_conf = {
            "input": DEFAULT_INPUT_FILE,
            "output": DEFAULT_OUTPUT_TEXT,
            "model": DEFAULT_MODEL_SIZE,
            "language": DEFAULT_LANGUAGE,
            "task": DEFAULT_TASK,
            "chunk_sec": CHUNK_SEC,
        }

    # ให้ config เป็นฐาน แล้ว CLI ทับเฉพาะค่าที่ไม่ใช่ None
    input_file = args.input if args.input is not None else base_conf["input"]
    output_file = args.output if args.output is not None else base_conf["output"]
    model_size = args.model if args.model is not None else base_conf["model"]
    language = args.lang if args.lang is not None else base_conf["language"]
    task = args.task if args.task is not None else base_conf["task"]
    chunk_sec = args.chunk if args.chunk is not None else base_conf["chunk_sec"]

    setup_ffmpeg()
    setup_proxy(args, config)

    transcribe_file(
        input_file=input_file,
        output_text=output_file,
        model_size=model_size,
        chunk_sec=chunk_sec,
        language=language,
        task=task,
    )


if __name__ == "__main__":
    main()
