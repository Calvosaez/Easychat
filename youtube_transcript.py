from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import urllib.request
import urllib.error
import json
import html
import re
import time
import random
from flask_cors import CORS

app = Flask(__name__, static_folder=".")
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://studycards.x10.mx",
                "https://www.studycards.x10.mx"
            ]
        }
    }
)

# ============================================================
# CONFIGURACIÓN
# ============================================================

COOKIE_BROWSER = None

MAX_SUBTITLE_RETRIES = 3

RETRY_DELAYS = [
    2,
    5,
    10
]


# ============================================================
# UTILIDADES
# ============================================================

def extract_video_id(url):

    patterns = [
        r"(?:v=)([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            url
        )

        if match:
            return match.group(1)


    if re.fullmatch(
        r"[A-Za-z0-9_-]{11}",
        url.strip()
    ):

        return url.strip()


    return None


# ============================================================
# LIMPIAR TEXTO
# ============================================================

def clean_text(text):

    if not text:
        return ""


    text = html.unescape(text)


    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )


    text = text.replace(
        "\n",
        " "
    )


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    return text.strip()


# ============================================================
# FORMATO DE SUBTÍTULOS
# ============================================================

def choose_subtitle_format(formats):

    preferences = [
        "json3",
        "srv3",
        "srv2",
        "srv1",
        "vtt",
        "ttml"
    ]


    for preferred in preferences:

        for item in formats:

            if (
                item.get("ext") == preferred
                and
                item.get("url")
            ):

                return item


    for item in formats:

        if item.get("url"):
            return item


    return None


# ============================================================
# HTTP
# ============================================================

def download_text(url):

    headers = {

        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),

        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "*/*;q=0.8"
        ),

        "Accept-Language":
            "es-ES,es;q=0.9,en;q=0.8",

        "Referer":
            "https://www.youtube.com/",

        "Connection":
            "keep-alive",
    }


    last_error = None


    for attempt in range(
        MAX_SUBTITLE_RETRIES
    ):

        try:

            req = urllib.request.Request(
                url,
                headers=headers
            )


            with urllib.request.urlopen(
                req,
                timeout=25
            ) as response:

                return response.read().decode(
                    "utf-8",
                    errors="replace"
                )


        except urllib.error.HTTPError as error:

            last_error = error


            if error.code != 429:
                raise


            print(
                f"YouTube devolvió 429. "
                f"Intento {attempt + 1}/"
                f"{MAX_SUBTITLE_RETRIES}"
            )


            if (
                attempt
                <
                MAX_SUBTITLE_RETRIES - 1
            ):

                delay = (
                    RETRY_DELAYS[
                        min(
                            attempt,
                            len(RETRY_DELAYS) - 1
                        )
                    ]
                    +
                    random.uniform(
                        0.2,
                        1.0
                    )
                )


                time.sleep(
                    delay
                )


        except Exception as error:

            last_error = error

            raise


    if (
        isinstance(
            last_error,
            urllib.error.HTTPError
        )
        and
        last_error.code == 429
    ):

        raise RuntimeError(
            "YOUTUBE_429"
        )


    raise last_error


# ============================================================
# JSON3
# ============================================================

def parse_json3(data):

    result = []


    for event in data.get(
        "events",
        []
    ):

        segments = event.get(
            "segs"
        )


        if not segments:
            continue


        text_parts = []


        for segment in segments:

            text = segment.get(
                "utf8",
                ""
            )


            if text:
                text_parts.append(
                    text
                )


        text = clean_text(
            "".join(
                text_parts
            )
        )


        if not text:
            continue


        start_ms = event.get(
            "tStartMs",
            0
        )


        duration_ms = event.get(
            "dDurationMs",
            0
        )


        result.append({

            "start":
                start_ms / 1000,

            "duration":
                duration_ms / 1000,

            "text":
                text

        })


    return remove_duplicates(
        result
    )


# ============================================================
# VTT
# ============================================================

def time_to_seconds(value):

    value = value.replace(
        ",",
        "."
    )


    parts = value.split(
        ":"
    )


    try:

        if len(parts) == 3:

            hours = float(
                parts[0]
            )

            minutes = float(
                parts[1]
            )

            seconds = float(
                parts[2]
            )


            return (
                hours * 3600
                +
                minutes * 60
                +
                seconds
            )


        if len(parts) == 2:

            minutes = float(
                parts[0]
            )

            seconds = float(
                parts[1]
            )


            return (
                minutes * 60
                +
                seconds
            )


    except Exception:
        pass


    return 0


def parse_vtt(content):

    content = content.replace(
        "\r",
        ""
    )


    blocks = re.split(
        r"\n\s*\n",
        content
    )


    result = []


    for block in blocks:

        lines = [

            line.strip()

            for line in block.split(
                "\n"
            )

            if line.strip()

        ]


        if not lines:
            continue


        time_index = -1


        for index, line in enumerate(
            lines
        ):

            if "-->" in line:

                time_index = index

                break


        if time_index == -1:
            continue


        time_line = lines[
            time_index
        ]


        match = re.search(

            r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d+)"
            r"\s*-->\s*"
            r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d+)",

            time_line

        )


        if not match:
            continue


        start = time_to_seconds(
            match.group(1)
        )


        end = time_to_seconds(
            match.group(2)
        )


        text_lines = lines[
            time_index + 1:
        ]


        text = clean_text(
            " ".join(
                text_lines
            )
        )


        text = re.sub(
            r"<\d{2}:\d{2}:\d{2}\.\d{3}>",
            "",
            text
        )


        text = clean_text(
            text
        )


        if not text:
            continue


        result.append({

            "start":
                start,

            "duration":
                max(
                    0,
                    end - start
                ),

            "text":
                text

        })


    return remove_duplicates(
        result
    )


# ============================================================
# XML / SRV
# ============================================================

def parse_xml(content):

    from xml.etree import ElementTree


    result = []


    try:

        root = ElementTree.fromstring(
            content
        )

    except Exception:

        return result


    for node in root.iter():

        if not node.tag.endswith(
            "text"
        ):

            continue


        try:

            start = float(
                node.attrib.get(
                    "start",
                    node.attrib.get(
                        "t",
                        0
                    )
                )
            )

        except Exception:

            start = 0


        try:

            duration = float(
                node.attrib.get(
                    "dur",
                    node.attrib.get(
                        "d",
                        0
                    )
                )
            )

        except Exception:

            duration = 0


        text = clean_text(
            "".join(
                node.itertext()
            )
        )


        if text:

            result.append({

                "start":
                    start,

                "duration":
                    duration,

                "text":
                    text

            })


    return remove_duplicates(
        result
    )


# ============================================================
# ELIMINAR DUPLICADOS
# ============================================================

def remove_duplicates(items):

    cleaned = []

    last_text = None


    for item in items:

        text = clean_text(
            item.get(
                "text",
                ""
            )
        )


        if not text:
            continue


        if text == last_text:
            continue


        item[
            "text"
        ] = text


        cleaned.append(
            item
        )


        last_text = text


    return cleaned


# ============================================================
# YT-DLP
# ============================================================

def build_ydl_options():

    options = {

        "quiet":
            True,

        "no_warnings":
            True,

        "skip_download":
            True,

        "extract_flat":
            False,

        "retries":
            3,

        "fragment_retries":
            3,

        "sleep_interval_requests":
            1,

        "http_headers": {

            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/127.0 Safari/537.36"
            ),

            "Accept-Language":
                "es-ES,es;q=0.9,en;q=0.8"

        }

    }


    if COOKIE_BROWSER:

        options[
            "cookiesfrombrowser"
        ] = (
            COOKIE_BROWSER,
            None,
            None,
            None
        )


    return options


def extract_video_info(
    video_url
):

    options = build_ydl_options()


    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        return ydl.extract_info(
            video_url,
            download=False
        )


# ============================================================
# LEER PISTA
# ============================================================

