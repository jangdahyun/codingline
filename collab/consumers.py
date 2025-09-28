import asyncio
import json
import logging
from math import log
import time  # ✅ 버전 스탬프용
from logui import log_banner_once, log_step

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async, async_to_sync
from django.db import transaction
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone
from typing import Optional

from .models import Room, RoomMember, Message

logger = logging.getLogger("collab")

SHOW_BANNER = True
GRACE_SECONDS = 10
EMPTY_ROOM_SILENT = True


class RoomPresenceConsumer(AsyncJsonWebsocketConsumer):
    """
    - connect: slug로 방 로드, 정책 검사, 그룹 조인
    - disconnect: 지연 정리(유예 후 open_conn 0이면 실제 나감 처리)
    - receive_json: 클라→서버 액션(chat, typing.*, leave)
    - chat_message / image / image_message / typing_event / kicked / room_event: 서버/뷰→클라 브로드캐스트
    """

    async def connect(self):
        # 배너/로그
        if SHOW_BANNER:
            log_banner_once(logger, key="ws-start", title="코딩라인", subtitle="실시간 접속 시작", self_obj=self)
        log_step(logger, "WS 연결 시도", "connect()", {"상태": "시작"}, self_obj=self)

        # 1) URL에서 slug 추출 + 유저 확인
        self.slug = self.scope["url_route"]["kwargs"]["slug"]
        self.user = self.scope.get("user", AnonymousUser())
        if not self.user.is_authenticated:
            await self.close(code=4001)  # 비로그인
            return

        # 2) 방 로드
        room = await self._get_room(self.slug)
        if not room:
            await self.close(code=4404)  # 방 없음
            return
        self.room = room

        # 3) 입장 정책 검사 + open_conn += 1 (0→1일 때 on_commit에서만 join 브로드캐스트)
        ok, reason = await self._inc_conn(room.id, self.user.id)   # ← (bool, reason) 받음
        if not ok:
            log_step(logger, "입장 거절", "can_enter=False", {
                "room": room.id, "user": self.user.id, "reason": reason  # ← 왜 거절됐는지 로그
            }, self_obj=self)
            await self.close(code=4403)  # ← 강퇴/밴/권한없음 등 명확한 코드
            return

        # 4) 그룹 조인
        self.group = f"room_{room.pk}"
        self.user_group = f"room_{room.pk}_user_{self.user.pk}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.channel_layer.group_add(self.user_group, self.channel_name)

        # 5) 연결 수락
        await self.accept()
        self.left_explicitly = False
        log_step(logger, "입장", "accept()", {"방": self.room.id, "유저": self.user.id, "그룹": self.group}, self_obj=self)

        # 6) ✅ 현재 접속자 스냅샷(본인에게만 1회 전송) — 버전 포함
        members = await self._active_users(self.room.id)  # [{user_id, username, is_owner}, ...]
        await self.send_json({
            "event": "presence_snapshot",
            "version": int(time.time() * 1000),  # 최신 스냅샷 식별용
            "members": members,
        })

        # ❌ 여기서 별도 user_joined를 쏘지 않습니다.
        #    실제 join 브로드캐스트는 _inc_conn() 트랜잭션 커밋 후 on_commit에서 0→1일 때만 쏨.

    async def disconnect(self, code):
        logging.debug("WS disconnect start: code=%s", code)

        # 명시적 퇴장이 아니면 유예 후 정리
        room_id = getattr(self, "room", None).id if hasattr(self, "room") else None
        user_id = getattr(self, "user", None).id if hasattr(self, "user") else None
        if room_id and user_id and not getattr(self, "left_explicitly", False):
            asyncio.create_task(self._delayed_cleanup(room_id, user_id))

        # 그룹 해제
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

        logger.debug("WS disconnect: code=%s", code)

    # 현재 방에서 open_conn>0 & 밴 아님 → 유저 목록 (+ is_owner)
    @sync_to_async
    def _active_users(self, room_id: int):
        room = Room.objects.only("id", "created_by_id").get(pk=room_id)
        qs = (RoomMember.objects
              .filter(room_id=room_id, is_banned=False, open_conn__gt=0)
              .select_related("user")
              .values("user_id", "user__username"))
        return [
            {
                "user_id": x["user_id"],
                "username": x["user__username"],
                "is_owner": (x["user_id"] == room.created_by_id),
            }
            for x in qs
        ]

    # ─────────────── 클라 → 서버 ───────────────
    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        if not action:
            logger.debug("[recv] action missing: %s", content)
            return
        if not hasattr(self, "group"):
            return

        # 1) 채팅
        if action == "chat":
            text = (content.get("message") or "").trim() if hasattr(str, "trim") else (content.get("message") or "").strip()
            if not text:
                return
            log_step(logger, "메시지 수신", "chat", {"길이": len(text)}, self_obj=self)
            msg_id = await self._save_text_message(self.room.id, self.user.id, text)
            await self.channel_layer.group_send(
                self.group,
                {
                    "type": "chat.message",
                    "message": text,
                    "sender": getattr(self.user, "username", "user"),
                    "message_id": msg_id,
                    "ts": timezone.now().isoformat(),
                },
            )
            return

        # 2) 타이핑 표시
        if action == "typing.start":
            await self.channel_layer.group_send(
                self.group,
                {"type": "typing.event", "status": "start",
                 "user_id": self.user.id, "username": getattr(self.user, "username", "user")}
            )
            return
        if action == "typing.stop":
            await self.channel_layer.group_send(
                self.group,
                {"type": "typing.event", "status": "stop",
                 "user_id": self.user.id, "username": getattr(self.user, "username", "user")}
            )
            return

        # 3) 명시적 퇴장
        if action == "leave":
            log_step(logger, "명시적 퇴장", "leave", {"유저": self.user.id}, self_obj=self)
            self.left_explicitly = True
            await sync_to_async(self._finalize_leave_immediately)(self.room.id, self.user.id)
            await self.close(code=4000)
            return

        logger.debug("[recv] unknown action: %s", action)

    # ─────────────── 서버/뷰 → 클라 ───────────────
    async def chat_message(self, event):
        await self.send_json({
            "event": "chat",
            "user": event.get("sender", "server"),
            "message": event["message"],
            "message_id": event.get("message_id"),
            "ts": event.get("ts") or timezone.now().isoformat(),
        })

    async def image(self, event):
        await self.send_json({
            "event": "image",
            "user": event.get("user", "server"),
            "image_url": event["image_url"],
            "caption": event.get("caption", ""),
            "message_id": event.get("message_id"),
            "ts": event.get("ts") or timezone.now().isoformat(),
        })

    async def image_message(self, event):
        await self.send_json({
            "event": "image",
            "user": event.get("sender", "server"),
            "image_url": event["image_url"],
            "caption": event.get("caption", ""),
            "message_id": event.get("message_id"),
            "ts": event.get("ts") or timezone.now().isoformat(),
        })

    async def typing_event(self, event):
        await self.send_json({
            "event": "typing",
            "status": event.get("status"),
            "user_id": event.get("user_id"),
            "username": event.get("username"),
            "ts": timezone.now().isoformat(),
        })

    async def kicked(self, event):
        await self.send_json({"event": "kicked", "msg": event.get("msg", "강퇴되었습니다.")})
        await self.close(code=4403)

    async def room_closed(self, event):
        #  "방이 삭제됨" 이벤트 보내고
        await self.send_json({
            "event": "room_closed",                # 클라 switch(data.event)에서 받도록
            "msg": event.get("msg", "방이 삭제되었습니다."),
            "slug": event.get("slug"),            # 어떤 방인지 식별용
        })
        # 곧바로 소켓을 닫아 onclose에서 리다이렉트가 실행되도록
        await self.close(code=4404)  

    # ✅ room.event → 그대로 전달(공통 브리지)
    async def room_event(self, event):
        await self.send_json(event["payload"])

    # ─────────────── 지연 정리(유예 후 최종) ───────────────
    async def _delayed_cleanup(self, room_id: int, user_id: int):
        await self._dec_open_conn_only(room_id, user_id)
        await asyncio.sleep(GRACE_SECONDS)
        await sync_to_async(self._finalize_leave_if_still_gone)(room_id, user_id)

    # ─────────────── DB helpers ───────────────
    @sync_to_async
    def _get_room(self, slug):
        try:
            return Room.objects.get(slug=slug)
        except Room.DoesNotExist:
            return None

    @sync_to_async
    def _inc_conn(self, room_id: int, user_id: int) -> bool:
        log_step(logger, "접속 증가 시도", "open_conn += 1", {"room": room_id, "user": user_id}, self_obj=self)

        User = get_user_model()
        user = User.objects.get(pk=user_id)
        room = Room.objects.get(pk=room_id)

        ok, reason = room.can_enter(user)
        if not ok:
            log_step(logger, "입장 정책 거절", "can_enter=False", {
                "room": room.id, "user": user.id, "reason": reason
            }, self_obj=self)
            return False, (reason or "입장할 수 없습니다.") 

        with transaction.atomic():
            is_owner = (room.created_by_id == user.id)
            mem, created = RoomMember.objects.select_for_update().get_or_create(
                room=room, user=user,
                defaults={"role": RoomMember.ROLE_OWNER if is_owner else RoomMember.ROLE_MEMBER},
            )
            if is_owner and mem.role != RoomMember.ROLE_OWNER:
                mem.role = RoomMember.ROLE_OWNER

            was_inactive = (mem.open_conn == 0)
            mem.open_conn = F("open_conn") + 1
            mem.save(update_fields=["role", "open_conn"])

            def _broadcast_join():
                if was_inactive:
                    async_to_sync(self.channel_layer.group_send)(
                        f"room_{room.id}",
                        {"type": "room.event",
                         "payload": {
                             "event": "user_joined",
                             "room_id": room.id,
                             "user_id": user.id,
                             "username": getattr(user, "username", "user"),
                             "is_owner": is_owner,                   # ✅ 정확한 방장 여부
                             "version": int(time.time() * 1000),     # ✅ 버전 스탬프
                         }}
                    )
            transaction.on_commit(_broadcast_join)
        return True,None

    @sync_to_async
    def _dec_open_conn_only(self, room_id: int, user_id: int):
        try:
            with transaction.atomic():
                room = Room.objects.select_for_update().get(pk=room_id)
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                (RoomMember.objects
                 .select_for_update()
                 .filter(room=room, user=user, open_conn__gt=0)
                 .update(open_conn=F("open_conn") - 1))
        except Room.DoesNotExist:
            return

    # 명시적 퇴장 (즉시)
    def _finalize_leave_immediately(self, room_id: int, user_id: int):
        try:
            owner_changed_payload = None
            with transaction.atomic():
                room = Room.objects.select_for_update().get(pk=room_id)
                room_slug = room.slug
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                mem = RoomMember.objects.select_for_update().filter(room=room, user=user).first()

                # 연결 감소
                if mem and mem.open_conn > 0:
                    mem.open_conn = F("open_conn") - 1
                    mem.save(update_fields=["open_conn"])
                    mem.refresh_from_db(fields=["open_conn"])
                    if mem.open_conn > 0:
                        return  # 다른 탭 남아있음

                # 진짜 퇴장 처리
                if mem:
                    if mem.role == RoomMember.ROLE_OWNER:
                        new_owner = room.transfer_ownership_to_earliest(demote_previous=False)
                        mem.delete()
                        if new_owner:
                            owner_changed_payload = {
                                "event": "owner_changed",
                                "room_id": room.id,
                                "new_owner_id": new_owner.user_id,
                                "new_owner_name": new_owner.user.username,
                            }
                    else:
                        if mem.is_banned:
                            RoomMember.objects.filter(pk=mem.pk).update(open_conn=0)
                        else:
                            mem.delete()

                has_active = RoomMember.objects.filter(room=room, is_banned=False, open_conn__gt=0).exists()
                has_owner = RoomMember.objects.filter(room=room, role=RoomMember.ROLE_OWNER).exists()
                if not has_active and not has_owner:
                    room.delete()
                    def _broadcast_room_closed():
                        async_to_sync(self.channel_layer.group_send)(
                            f"room_{room_id}",
                            {"type": "room.closed", "msg": "방이 삭제되었습니다.","slug": room_slug}
                        )
                        async_to_sync(self.channel_layer.group_send)(
                            "lobby",
                            {"type": "lobby.event", 
                             "payload": { "event": "room_closed","room_id": room_id, "slug": room_slug}} 
                        )
                    transaction.on_commit(_broadcast_room_closed)


                def _broadcast():
                    ver = int(time.time() * 1000)  # ✅ 버전 스탬프
                    if owner_changed_payload:
                        owner_changed_payload["version"] = ver
                        async_to_sync(self.channel_layer.group_send)(
                            f"room_{room.id}", {"type": "room.event", "payload": owner_changed_payload}
                        )
                    async_to_sync(self.channel_layer.group_send)(
                        f"room_{room_id}",
                        {"type": "room.event", "payload": {
                            "event": "user_left",
                            "room_id": room_id,
                            "user_id": user_id,
                            "username": getattr(user, "username", "user"),
                            "version": ver,  # ✅ 추가
                        }}
                    )
                transaction.on_commit(_broadcast)
        except Room.DoesNotExist:
            return

    # 유예 후 최종 판정
    def _finalize_leave_if_still_gone(self, room_id: int, user_id: int):
        try:
            owner_changed_payload = None
            user_left_payload = None
            with transaction.atomic():
                room = Room.objects.select_for_update().get(pk=room_id)
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                mem = RoomMember.objects.select_for_update().filter(room=room, user=user).first()

                if not mem:
                    user_left_payload = {
                        "event": "user_left",
                        "room_id": room_id, "user_id": user_id,
                        "username": getattr(user, "username", "user"),
                    }
                else:
                    if mem.open_conn > 0:
                        return  # 재접속
                    if mem.role == RoomMember.ROLE_OWNER:
                        new_owner = room.transfer_ownership_to_earliest(demote_previous=False)
                        mem.delete()
                        if new_owner:
                            owner_changed_payload = {
                                "event": "owner_changed",
                                "room_id": room.id,
                                "new_owner_id": new_owner.user_id,
                                "new_owner_name": new_owner.user.username,
                            }
                    else:
                        if mem.is_banned:
                            RoomMember.objects.filter(pk=mem.pk).update(open_conn=0)
                        else:
                            mem.delete()

                    user_left_payload = {
                        "event": "user_left",
                        "room_id": room_id, "user_id": user_id,
                        "username": getattr(user, "username", "user"),
                    }

                has_active = RoomMember.objects.filter(room=room, is_banned=False, open_conn__gt=0).exists()
                has_owner = RoomMember.objects.filter(room=room, role=RoomMember.ROLE_OWNER).exists()
                if not has_active and not has_owner:
                    room.delete()
                    def _broadcast_room_closed():
                        # 아직 방에 남아있는 사람들(있다면)에게도 알림
                        async_to_sync(self.channel_layer.group_send)(
                            f"room_{room_id}",
                            {"type": "room.closed", "msg": "방이 삭제되었습니다.", "slug": room_slug}
                        )
                        # 로비에 알림
                        async_to_sync(self.channel_layer.group_send)(
                            "lobby",
                            {"type": "lobby.event",
                            "payload": {"event": "room_closed", "room_id": room_id, "slug": room_slug}}
                        )
                    transaction.on_commit(_broadcast_room_closed)

                def _broadcast_after_commit():
                    ver = int(time.time() * 1000)  # 버전 스탬프
                    if owner_changed_payload:
                        owner_changed_payload["version"] = ver
                        async_to_sync(self.channel_layer.group_send)(
                            f"room_{room.id}", {"type": "room.event", "payload": owner_changed_payload}
                        )
                    if user_left_payload:
                        user_left_payload["version"] = ver
                        async_to_sync(self.channel_layer.group_send)(
                            f"room_{room_id}", {"type": "room.event", "payload": user_left_payload}
                        )
                transaction.on_commit(_broadcast_after_commit)
        except Room.DoesNotExist:
            return

    # 메시지 저장(옵션)
    @sync_to_async
    def _save_text_message(self, room_id: int, user_id: int, message: str) -> Optional[int]:
        try:
            room = Room.objects.get(pk=room_id)
            user = get_user_model().objects.get(pk=user_id)
            m = Message.objects.create(room=room, user=user, content=message)
            return m.pk
        except Exception as e:
            logger.exception("save_text_message failed: %s", e)
            return None

    @sync_to_async
    def _save_image_message(self, room_id: int, user_id: int, image_url: str, caption: str) -> Optional[int]:
        try:
            room = Room.objects.get(pk=room_id)
            user = get_user_model().objects.get(pk=user_id)
            m = Message.objects.create(
                room=room, user=user,
                content=json.dumps({"url": image_url, "caption": caption}, ensure_ascii=False),
            )
            return m.pk
        except Exception as e:
            logger.exception("save_image_message failed: %s", e)
            return None
        
    


class LobbyConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("lobby", self.channel_name)
        await self.accept()
        self.left_explicitly = False
        log_step(logger, "로비 WS 연결 성공", "lobby.connect", {"chan": self.channel_name}, self_obj=self)

    async def disconnect(self, code):
        await self.channel_layer.group_discard("lobby", self.channel_name)
        log_step(logger, "로비 WS 연결 종료", "lobby.disconnect", {"code": code, "chan": self.channel_name}, self_obj=self)

    async def lobby_event(self, event):
        payload = event["payload"]
        await self.send(text_data=json.dumps(payload))
        log_step(logger, "로비 이벤트 전송", "lobby.event", {"payload": payload}, self_obj=self)
