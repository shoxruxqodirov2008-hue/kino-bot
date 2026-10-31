import os
import re
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = os.environ.get("OWNER_ID")
CHANNEL = (os.environ.get("CHANNEL") or "").lstrip("@").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
UPSTASH_URL = (os.environ.get("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

SITE_URL = "https://telegram-movie-bot.netlify.app"
SERVER_URL = "https://kino-bot-mlm9.onrender.com"
CATALOG_KEY = "catalog"


def fail(text, code):
    return jsonify({"ok": False, "error": text}), code


def redis(*command):
    r = requests.post(
        UPSTASH_URL,
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        json=list(command),
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("result")


def load_catalog():
    raw = redis("HGETALL", CATALOG_KEY) or []
    if isinstance(raw, dict):
        items = raw.items()
    else:
        items = zip(raw[0::2], raw[1::2])
    movies = []
    for msg_id, title in items:
        try:
            movies.append({"id": int(msg_id), "title": str(title)})
        except ValueError:
            continue
    return movies


def normalize(text):
    return re.sub(r"[^\w\s]", " ", text.lower())


def post_title(msg):
    text = (msg.get("caption") or msg.get("text") or "").strip()
    if text:
        first = text.splitlines()[0].strip()
        if first:
            return first[:120]
    for key in ("video", "document"):
        media = msg.get(key)
        if media and media.get("file_name"):
            name = media["file_name"].rsplit(".", 1)[0]
            return name.replace("_", " ").replace(".", " ").strip()[:120]
    return ""


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = SITE_URL
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/")
def home():
    return jsonify({
        "status": "success",
        "message": "KinoBot serveri ishlayapti!"
    })


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        return "forbidden", 403

    update = request.get_json(silent=True) or {}
    msg = update.get("channel_post") or update.get("edited_channel_post")

    if msg and (msg.get("video") or msg.get("document")):
        username = ((msg.get("chat") or {}).get("username") or "").lower()
        if CHANNEL and username == CHANNEL.lower():
            title = post_title(msg)
            if title:
                try:
                    redis("HSET", CATALOG_KEY, str(msg["message_id"]), title)
                except requests.RequestException:
                    app.logger.exception("Katalogga yozishda xatolik")
                    return "retry", 500

    return "ok"


@app.route("/setup-webhook")
def setup_webhook():
    if not WEBHOOK_SECRET or request.args.get("key") != WEBHOOK_SECRET:
        return "forbidden", 403
    if not BOT_TOKEN:
        return "BOT_TOKEN yo'q", 503
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json={
            "url": SERVER_URL + "/telegram-webhook",
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": ["channel_post", "edited_channel_post"],
        },
        timeout=15,
    )
    return jsonify(r.json())


@app.route("/remove")
def remove():
    if not WEBHOOK_SECRET or request.args.get("key") != WEBHOOK_SECRET:
        return "forbidden", 403
    post_id = str(request.args.get("id", "")).strip()
    if not post_id.isdigit():
        return "id kerak", 400
    redis("HDEL", CATALOG_KEY, post_id)
    return jsonify({"ok": True, "removed": post_id})


@app.route("/api/stats")
def stats():
    try:
        return jsonify({"ok": True, "count": redis("HLEN", CATALOG_KEY)})
    except requests.RequestException:
        return fail("Baza ishlamayapti.", 502)


@app.route("/api/suggest", methods=["POST", "OPTIONS"])
def suggest():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    movie = str(data.get("title") or data.get("movie") or "").strip()

    if not movie:
        return fail("Kino nomini kiriting.", 400)

    if not BOT_TOKEN or not OWNER_ID:
        return fail("Server sozlamalari hali tugallanmagan.", 503)

    text = "💡 Yangi kino taklifi:\n🎬 " + movie

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_ID, "text": text},
            timeout=15
        )
        r.raise_for_status()
        result = r.json()

        if not result.get("ok"):
            return fail("Telegram xabarni yubora olmadi.", 502)

        return jsonify({"ok": True, "message": "Taklif sizga yuborildi! Rahmat."})
    except requests.RequestException:
        app.logger.exception("Telegram xabarini yuborishda xatolik")
        return fail("Xabar yuborilmadi. Keyinroq urinib ko‘ring.", 502)


@app.route("/api/search", methods=["POST", "OPTIONS"])
def search():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or data.get("movie") or "").strip()

    words = normalize(query).split()[:6]
    if not words:
        return fail("Kino nomini kiriting.", 400)

    if not CHANNEL:
        return fail("Server sozlamalari hali tugallanmagan.", 503)

    try:
        movies = load_catalog()
    except (requests.RequestException, ValueError):
        app.logger.exception("Katalogni o'qishda xatolik")
        return fail("Katalog vaqtincha ishlamayapti. Birozdan so‘ng urinib ko‘ring.", 502)

    matches = [
        m for m in movies
        if all(w in normalize(m["title"]) for w in words)
    ]
    matches.sort(key=lambda m: m["id"], reverse=True)

    results = [
        {
            "title": m["title"],
            "url": f"https://t.me/{CHANNEL}/{m['id']}",
            "source": "Kanal",
        }
        for m in matches[:20]
    ]

    return jsonify({"ok": True, "results": results})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
