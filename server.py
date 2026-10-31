import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = os.environ.get("OWNER_ID")
SITE_URL = "https://telegram-movie-bot.netlify.app"

MOVIES = [
    "Interstellar",
    "Inception",
    "Titanic",
    "Avatar",
    "The Dark Knight",
    "Forrest Gump",
    "The Matrix",
    "Home Alone",
]


def fail(text, code):
    return jsonify({"ok": False, "error": text}), code


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

    if not query:
        return fail("Kino nomini kiriting.", 400)

    results = [
        {"title": m}
        for m in MOVIES
        if query.casefold() in m.casefold()
    ]

    return jsonify({"ok": True, "results": results})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
