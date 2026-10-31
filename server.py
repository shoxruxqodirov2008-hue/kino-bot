
import os
import re
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")
CHANNEL = (os.getenv("CHANNEL") or "").replace("@", "").replace("https://t.me/", "").strip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
UPSTASH_URL = (os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

SITE_URL = "https://telegram-movie-bot.netlify.app"
SERVER_URL = "https://kino-bot-mlm9.onrender.com"
CATALOG_KEY = "catalog"


def redis(*command):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise RuntimeError("Upstash URL yoki TOKEN sozlanmagan")

    response = requests.post(
        UPSTASH_URL,
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        json=list(command),
        timeout=15
    )
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(data["error"])

    return data.get("result")


def normalize(text):
    return re.sub(r"[^\w\s]", " ", text.lower())


def post_title(msg):
    text = (msg.get("caption") or msg.get("text") or "").strip()

    if text:
        return text.splitlines()[0].strip()[:120]

    for key in ("video", "document"):
        media = msg.get(key)
        if media:
            name = media.get("file_name") or ""
            if name:
                return name.rsplit(".", 1)[0].replace("_", " ")[:120]

    return ""


def load_catalog():
    raw = redis("HGETALL", CATALOG_KEY) or []
    if isinstance(raw, dict):
        pairs = raw.items()
    else:
        pairs = zip(raw[0::2], raw[1::2])

    movies = []
    for msg_id, title in pairs:
        try:
            movies.append({"id": int(msg_id), "title": str(title)})
        except (ValueError, TypeError):
            continue

    return movies


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = SITE_URL
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "message": "KinoBot serveri ishlayapti",
        "channel_configured": bool(CHANNEL),
        "redis_configured": bool(UPSTASH_URL and UPSTASH_TOKEN),
        "webhook_configured": bool(WEBHOOK_SECRET)
    })


@app.route("/api/stats")
def stats():
    try:
        return jsonify({"ok": True, "count": len(load_catalog())})
    except Exception as e:
        app.logger.exception("STATS ERROR")
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if not WEBHOOK_SECRET:
        app.logger.error("WEBHOOK_SECRET sozlanmagan")
        return "Webhook secret missing", 503

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        app.logger.warning("Webhook secret mos kelmadi")
        return "Forbidden", 403

    update = request.get_json(silent=True) or {}

    msg = (
        update.get("channel_post")
        or update.get("edited_channel_post")
    )

    if not msg:
        app.logger.info("Webhook keldi, lekin channel_post yo'q")
        return "ok"

    chat = msg.get("chat") or {}
    username = (chat.get("username") or "").lower()

    app.logger.info(
        "KANAL POST: username=%s, message_id=%s, video=%s, document=%s",
        username,
        msg.get("message_id"),
        bool(msg.get("video")),
        bool(msg.get("document"))
    )

    if username != CHANNEL.lower():
        app.logger.warning(
            "Kanal mos kelmadi: kelgan=%s, sozlangan=%s",
            username, CHANNEL
        )
        return "ok"

    if not (msg.get("video") or msg.get("document")):
        app.logger.info("Post video yoki document emas")
        return "ok"

    title = post_title(msg)
    if not title:
        app.logger.warning("Postdan kino nomi topilmadi")
        return "ok"

    try:
        redis("HSET", CATALOG_KEY, str(msg["message_id"]), title)
        app.logger.info("KINO KATALOGGA SAQLANDI: %s", title)
    except Exception:
        app.logger.exception("REDISGA SAQLASHDA XATO")
        return "retry", 500

    return "ok"


@app.route("/setup-webhook")
def setup_webhook():
    if not WEBHOOK_SECRET or request.args.get("key") != WEBHOOK_SECRET:
        return "Forbidden", 403

    if not BOT_TOKEN:
        return "BOT_TOKEN sozlanmagan", 503

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={
                "url": SERVER_URL + "/telegram-webhook",
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": [
                    "channel_post",
                    "edited_channel_post"
                ]
            },
            timeout=15
        )
        return jsonify(response.json())
    except requests.RequestException as e:
        app.logger.exception("WEBHOOK O'RNATISHDA XATO")
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/search", methods=["POST", "OPTIONS"])
def search():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or data.get("movie") or "").strip()

    words = normalize(query).split()[:6]
    if not words:
        return jsonify({"ok": False, "message": "Kino nomini kiriting"}), 400

    try:
        movies = load_catalog()
        matches = [
            m for m in movies
            if all(w in normalize(m["title"]) for w in words)
        ]
        matches.sort(key=lambda m: m["id"], reverse=True)

        results = [
            {
                "title": m["title"],
                "url": f"https://t.me/{CHANNEL}/{m['id']}",
                "source": "Kanal"
            }
            for m in matches[:20]
        ]

        return jsonify({"ok": True, "results": results})

    except Exception as e:
        app.logger.exception("SEARCH ERROR")
        return jsonify({"ok": False, "message": str(e)}), 502


@app.route("/api/suggest", methods=["POST", "OPTIONS"])
def suggest():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    movie = str(data.get("movie") or "").strip()

    if not movie:
        return jsonify({"ok": False, "message": "Kino nomini kiriting"}), 400

    if not BOT_TOKEN or not OWNER_ID:
        return jsonify({"ok": False, "message": "Bot sozlamalari yo'q"}), 503

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": OWNER_ID,
                "text": "💡 Yangi kino taklifi:\n🎬 " + movie
            },
            timeout=15
        )
        response.raise_for_status()

        if not response.json().get("ok"):
            return jsonify({"ok": False, "message": "Telegram xabar yubormadi"}), 502

        return jsonify({"ok": True, "message": "Taklif yuborildi!"})

    except Exception:
        app.logger.exception("SUGGEST ERROR")
        return jsonify({"ok": False, "message": "Xabar yuborishda xato"}), 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