def read_subtitle_track(
    formats
):

    selected_format = (
        choose_subtitle_format(
            formats
        )
    )


    if not selected_format:

        raise RuntimeError(
            "No se encontró un formato válido de subtítulos."
        )


    subtitle_url = (
        selected_format[
            "url"
        ]
    )


    extension = (
        selected_format.get(
            "ext",
            ""
        )
    )


    content = download_text(
        subtitle_url
    )


    if extension == "json3":

        return parse_json3(
            json.loads(
                content
            )
        )


    if extension == "vtt":

        return parse_vtt(
            content
        )


    items = parse_xml(
        content
    )


    if items:
        return items


    try:

        data = json.loads(
            content
        )


        return parse_json3(
            data
        )

    except Exception:
        pass


    return []


# ============================================================
# SELECCIONAR IDIOMA ORIGINAL
# ============================================================

def select_original_language(
    info,
    subtitles
):

    if not subtitles:
        return None


    # Idioma que yt-dlp detecta para el vídeo.
    original_language = (
        info.get("language")
        or ""
    ).strip()


    print(
        "Idioma detectado del vídeo:",
        original_language
    )


    if (
        original_language
        and
        original_language in subtitles
    ):

        return original_language


    # Si devuelve algo como ja-JP y existe ja.
    if original_language:

        base_language = (
            original_language
            .split("-")[0]
        )


        if base_language in subtitles:

            return base_language


    # Idiomas originales frecuentes.
    preferred = [
        "ja",
        "en",
        "es",
        "ko",
        "zh",
        "fr",
        "de",
        "it",
        "pt",
        "ru"
    ]


    for language in preferred:

        if language in subtitles:
            return language


    # Preferir códigos simples como "ja"
    # en lugar de traducciones como "ja-en".
    for language in subtitles.keys():

        if (
            "-" not in language
            and
            "." not in language
        ):

            return language


    return next(
        iter(
            subtitles.keys()
        ),
        None
    )


# ============================================================
# OBTENER TRANSCRIPCIÓN
# ============================================================

def get_transcript(
    video_url
):

    info = extract_video_info(
        video_url
    )


    manual_subtitles = (
        info.get(
            "subtitles"
        )
        or {}
    )


    automatic_subtitles = (
        info.get(
            "automatic_captions"
        )
        or {}
    )


    print()
    print("=" * 70)
    print(
        "VIDEO:",
        info.get(
            "title",
            ""
        )
    )

    print(
        "ID:",
        info.get(
            "id",
            ""
        )
    )

    print(
        "IDIOMA:",
        info.get(
            "language",
            ""
        )
    )

    print()

    print(
        "SUBTÍTULOS MANUALES:"
    )

    print(
        list(
            manual_subtitles.keys()
        )
    )

    print()

    print(
        "PISTAS DE TRANSCRIPCIÓN:"
    )

    print(
        list(
            automatic_subtitles.keys()
        )
    )

    print("=" * 70)
    print()


    selected_source = None
    selected_language = None
    source_type = None


    # ========================================================
    # PRIMERO: SUBTÍTULOS MANUALES
    # ========================================================

    if manual_subtitles:

        selected_language = (
            select_original_language(
                info,
                manual_subtitles
            )
        )


        if selected_language:

            selected_source = (
                manual_subtitles
            )

            source_type = (
                "manual"
            )


    # ========================================================
    # SEGUNDO:
    # TRANSCRIPCIÓN QUE YOUTUBE TIENE DISPONIBLE
    # ========================================================

    if (
        selected_source is None
        and
        automatic_subtitles
    ):

        selected_language = (
            select_original_language(
                info,
                automatic_subtitles
            )
        )


        if selected_language:

            selected_source = (
                automatic_subtitles
            )

            source_type = (
                "transcript"
            )


    # ========================================================
    # NO EXISTE
    # ========================================================

    if (
        selected_source is None
        or
        selected_language is None
    ):

        return {

            "items":
                [],

            "language":
                None,

            "title":
                info.get(
                    "title",
                    ""
                ),

            "channel":
                info.get(
                    "channel",
                    info.get(
                        "uploader",
                        ""
                    )
                ),

            "videoId":
                info.get(
                    "id",
                    ""
                ),

            "message":
                "Este vídeo no tiene una transcripción disponible."

        }


    print(
        "Pista seleccionada:",
        selected_language
    )

    print(
        "Origen:",
        source_type
    )


    formats = (
        selected_source.get(
            selected_language,
            []
        )
    )


    print(
        "Formatos disponibles:",
        [
            item.get("ext")
            for item in formats
        ]
    )

    print()


    # ========================================================
    # DESCARGAR TRANSCRIPCIÓN
    # ========================================================

    items = read_subtitle_track(
        formats
    )


    if not items:

        return {

            "items":
                [],

            "language":
                selected_language,

            "title":
                info.get(
                    "title",
                    ""
                ),

            "channel":
                info.get(
                    "channel",
                    info.get(
                        "uploader",
                        ""
                    )
                ),

            "videoId":
                info.get(
                    "id",
                    ""
                ),

            "message":
                (
                    "La transcripción existe, "
                    "pero no se pudo leer."
                )

        }


    return {

        "items":
            items,

        "language":
            selected_language,

        "title":
            info.get(
                "title",
                ""
            ),

        "channel":
            info.get(
                "channel",
                info.get(
                    "uploader",
                    ""
                )
            ),

        "videoId":
            info.get(
                "id",
                ""
            ),

        "message":
            "Transcripción cargada correctamente."

    }


