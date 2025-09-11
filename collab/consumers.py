# consumers.py
from channels.generic.websocket import AsyncJsonWebsocketConsumer  # JSON 송수신에 특화된 Consumer
from asgiref.sync import sync_to_async                              # 동기 ORM을 비동기에서 안전하게 호출
from django.db import transaction                                   # 트랜잭션(동시성 안전)
from django.contrib.auth.models import AnonymousUser                # 비로그인 사용자 표현
from django.contrib.auth import get_user_model                      # 유저 로딩 함수
from django.db.models import F                                      # 원자적 +1/-1 연산
from .models import Room, RoomMember, Message                       # 우리 도메인 모델들


class RoomPresenceConsumer(AsyncJsonWebsocketConsumer):             # WebSocket용 Consumer (JSON 기반)
    async def connect(self):                                        # 클라이언트가 소켓 연결을 시도할 때
        self.slug = self.scope["url_route"]["kwargs"]["slug"]       # URL 경로에서 방 slug 추출
        self.user = self.scope.get("user", AnonymousUser())         # 인증 미들웨어가 넣어준 user

        if not self.user.is_authenticated:                          # 비로그인은 거절
            await self.close(code=4001); return

        room = await self._get_room(self.slug)                      # 슬러그로 방 로드(없으면 None)
        if not room:                                                # 방이 없으면
            await self.close(code=4404); return                     # 4404 → Not Found 의미로 사용
        self.room = room                                            # 인스턴스로 저장(이후에 사용)

        # 접속 허용/정원 체크 + 멤버십 upsert + open_conn += 1 (실패 시 False)
        ok = await self._inc_conn(room.id, self.user.id)            # _inc_conn 내부에서 room.can_enter 검사
        if not ok:                                                  # 정원/밴 등으로 거절된 경우
            await self.close(code=4403); return                     # 4403 → Forbidden 의미로 사용

        # 그룹 이름 두 가지: 방 공용 / 개인(강퇴 알림용)
        self.group = f"room_{room.pk}"                              # 방 전체에 브로드캐스트할 그룹
        self.user_group = f"room_{room.pk}_user_{self.user.pk}"     # 특정 유저에게만 보낼 그룹(강퇴 등)

        # 그룹 가입 (Consumer의 channel_name을 그룹에 등록)
        await self.channel_layer.group_add(self.group, self.channel_name)      # 공용 그룹 join
        await self.channel_layer.group_add(self.user_group, self.channel_name) # 개인 그룹 join

        await self.accept()                                          # WebSocket 연결 수락(핸드셰이크 완료)

    async def disconnect(self, code):                                # 소켓이 끊길 때(정상/비정상 모두)
        # open_conn -= 1 및 마지막이면 멤버십 정리/방장 위임/빈 방 삭제까지 처리
        await self._dec_conn_and_cleanup(getattr(self, 'room', None), getattr(self, 'user', None))

        # 그룹 해제(등록된 채널 제거)
        if hasattr(self, 'group'):
            await self.channel_layer.group_discard(self.group, self.channel_name)      # 공용 그룹 leave
        if hasattr(self, 'user_group'):
            await self.channel_layer.group_discard(self.user_group, self.channel_name) # 개인 그룹 leave

    async def receive_json(self, content, **kwargs):                 # 브라우저 → 서버 JSON 수신 핸들러
        if content.get('action') == 'chat':                          # 액션이 'chat'이면 채팅 메시지 처리
            text = (content.get('message') or '').trim() if hasattr(str, 'trim') else (content.get('message') or '').strip()
            if not text:                                             # 빈 문자열은 무시
                return
            msg = await self._create_message(self.room.id, self.user.id, text)  # DB에 Message 저장
            # 방 공용 그룹으로 브로드캐스트 (type 이름과 동일한 메서드가 아래에 있어야 함)
            await self.channel_layer.group_send(
                self.group,
                {
                    "type": "chat",                                  # → chat(self, event) 핸들러로 라우팅됨
                    "user": getattr(self.user, "username", str(self.user.id)),
                    "message": msg["content"],
                    "ts": msg["ts"],
                }
            )

    # ----------------------- 서버 → 클라 이벤트 핸들러들 -----------------------

    async def chat(self, event):                                     # group_send(type="chat") 수신
        await self.send_json({
            "event": "chat",                                         # 클라에서 구분할 event 키
            "user": event["user"],                                   # 보낸 사람
            "message": event["message"],                             # 내용
            "ts": event["ts"],                                       # 타임스탬프(ISO)
        })

    async def image(self, event):                                    # group_send(type="image") 수신
        await self.send_json({
            "event": "image",                                        # 이미지 업로드 브로드캐스트
            "user": event["user"],
            "image_url": event["image_url"],                         # 이미지 URL
            "message_id": event["message_id"],                       # 메시지 PK
            "ts": event["ts"],
        })

    async def kicked(self, event):                                   # group_send(type="kicked") 수신(개인 그룹)
        await self.send_json({"event": "kicked", "msg": event.get("msg", "")}) # 알림 전송
        await self.close(code=4403)                                  # 강퇴 시 소켓 종료

    async def room_closed(self, event):                              # (필요 시) 방 삭제 알림
        await self.send_json({"event": "room_closed", "msg": event.get("msg", "")})
        await self.close(code=4404)                                  # 방이 사라졌으니 종료

    # ----------------------- DB helpers (동기 ORM → 비동기 래핑) -----------------------

    @sync_to_async
    def _get_room(self, slug):                                       # 슬러그로 방 로딩
        try:
            return Room.objects.get(slug=slug)                       # 존재하면 Room 반환
        except Room.DoesNotExist:
            return None                                              # 없으면 None (connect에서 처리)

    @sync_to_async
    def _create_message(self, room_id: int, user_id: int, text: str):# 채팅 메시지 DB 생성
        m = Message.objects.create(room_id=room_id, user_id=user_id, content=text)  # 레코드 생성
        return {"id": m.id, "content": m.content, "ts": m.created_at.isoformat()}   # 직렬화해서 반환

    @sync_to_async
    def _inc_conn(self, room_id: int, user_id: int) -> bool:         # 접속 시 카운터 증가(+ 입장 허용 검사)
        User = get_user_model()                                      # 현재 User 모델 가져오기
        user = User.objects.get(pk=user_id)                          # 유저 로딩
        room = Room.objects.get(pk=room_id)                          # 방 로딩

        ok, _reason = room.can_enter(user)                           # 밴/정원 정책 검사(모델에 응집)
        if not ok:                                                   # 입장 불가면
            return False                                             # connect에서 4403으로 종료

        with transaction.atomic():                                   # 동시성 안전
            mem, _ = RoomMember.objects.select_for_update().get_or_create(
                room=room,
                user=user,
                defaults={"role": RoomMember.ROLE_MEMBER},           # 없으면 일반 멤버로 생성
            )
            mem.open_conn = F('open_conn') + 1                       # 원자적으로 open_conn += 1
            mem.save(update_fields=['open_conn'])                     # 해당 필드만 업데이트
        return True                                                  # OK → connect 계속 진행

    @sync_to_async
    def _dec_conn_and_cleanup(self, room: Room, user):               # 끊김 시 카운터 감소 및 청소
        if not room or not user:                                     # 이미 실패한 연결 등
            return

        with transaction.atomic():                                   # 동시성 안전
            try:
                mem = RoomMember.objects.select_for_update().get(room=room, user=user)  # 멤버 레코드 잠금
            except RoomMember.DoesNotExist:
                mem = None

            if mem:
                if mem.open_conn > 1:                                # 다른 탭이 남아있다면
                    mem.open_conn = mem.open_conn - 1                # 카운터만 감소
                    mem.save(update_fields=['open_conn'])
                else:                                                # 마지막 연결이 끊긴 순간
                    if mem.role == RoomMember.ROLE_OWNER:            # 방장이면
                        room.transfer_ownership_to_earliest()         # 방장 위임 시도

                    if mem.is_banned:                                # 밴 유저는 기록 유지(재입장 방지)
                        mem.open_conn = 0
                        mem.save(update_fields=['open_conn'])
                    else:                                            # 일반 유저는 자동 퇴장
                        mem.delete()

            # 방에 활성 접속자(open_conn>0, is_banned=False)가 없으면 방 삭제
            has_active = RoomMember.objects.filter(
                room=room, is_banned=False, open_conn__gt=0
            ).exists()
            if not has_active:
                room.delete()                                        # 빈 방 자동 삭제
