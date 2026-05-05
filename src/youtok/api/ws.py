import asyncio
import json
from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from youtok.db.base import SessionLocal
from youtok.db.models import Job

subscribers: dict[int, set[WebSocket]] = defaultdict(set)


def register_ws(app: FastAPI):
    @app.websocket("/ws/jobs/{job_id}")
    async def ws_job(ws: WebSocket, job_id: int):
        await ws.accept()
        subscribers[job_id].add(ws)
        try:
            while True:
                await asyncio.sleep(60)
                await ws.send_text(json.dumps({"ping": True}))
        except WebSocketDisconnect:
            subscribers[job_id].discard(ws)


async def broadcast_progress(job_id: int, payload: dict):
    dead = set()
    for ws in subscribers[job_id]:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.add(ws)
    subscribers[job_id] -= dead


async def progress_watcher():
    last_state: dict[int, tuple] = {}
    while True:
        await asyncio.sleep(1)
        try:
            with SessionLocal() as db:
                running = db.query(Job).filter(
                    Job.status.notin_(["done", "failed"])
                ).all()
                for j in running:
                    key = (j.status, j.progress_pct)
                    if last_state.get(j.id) != key:
                        last_state[j.id] = key
                        await broadcast_progress(j.id, {
                            "step": j.current_step or j.status,
                            "pct": j.progress_pct,
                            "message": j.error_message or "",
                            "status": j.status,
                        })
        except Exception:
            pass