# ============================================================
# API
# ============================================================

@app.route(
    "/api/transcript"
)
def transcript():

    video_url = (
        request.args.get(
            "url",
            ""
        )
        .strip()
    )


    if not video_url:

        return jsonify({

            "error":
                "Falta la URL del vídeo."

        }), 400


    video_id = (
        extract_video_id(
            video_url
        )
    )


    if not video_id:

        return jsonify({

            "error":
                "La URL de YouTube no es válida."

        }), 400


    normalized_url = (
        "https://www.youtube.com/watch?v="
        +
        video_id
    )


    try:

        result = get_transcript(
            normalized_url
        )


        return jsonify(
            result
        )


    except RuntimeError as error:

        if str(error) == "YOUTUBE_429":

            return jsonify({

                "error":
                    "YouTube está limitando temporalmente "
                    "la descarga de la transcripción.",

                "details":
                    "HTTP 429 Too Many Requests.",

                "code":
                    "YOUTUBE_429"

            }), 429


        return jsonify({

            "error":
                "Error al obtener la transcripción.",

            "details":
                str(error)

        }), 500


    except yt_dlp.utils.DownloadError as error:

        text = str(
            error
        )


        if (
            "429" in text
            or
            "Too Many Requests" in text
        ):

            return jsonify({

                "error":
                    "YouTube está limitando temporalmente "
                    "las solicitudes.",

                "details":
                    text,

                "code":
                    "YOUTUBE_429"

            }), 429


        return jsonify({

            "error":
                "YouTube rechazó la solicitud "
                "o no se pudo leer el vídeo.",

            "details":
                text

        }), 500


    except urllib.error.HTTPError as error:

        if error.code == 429:

            return jsonify({

                "error":
                    "YouTube está bloqueando temporalmente "
                    "la descarga de la transcripción.",

                "details":
                    "HTTP 429 Too Many Requests.",

                "code":
                    "YOUTUBE_429"

            }), 429


        return jsonify({

            "error":
                "Error HTTP al obtener la transcripción.",

            "details":
                str(error)

        }), 500


    except Exception as error:

        print(
            "Error:",
            error
        )


        return jsonify({

            "error":
                "Error al obtener la transcripción.",

            "details":
                str(error)

        }), 500


# ============================================================
# FAVICON
# ============================================================

@app.route(
    "/favicon.ico"
)
def favicon():

    return (
        "",
        204
    )


# ============================================================
# WEB
# ============================================================

@app.route("/")
def index():

    return jsonify({
        "status": "ok",
        "service": "StudyCards YouTube Transcript API"
    })


# ============================================================
# EJECUCIÓN
# ============================================================

if __name__ == "__main__":

    import os

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )