import os
import time
import secrets
import asyncio

import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

PUBLIC_URL = os.getenv(
    "PUBLIC_URL",
    "https://your-app.onrender.com",
)

LINK_TTL = 60 * 60  # 60 minutes


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Temporary Telegram Video Links"
)


# ============================================================
# TEMPORARY STORAGE
# ============================================================

VIDEOS = {}


# Structure:
#
# VIDEOS[token] = {
#     "file_id": "...",
#     "expires": 1234567890,
#     "mime": "video/mp4"
# }


# ============================================================
# CLEAN EXPIRED LINKS
# ============================================================

def cleanup_expired():
    now = time.time()

    expired = [
        token
        for token, data in VIDEOS.items()
        if data["expires"] <= now
    ]

    for token in expired:
        del VIDEOS[token]


# ============================================================
# TELEGRAM BOT
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Temporary Video Link Bot\n\n"
        "Send me a video and I will give you "
        "a temporary browser link."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Send a video to this bot.\n\n"
        "The bot will return a temporary link "
        "that expires after 60 minutes."
    )


async def handle_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    video = update.message.video

    if not video:
        return

    # Remove old links first
    cleanup_expired()

    # Generate random temporary ID
    token = secrets.token_urlsafe(16)

    # Save Telegram file_id
    VIDEOS[token] = {
        "file_id": video.file_id,
        "expires": time.time() + LINK_TTL,
        "mime": video.mime_type or "video/mp4",
    }

    link = (
        PUBLIC_URL.rstrip("/")
        + "/v/"
        + token
    )

    await update.message.reply_text(
        "Temporary link:\n\n"
        + link
        + "\n\n"
        "Expires in 60 minutes."
    )


async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    document = update.message.document

    if not document:
        return

    mime = document.mime_type or ""

    # Only accept video files
    if not mime.startswith("video/"):
        await update.message.reply_text(
            "Please send a video file."
        )
        return

    cleanup_expired()

    token = secrets.token_urlsafe(16)

    VIDEOS[token] = {
        "file_id": document.file_id,
        "expires": time.time() + LINK_TTL,
        "mime": mime,
    }

    link = (
        PUBLIC_URL.rstrip("/")
        + "/v/"
        + token
    )

    await update.message.reply_text(
        "Temporary link:\n\n"
        + link
        + "\n\n"
        "Expires in 60 minutes."
    )


# ============================================================
# TELEGRAM FILE URL
# ============================================================

async def get_telegram_file_url(
    file_id: str,
):

    api_url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getFile"
    )

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        response = await client.get(
            api_url,
            params={
                "file_id": file_id
            },
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError(
                "Telegram getFile failed."
            )

        file_path = (
            data["result"]["file_path"]
        )

    return (
        f"https://api.telegram.org/"
        f"file/bot{BOT_TOKEN}/"
        f"{file_path}"
    )


# ============================================================
# VIDEO PAGE
# ============================================================

@app.get(
    "/v/{token}",
    response_class=HTMLResponse,
)
async def video_page(token: str):

    cleanup_expired()

    data = VIDEOS.get(token)

    if not data:
        raise HTTPException(
            status_code=404,
            detail="Link expired or not found.",
        )

    remaining = int(
        data["expires"] - time.time()
    )

    minutes = max(
        1,
        remaining // 60
    )

    video_url = (
        f"/stream/{token}"
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta
            name="viewport"
            content="width=device-width,
                     initial-scale=1.0"
        >

        <title>Video</title>

        <style>
            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                min-height: 100vh;

                display: flex;
                align-items: center;
                justify-content: center;

                background: #0f0f0f;
                color: #ffffff;

                font-family:
                    Arial,
                    Helvetica,
                    sans-serif;

                padding: 20px;
            }}

            .container {{
                width: 100%;
                max-width: 900px;
            }}

            video {{
                width: 100%;
                max-height: 80vh;

                display: block;

                background: #000;

                border-radius: 12px;
            }}

            .info {{
                margin-top: 14px;

                color: #aaa;
                font-size: 14px;

                text-align: center;
            }}
        </style>
    </head>

    <body>

        <div class="container">

            <video
                controls
                playsinline
                preload="metadata"
                src="{video_url}"
            ></video>

            <div class="info">
                Temporary link ·
                expires in approximately
                {minutes} minutes
            </div>

        </div>

    </body>
    </html>
    """

    return HTMLResponse(html)


# ============================================================
# STREAM VIDEO
# ============================================================

@app.get("/stream/{token}")
async def stream_video(token: str):

    cleanup_expired()

    data = VIDEOS.get(token)

    if not data:
        raise HTTPException(
            status_code=404,
            detail="Link expired or not found.",
        )

    # Get fresh Telegram file URL
    try:

        telegram_url = (
            await get_telegram_file_url(
                data["file_id"]
            )
        )

    except Exception:

        raise HTTPException(
            status_code=502,
            detail="Could not access Telegram file.",
        )

    async def generate():

        async with httpx.AsyncClient(
            timeout=None
        ) as client:

            async with client.stream(
                "GET",
                telegram_url,
            ) as response:

                if response.status_code != 200:
                    return

                async for chunk in response.aiter_bytes(
                    chunk_size=1024 * 1024
                ):
                    yield chunk

    return StreamingResponse(
        generate(),
        media_type=data["mime"],
        headers={
            "Cache-Control": "no-store",
            "Accept-Ranges": "bytes",
        },
    )


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():

    return {
        "status": "online",
        "service": "Temporary Telegram Video Link Bot",
        "link_expiry": "60 minutes",
    }


# ============================================================
# BOT RUNNER
# ============================================================

async def run_bot():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    bot_app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    bot_app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    bot_app.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    bot_app.add_handler(
        MessageHandler(
            filters.VIDEO,
            handle_video,
        )
    )

    bot_app.add_handler(
        MessageHandler(
            filters.Document.VIDEO,
            handle_document,
        )
    )

    await bot_app.initialize()
    await bot_app.start()

    await bot_app.updater.start_polling()

    try:

        while True:
            await asyncio.sleep(3600)
            cleanup_expired()

    finally:

        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


# ============================================================
# START BOTH WEB SERVER + BOT
# ============================================================

@app.on_event("startup")
async def startup_event():

    app.state.bot_task = asyncio.create_task(
        run_bot()
    )

@app.on_event("shutdown")
async def shutdown_event():

    task = getattr(
        app.state,
        "bot_task",
        None,
    )

    if task:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass
