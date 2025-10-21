import asyncio
import json
import logging
from operator import ne
import time
from collections import defaultdict
from typing import Optional, Dict, List

from asgiref.sync import sync_to_async, async_to_sync
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Room, RoomMember, Message

logger = logging.getLogger("collab")

SHOW_BANNER = True
GRACE_SECONDS = 10
EMPTY_ROOM_SILENT = True

# ========= [ADD] 드로잉 스토어 =========
# 메모리 구현(개발용). 실제 서비스에선 Redis/Cache 사용 권장.
# 구조: DRAW_STORE[room_id][image_id] = [
#   {"path_id": str, "color": str, "size": int|float, "mode": "pen"|"eraser", "points": [{"x":float,"y":float}, ...]}
# ]
DRAW_STORE: Dict[int, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))


class RoomPresenceConsumer(AsyncJsonWebsocketConsumer):
    """
    - connect: slug→방 로드, owner id 세팅, 그룹조인, 스냅샷 전송
    - receive_json: chat, draw.stroke/clear/request_snapshot, image.*(propose/approved/rejected/goto)
    - room_event: 모든 커스텀 payload 브릿지
    - image: 업로드 API가 group_send(type="image") 보낸 케이스 처리
    """

    async def connect(self):
        if SHOW_BANNER:
            logger.info("┌────────────────────────────────────────────────────────────────────────────┐")
            logger.info("│   코딩라인 — 실시간 접속 시작                                              │")
            logger.info("└────────────────────────────────────────────────────────────────────────────┘")
        logger.info("[단계] WS 연결 시도 connect()")

        # 1) URL params + 유저
        self.slug = self.scope["url_route"]["kwargs"]["slug"]
        self.user = self.scope.get("user", AnonymousUser())
        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        # 2) 방
        room = await self._get_room(self.slug)
        if not room:
            await self.close(code=4404)
            return
        self.room = room
        self.owner_id = room.created_by_id  # 방장 id 보관
        self.was_owner = (self.user.id == self.owner_id)   #

        # 3) 입장 정책/카운트
        ok, reason = await self._inc_conn(room.id, self.user.id)
        if not ok:
            logger.info("입장 거절: %s", reason)
            await self.close(code=4403)
            return

        # 4) 그룹
        self.group = f"room_{room.pk}"
        self.user_group = f"room_{room.pk}_user_{self.user.pk}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.channel_layer.group_add(self.user_group, self.channel_name)

        # 5) 수락
        await self.accept()
        self.left_explicitly = False
        logger.info("[단계] 입장 accept() room=%s user=%s", self.room.id, self.user.id)

        # 6) 현재 접속자 스냅샷(본인에게만)
        members = await self._active_users(self.room.id)
        await self.send_json({
            "event": "presence_snapshot",
            "version": int(time.time() * 1000),
            "members": members,
        })

    # collab/consumers.py

    async def disconnect(self, code):
        room_id = getattr(self, "room", None).id if hasattr(self, "room") else None
        user_id = getattr(self, "user", None).id if hasattr(self, "user") else None
        if room_id and user_id and not getattr(self, "left_explicitly", False):
            # leave 액션 없이 끊긴 경우 → 모두 동일하게 유예 후 정리
            delay = GRACE_SECONDS
            asyncio.create_task(self._delayed_cleanup(room_id, user_id, delay))

        group = getattr(self, "group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)
        self.group = None

        user_group = getattr(self, "user_group", None)
        if user_group:
            await self.channel_layer.group_discard(user_group, self.channel_name)
        self.user_group = None

        logger.debug("WS disconnect code=%s", code)



    # ─────────────── DB helpers ───────────────
    @sync_to_async
    def _get_room(self, slug):
        try:
            return Room.objects.get(slug=slug)
        except Room.DoesNotExist:
            return None

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

    @sync_to_async
    def _inc_conn(self, room_id: int, user_id: int):
        User = get_user_model()
        user = User.objects.get(pk=user_id)
        room = Room.objects.get(pk=room_id)

        ok, reason = room.can_enter(user)
        if not ok:
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
                             "is_owner": is_owner,
                             "version": int(time.time() * 1000),
                         }}
                    )
            transaction.on_commit(_broadcast_join)
        return True, None

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

    def _finalize_leave_immediately(self, room_id: int, user_id: int):
        try:
            owner_changed_payload = None
            with transaction.atomic():
                room = Room.objects.select_for_update().get(pk=room_id)
                room_slug = room.slug
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                mem = RoomMember.objects.select_for_update().filter(room=room, user=user).first()

                if mem and mem.open_conn > 0:
                    mem.open_conn = F("open_conn") - 1
                    mem.save(update_fields=["open_conn"])
                    mem.refresh_from_db(fields=["open_conn"])
                    if mem.open_conn > 0:
                        return

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
                            {"type": "room.closed", "msg": "방이 삭제되었습니다.", "slug": room_slug}
                        )
                        async_to_sync(self.channel_layer.group_send)(
                            "lobby",
                            {"type": "lobby.event",
                             "payload": {"event": "room_closed", "room_id": room_id, "slug": room_slug}}
                        )
                    transaction.on_commit(_broadcast_room_closed)

                def _broadcast():
                    ver = int(time.time() * 1000)
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
                            "version": ver,
                        }}
                    )
                transaction.on_commit(_broadcast)
        except Room.DoesNotExist:
            return

    def _finalize_leave_if_still_gone(self, room_id: int, user_id: int):
        try:
            owner_changed_payload = None
            user_left_payload = None
            with transaction.atomic():
                room = Room.objects.select_for_update().get(pk=room_id)
                room_slug = room.slug
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
                        return
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
                            logger.info("방장 위임: room=%s new_owner=%s", room.id, new_owner.user_id)
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
                        async_to_sync(self.channel_layer.group_send)(
                            f"room_{room_id}",
                            {"type": "room.closed", "msg": "방이 삭제되었습니다.", "slug": room_slug}
                        )
                        async_to_sync(self.channel_layer.group_send)(
                            "lobby",
                            {"type": "lobby.event",
                             "payload": {"event": "room_closed", "room_id": room_id, "slug": room_slug}}
                        )
                    transaction.on_commit(_broadcast_room_closed)

                def _broadcast_after_commit():
                    ver = int(time.time() * 1000)
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

    # ─────────────── 클라 → 서버 ───────────────
    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        if not action:
            return
        if not hasattr(self, "group"):
            return

        # 1) 채팅
        if action == "chat":
            text = (content.get("message") or "").strip()
            if not text:
                return
            msg_id = await self._save_text_message(self.room.id, self.user.id, text)
            await self.channel_layer.group_send(
                self.group,
                {"type": "chat.message",
                 "message": text,
                 "sender": getattr(self.user, "username", "user"),
                 "message_id": msg_id,
                 "ts": timezone.now().isoformat()}
            )
            return

        # 2) 이미지 인덱스 동기화
        if action == "image.goto":
            payload = {
                "action": "image.goto",
                "idx": content.get("idx"),
                "image_id": content.get("image_id"),
                "ts": timezone.now().isoformat(),
            }
            await self.channel_layer.group_send(self.group, {"type": "room.event", "payload": payload})
            return

        # 3) 드로잉
        if action == "draw.stroke":
            image_id = str(content.get("image_id") or "")
            if not image_id:
                return
            path_id = str(content.get("path_id") or "")
            color = content.get("color") or "#111"
            size = content.get("size") or 4
            mode = content.get("mode") or "pen"
            points = content.get("points") or []
            first = bool(content.get("first"))
            # last = bool(content.get("last"))

            # [ADD] 스토어에 누적(정규화 좌표)
            lst = DRAW_STORE[self.room.id][image_id]
            if first or (not lst) or (lst and lst[-1].get("path_id") != path_id):
                lst.append({"path_id": path_id, "color": color, "size": size, "mode": mode, "points": list(points)})
            else:
                lst[-1]["points"].extend(points)

            # [브로드캐스트] 같은 프레임에서 받은 포인트만 뿌림
            await self.channel_layer.group_send(
                self.group,
                {"type": "room.event",
                 "payload": {
                     "action": "draw.stroke",
                     "image_id": image_id,
                     "color": color, "size": size, "mode": mode,
                     "points": points,
                     "ts": timezone.now().isoformat(),
                 }}
            )
            return

        if action == "draw.clear":
            image_id = str(content.get("image_id") or "")
            if not image_id:
                return
            DRAW_STORE[self.room.id][image_id] = []  # 전체 비움
            await self.channel_layer.group_send(
                self.group,
                {"type": "room.event",
                 "payload": {"action": "draw.clear", "image_id": image_id, "ts": timezone.now().isoformat()}}
            )
            return

        if action == "draw.request_snapshot":
            image_id = str(content.get("image_id") or "")
            if not image_id:
                return
            strokes = DRAW_STORE[self.room.id][image_id]
            # 요청자에게만 전송
            await self.send_json({
                "action": "draw.snapshot",
                "image_id": image_id,
                "strokes": strokes,
                "ts": timezone.now().isoformat(),
            })
            return

        # 4) 업로드 승인 플로우
        if action == "image.propose":
            # 방장은 바로 업로드(bypass) 하므로 여기까지 안 오게 하는 게 클라 기본.
            # 혹시 왔다면 무시하거나 승인으로 취급 가능. 여기선 "방장에게만 전달".
            pending_id = content.get("pending_id")
            name = content.get("name")
            size = content.get("size")
            typ = content.get("type")
            # 방장 개인 그룹으로 보내기
            owner_group = f"room_{self.room.id}_user_{self.owner_id}"
            await self.channel_layer.group_send(
                owner_group,
                {"type": "room.event",
                 "payload": {
                     "action": "image.propose",
                     "pending_id": pending_id,
                     "uploader_id": self.user.id,
                     "uploader_name": getattr(self.user, "username", "user"),
                     "name": name, "size": size, "type": typ,
                     "ts": timezone.now().isoformat(),
                 }}
            )
            return

        if action in ("image.approved", "image.rejected"):
            # 방장만 가능
            if self.user.id != self.owner_id:
                return
            payload = {
                "action": action,
                "pending_id": content.get("pending_id"),
                "uploader_id": content.get("uploader_id"),
                "ts": timezone.now().isoformat(),
            }
            await self.channel_layer.group_send(self.group, {"type": "room.event", "payload": payload})
            return

        # 5) 명시적 퇴장
        if action == "leave":
            self.left_explicitly = True
            # 현재 그룹을 안전하게 탈퇴
            group = getattr(self, "group", None)
            user_group = getattr(self, "user_group", None)
            if group:
                await self.channel_layer.group_discard(group, self.channel_name)
            if user_group:
                await self.channel_layer.group_discard(user_group, self.channel_name)

            # 필요하다면 이후 disconnect에서 중복으로 호출되지 않도록 정리
            self.group = None
            self.user_group = None

            await sync_to_async(self._finalize_leave_immediately)(self.room.id, self.user.id)
            await self.close(code=4000)
            return

    # ─────────────── 서버 → 클라 헬퍼 ───────────────
    async def chat_message(self, event):
        await self.send_json({
            "event": "chat",
            "user": event.get("sender", "server"),
            "message": event["message"],
            "message_id": event.get("message_id"),
            "ts": event.get("ts") or timezone.now().isoformat(),
        })

    async def image(self, event):
        """업로드 API가 group_send(type="image")로 보낼 때 처리"""
        await self.send_json({
            "event": "image",
            "user": event.get("user", "server"),
            "image_url": event["image_url"],
            "caption": event.get("caption", ""),
            "message_id": event.get("message_id"),
            "ts": event.get("ts") or timezone.now().isoformat(),
        })

    async def room_event(self, event):
        """브리지: payload를 그대로 클라이언트로"""
        payload = event["payload"]

        if isinstance(payload, dict) and payload.get("event") == "owner_changed":
            new_owner_id = payload.get("new_owner_id")
            if new_owner_id:
                self.owner_id = new_owner_id
                self.was_owner = (self.user.id == new_owner_id)
        await self.send_json(payload)

    async def kicked(self, event):
        await self.send_json({"event": "kicked", "msg": event.get("msg", "강퇴되었습니다.")})
        await self.close(code=4403)

    async def room_closed(self, event):
        await self.send_json({
            "event": "room_closed",
            "msg": event.get("msg", "방이 삭제되었습니다."),
            "slug": event.get("slug"),
        })
        await self.close(code=4404)

    # ─────────────── 유예 정리 ───────────────
    async def _delayed_cleanup(self, room_id: int, user_id: int,delay: float=GRACE_SECONDS):
        await self._dec_open_conn_only(room_id, user_id)
        if delay>0:
            await asyncio.sleep(GRACE_SECONDS)
        await sync_to_async(self._finalize_leave_if_still_gone)(room_id, user_id)


class LobbyConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("lobby", self.channel_name)
        await self.accept()

        # 방 스냅샷을 접속한 사용자에게만 전송
        rooms = await sync_to_async(self._room_snapshot)()
        await self.send(text_data=json.dumps({
            "event": "snapshot",
            "rooms": rooms,
        }))
        # self.left_explicitly = False
        logger.info("[단계] 로비 WS 연결 성공")

    async def disconnect(self, code):
        await self.channel_layer.group_discard("lobby", self.channel_name)
        logger.info("[단계] 로비 WS 연결 종료 code=%s", code)

    async def lobby_event(self, event):
        payload = event["payload"]
        await self.send(text_data=json.dumps(payload))
        logger.info("[단계] 로비 이벤트 전송 %s", payload)

    def _room_snapshot(self):
        """
        로비 접속 직후 내려줄 현재 방 목록.
        필요한 필드만 골라서 dict로 리턴하세요.
        """
        qs = (Room.objects
              .select_related("created_by")
              .order_by("-created_at"))

        return [
            {
                "id": room.id,
                "slug": room.slug,
                "name": room.Romname,
                "topic": room.topic,
                "owner": getattr(room.created_by, "display_name", room.created_by.username),
                "created_at": room.created_at.isoformat(),
                "requires_password": bool(room.password),
            }
            for room in qs
        ]
