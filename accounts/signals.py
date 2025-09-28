# accounts/signals.py (최소/WS용)
import logging
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.core.cache import cache
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from logui import log_step

logger = logging.getLogger("accounts")
ACTIVE_WS = "active_ws:user:{uid}"

@receiver(user_logged_in)
def on_login(sender, user, request, **kwargs):
    prev = cache.get(ACTIVE_WS.format(uid=user.id))
    log_step(logger, "로그인 감지", "signals.on_login", {"user": user.id, "prev_chan": prev})
    if prev:
        async_to_sync(get_channel_layer().send)(prev, {
            "type": "force.logout",
            "reason": "다른 곳에서 로그인되었습니다.",
        })
        log_step(logger, "강제 로그아웃 전송", "channel_layer.send", {"to": prev})
