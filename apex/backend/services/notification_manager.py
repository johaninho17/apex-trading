import logging
from typing import Any, Dict
from services.notification_store import record_notification

# Global hub reference (injected from main.py)
_hub = None

logger = logging.getLogger("apex.notifications")

def set_hub(hub):
    """Inject the WebSocketHub instance."""
    global _hub
    _hub = hub

async def broadcast(channel: str, event_type: str, payload: Dict[str, Any]):
    """
    Send a real-time notification to all connected clients.
    
    Args:
        channel: 'system', 'dfs', 'kalshi', 'alpaca'
        event_type: 'alert', 'toast', 'update'
        payload: Dict with data
    """
    logger.info(f"NotificationManager: Broadcasting {event_type} to {channel} (Hub connected: {_hub is not None})")
    notif_summary = None
    try:
        notif_summary = await record_notification(channel=channel, event_type=event_type, payload=payload or {})
    except Exception as e:
        logger.warning(f"NotificationManager: Failed to persist notification: {e}")
    outgoing_payload = dict(payload or {})
    if isinstance(notif_summary, dict):
        outgoing_payload["notification"] = notif_summary
    if _hub:
        await _hub.broadcast(channel, event_type, outgoing_payload)
    else:
        logger.warning(f"NotificationManager: Hub not connected. Dropping {event_type}")

async def send_toast(title: str, message: str, type: str = "info", duration: int = 5000):
    """Helper to send a toast notification to the frontend."""
    await broadcast("system", "toast", {
        "title": title,
        "message": message,
        "type": type,  # info, success, warning, error
        "duration": duration,
    })

async def send_log(message: str, level: str = "info"):
    """Stream a log line to the specialized log terminal."""
    await broadcast("system", "log", {
        "message": message,
        "level": level,
        "timestamp": 0  # Frontend will timestamp it
    })
