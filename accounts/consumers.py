# accounts/consumers.py (필요 부분만 수정/추가)
import logging
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache

from logui import log_banner_once, log_step

logger = logging.getLogger("accounts")
ACTIVE_WS = "active_ws:user:{uid}"

class AuthPresenceConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        log_banner_once(logger, key="auth-ws", title="코딩라인", subtitle="인증 WS 시작", self_obj=self)
        log_step(logger, " (사용자 세션 관리)WS 연결 시도", "accounts.AuthPresenceConsumer.connect", self_obj=self)

        user = self.scope.get("user", AnonymousUser())
        if not user.is_authenticated:
            log_step(logger, "비인증 사용자", "접속 거절", {"code": 4001}, self_obj=self)
            await self.close(code=4001)
            return

        self.user = user
        self.group = f"user_{user.id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        log_step(logger, "(사용자 세션 관리)WS 연결 성공", "accept()", {"user": user.id, "chan": self.channel_name}, self_obj=self)

        # 1) 캐시에 이전 채널이 있으면 그 채널로 직접 강제 종료
        prev = await sync_to_async(cache.get)(ACTIVE_WS.format(uid=user.id))
        log_step(logger, "이전 채널 조회", "cache.get", {"prev": prev}, self_obj=self)
        if prev and prev != self.channel_name:
            log_step(logger, "이전 채널 강제 종료 전송", "channel_layer.send", {"to": prev}, self_obj=self)
            await self.channel_layer.send(prev, {
                "type": "force.logout",
                "reason": "다른 탭/창에서 접속이 활성화되었습니다.",
            })

        # 2) ★ 백업 로직: 그룹 전체에 공지(자기 자신은 exclude)
        log_step(logger, "그룹 강제 종료 브로드캐스트", "group_send", {"group": self.group, "exclude": self.channel_name}, self_obj=self)
        await self.channel_layer.group_send(self.group, {
            "type": "force.logout",
            "reason": "다른 탭/창에서 접속이 활성화되었습니다.",
            "exclude": self.channel_name,
        })

        # 3) 현재 채널을 활성으로 저장(향후 신속 직통)
        await sync_to_async(cache.set)(ACTIVE_WS.format(uid=user.id), self.channel_name, 60 * 60 * 24)
        log_step(logger, "현재 채널 저장", "cache.set", {"key": ACTIVE_WS.format(uid=user.id)}, self_obj=self)

    async def disconnect(self, code):
        cur = await sync_to_async(cache.get)(ACTIVE_WS.format(uid=self.user.id))
        if cur == self.channel_name:
            await sync_to_async(cache.delete)(ACTIVE_WS.format(uid=self.user.id))
            log_step(logger, "활성 채널 삭제", "cache.delete", {"user": self.user.id}, self_obj=self)
        await self.channel_layer.group_discard(self.group, self.channel_name)
        log_step(logger, "(사용자 세션 관리)WS 연결 종료", "disconnect()", {"code": code, "chan": self.channel_name}, self_obj=self)

    async def force_logout(self, event):
        # 자기 자신은 제외
        if event.get("exclude") == self.channel_name:
            # log_step(logger, "강제 로그아웃 수신(자기자신 제외)", "force_logout()", {"skip": True}, self_obj=self)
            return

        log_step(logger, "강제 로그아웃 수신", "force_logout()", {"reason": event.get("reason")}, self_obj=self)
        await self.send_json({
            "event": "force_logout",
            "msg": event.get("reason", "다른 곳에서 로그인이 감지되어 로그아웃됩니다."),
        })
        await self.close(code=4002)
