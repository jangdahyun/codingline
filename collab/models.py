# models.py
from django.db import models, transaction             # ORM + 트랜잭션
from django.conf import settings                      # AUTH_USER_MODEL 참조
from django.utils.text import slugify                 # 한글/공백 → 슬러그
from django.utils import timezone                     # 시간 기록
from django.core.exceptions import PermissionDenied,ValidationError  # 권한 예외 (403로 매핑 쉬움)
from django.contrib.auth.models import AnonymousUser  # 로그인 확인

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"                     # 조건부 UniqueConstraint에 필요

# ──────────────────────────────────────────────────────────────────────
# 슬러그 생성 유틸: 같은 슬러그가 있으면 -2, -3 … 붙여서 고유값 보장
# ──────────────────────────────────────────────────────────────────────
def make_unique_slug(model, base_text: str, max_len: int) -> str:
    base = slugify(base_text, allow_unicode=True)[:max_len] or "room"  # 기본 후보
    slug, i = base, 1                                                 # 현재 후보 + 카운터
    while model.objects.filter(slug=slug).exists():                   # 존재하면…
        i += 1                                                        # 번호 증가
        suffix = f"-{i}"                                              # -2, -3 …
        slug = f"{base[:max_len - len(suffix)]}{suffix}"              # 잘라 붙이기
    return slug

# ──────────────────────────────────────────────────────────────────────
# 방 (Room)
# ──────────────────────────────────────────────────────────────────────
class Room(models.Model):
    Romname    = models.CharField(max_length=100)                     # 방 제목(오탈자지만 유지)
    slug       = models.SlugField(max_length=120, unique=True)        # URL 식별자
    topic      = models.CharField(max_length=50, default="general")   # 주제(검색)
    created_by = models.ForeignKey(                                   # 만든 사람
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rooms_created",
    )
    is_private = models.BooleanField(default=False)                    # (현재 로직에선 미사용)
    created_at = models.DateTimeField(auto_now_add=True)               # 생성 시각
    password   = models.CharField(max_length=128, blank=True)          # 비번(빈문자 허용)
    capacity   = models.PositiveSmallIntegerField(default=20)          # 정원
    updated_at = models.DateTimeField(auto_now=True)                   # 갱신 시각

    # 읽기 전용 속성: name → 실제 필드는 Romname 이지만 일관된 접근을 위해 제공
    @property
    def name(self) -> str:
        return self.Romname

    # 비밀번호 요구 여부: 비공개 플래그와 무관, “비번이 있으면 요구”
    @property
    def requires_password(self) -> bool:
        return bool(self.password)

    # 비밀번호 설정/해제(빈문자 → 해제)
    def set_password(self, raw: str | None):
        self.password = (raw or "").strip()

    # 비밀번호 검증: 비번이 없으면 True
    def check_room_password(self, raw_pw: str) -> bool:
        return True if not self.password else (raw_pw == self.password)

    # 저장 시 슬러그 자동 생성(최초 1회)
    def save(self, *args, **kwargs):
        if not self.slug:
            ml = self._meta.get_field("slug").max_length
            self.slug = make_unique_slug(self.__class__, self.Romname, ml)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.Romname

    # ──────────────────────────────────────────────────────────────
    # 아래부터 “도메인 규칙 함수”를 인스턴스 메서드로 승격
    # (뷰/컨슈머에서 room.메서드() 형태로 호출 → 가독성/응집도 ↑)
    # ──────────────────────────────────────────────────────────────

    @transaction.atomic
    def room_update(self, *, actor, name: None, topic: None, is_private: None, capacity: None, password:None,broadcast=True):
        if actor.pk != self.created_by_id and not getattr(actor, "is_staff", False):
            raise PermissionDenied("권한이 없습니다.")
        if name is not None:
            v=name.strip()
            if not v:
                raise ValidationError("방 제목은 비울 수 없습니다.")
            self.Romname = v[:100]

        if topic is not None:
            self.topic = topic.strip()[:50]

        if is_private is not None:
            self.is_private = bool(is_private)

        if capacity is not None:
            if capacity < 1:
                raise ValidationError("정원은 1명 이상이어야 합니다.")
            if capacity > 10:
                raise ValidationError("정원은 최대 10명입니다.")
            self.capacity = capacity    
        
        if password is not None:
            self.password = (password or "").strip()

        self.save()

        if broadcast:
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"room_{self.id}",
                    {
                        "type": "room.event",     # 컨슈머의 핸들러 이름
                        "event": "room.updated",  # 프론트에서 분기 처리
                        "payload": {
                            "slug": self.slug,
                            "name": self.Romname,
                            "topic": self.topic,
                            "is_private": self.is_private,
                            "capacity": self.capacity,
                            "requires_password": self.requires_password,
                        },
                    },
                )
            except Exception:
                # 채널 레이어가 아직 준비 전이거나 테스트 환경일 수 있으므로, 갱신 자체는 계속 진행
                pass

            # def _lobby_updated():
            #     try:
            #         from channels.layers import get_channel_layer
            #         from asgiref.sync import async_to_sync
            #         channel_layer = get_channel_layer()
            #         async_to_sync(channel_layer.group_send)(
            #             "lobby",   # 로비 그룹
            #             {
            #                 "type": "lobby.event",  # 컨슈머의 핸들러 이름
            #                   # 프론트에서 분기 처리
            #                 "payload": {
            #                     "event": "room_updated",
            #                     "slug": self.slug,
            #                     "name": self.Romname,
            #                     "topic": self.topic,
            #                     "is_private": self.is_private,
            #                     "capacity": self.capacity,
            #                     "requires_password": self.requires_password,
            #                 },
            #             },
            #         )
            #     except Exception:
            #         pass  # 채널 레이어가 아직 준비 전이거나 테스트 환경일 수 있으므로, 무시
            # transaction.on_commit(_lobby_updated)
        return self
    
    @transaction.atomic
    def room_delete(self, *, actor, broadcast=True):
        # 1) 권한 확인: 방장 또는 스태프만 허용
        if actor.pk != self.created_by_id and not getattr(actor, "is_staff", False):
            raise PermissionDenied("권한이 없습니다.")

        # 2) 삭제 전 식별자(값) 백업: 삭제 후엔 self 접근이 불안정하므로 미리 보관
        room_id   = self.id
        room_slug = self.slug
        room_name = self.Romname

        # 3) 브로드캐스트가 켜져 있으면, 트랜잭션 커밋 이후에만 이벤트를 쏘도록 예약
        if broadcast:
            def _after_commit_broadcasts():
                """
                DB 커밋이 확정된 '이후'에만 실행.
                - 정합성 보장: 롤백되면 전송 안 됨
                - 프론트 즉시 재조회 시 404/레이스 조건 완화
                """
                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    channel_layer = get_channel_layer()

                    # (선택) 방 참여자 그룹에 'room.deleted' 통지
                    # - 방 탭을 열어둔 사용자에게 즉시 안내/리다이렉트 용
                    async_to_sync(channel_layer.group_send)(
                        f"room_{room_id}",
                        {
                            "type": "room.event",
                            "event": "room.deleted",
                            "payload": {
                                # 표준 키
                                "room_id": room_id,
                                "room_slug": room_slug,
                                "room_name": room_name,

                                # (임시 호환용) 구키 — 프론트 이전 완료 후 삭제 가능
                                "slug": room_slug,
                                "name": room_name,
                            },
                        },
                    )

                    # 로비에 'room_deleted' 단 1회만 통지 (중복 전송 금지)
                #     async_to_sync(channel_layer.group_send)(
                #         "lobby",
                #         {
                #             "type": "lobby.event",
                #             "payload": {
                #                 "event": "room_deleted",
                #                 # 표준 키
                #                 "room_id": room_id,
                #                 "room_slug": room_slug,
                #                 "room_name": room_name,

                #                 # (임시 호환용) 구키 — 프론트 이전 완료 후 삭제 가능
                #                 "slug": room_slug,
                #                 "name": room_name,
                #             },
                #         },
                #     )
                except Exception:
                    # 채널 레이어 미준비/테스트 환경 등은 무시(삭제 자체는 계속 진행)
                    pass

            # 커밋 후 실행 예약
            transaction.on_commit(_after_commit_broadcasts)

        # 4) 실제 삭제 수행 (CASCADE로 멤버십/메시지 등 함께 삭제)
        super(Room, self).delete()

        # 5) 호출자에게 삭제 결과(표준 키) 반환
        return {
            "room_id": room_id,
            "room_slug": room_slug,
            "room_name": room_name,
        }
    
    # 입장 가능 여부 + 사유
    def can_enter(self, user) -> tuple[bool, str | None]:
        if not user or isinstance(user, AnonymousUser):
            return False, "로그인이 필요합니다."
        
        # 밴(강퇴) 여부 먼저 체크
        if RoomMember.objects.filter(room=self, user=user, is_banned=True).exists():
            return False, "강퇴된 사용자입니다."
        
        # 방장이 아니면 정원 체크
        if user.pk != self.created_by_id:
            current = RoomMember.objects.filter(
                room=self, is_banned=False, open_conn__gt=0
            ).count()
            if current >= self.capacity:
                return False, "정원이 가득 찼습니다."
        return True, None

    # 방장 권한을 “가장 먼저 들어온 멤버”에게 위임
    @transaction.atomic
    def transfer_ownership_to_earliest(self, *, demote_previous: bool = True):
        # 1) Room 행 잠금
        room = type(self).objects.select_for_update().get(pk=self.pk)

        prev_owner_id = room.created_by_id

        # 2) 현재 방장 멤버십 잠금 + 존재/역할 재확인
        prev_owner_mem = (RoomMember.objects
            .select_for_update()
            .filter(room=room, user_id=prev_owner_id, role=RoomMember.ROLE_OWNER)
            .first())
        print("이전 방장 멤버십:", prev_owner_mem)

        # 3) 다음 방장 후보 선별(본인 제외, 활성/남아있는 순서 기준은 프로젝트 규칙에 맞게)
        nxt = (RoomMember.objects
            .select_for_update()
            .filter(room=room)
            .exclude(user_id=prev_owner_id)
            .exclude(is_banned=True) # 밴된 사람 제외
            .order_by("joined_at", "id")    # 규칙에 맞게 정렬
            .first())

        if not nxt:
            return None  # 넘길 대상 없으면 종료

        # 4) 이전 방장 강등은 '남아 있는 연결이 확실'할 때만 수행
        if demote_previous and prev_owner_mem is not None:
            # open_conn 필드가 있다면 안전하게 조건 재확인
            if getattr(prev_owner_mem, "open_conn", 0) > 0:
                prev_owner_mem.role = RoomMember.ROLE_MEMBER
                prev_owner_mem.save(update_fields=["role"])
            # 연결이 0이면 강등하지 않음(곧 멤버십이 정리될 상황)

        # 5) 소유권 이전
        room.created_by_id = nxt.user_id
        room.save(update_fields=["created_by"])

        # 6) 새 방장 승격
        if nxt.role != RoomMember.ROLE_OWNER:
            nxt.role = RoomMember.ROLE_OWNER
            nxt.save(update_fields=["role"])

        return nxt

    # 강퇴(방장만, 방장은 강퇴 불가)
    @transaction.atomic
    def kick(self, actor, target_user):
        # 실행자 멤버십(락)
        actor_mem = (
            RoomMember.objects
            .select_for_update()
            .get(room=self, user=actor)
        )
        # 권한: 방장만
        if actor_mem.role != RoomMember.ROLE_OWNER:
            print("권한 없음")
            raise PermissionDenied("권한이 없습니다.")
        # 대상이 방장인지
        if target_user.pk == self.created_by_id:
            print("방장은 강퇴 불가")
            raise PermissionDenied("방장은 강퇴할 수 없습니다.")
            
        # 대상 멤버십 확보(+락)
        target_mem, _ = (
            RoomMember.objects
            .select_for_update()
            .get_or_create(
                room=self,
                user=target_user,
                defaults={"role": RoomMember.ROLE_MEMBER},
            )
        )
        # 밴 플래그 ON
        target_mem.is_banned = True
        target_mem.save(update_fields=["is_banned"])
        print("강퇴 성공")
        return True

    # 밴 해제(방장만)
    @transaction.atomic
    def unban(self, actor, target_user):
        mem_actor = (
            RoomMember.objects
            .select_for_update()
            .get(room=self, user=actor)
        )
        if mem_actor.role != RoomMember.ROLE_OWNER:
            raise PermissionDenied("권한이 없습니다.")
        updated = (
            RoomMember.objects
            .filter(room=self, user=target_user, is_banned=True)
            .update(is_banned=False)
        )
        return bool(updated)
    

# ──────────────────────────────────────────────────────────────────────
# 방 멤버십 (RoomMember)
# ──────────────────────────────────────────────────────────────────────
class RoomMember(models.Model):
    ROLE_OWNER  = ROLE_OWNER                                         # 방장
    ROLE_MEMBER = ROLE_MEMBER                                      # 멤버

    ROLE_CHOICES = [
        (ROLE_OWNER, "방장"),
        (ROLE_MEMBER, "참여자"),
    ]

    room           = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="memberships")
    user           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="room_memberships")
    role           = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    joined_at      = models.DateTimeField(auto_now_add=True)      # 참여 시각
    updated_at     = models.DateTimeField(auto_now=True)          # 갱신 시각
    last_active_at = models.DateTimeField(default=timezone.now, db_index=True)  # 마지막 활동
    is_banned      = models.BooleanField(default=False)           # 강퇴 여부
    open_conn      = models.PositiveIntegerField(default=0)  # ← 현재 열린 WebSocket 연결 수(멀티 탭 대응)

    class Meta:
        # (레거시) unique_together 대신 UniqueConstraint 사용
        indexes = [
            models.Index(fields=["room", "joined_at"]),           # 방별 참여자 조회
            models.Index(fields=["room", "is_banned"]),           # 정원/밴 여부 조회 최적화
            models.Index(fields=["room", "open_conn"]),           # 정원 체크 최적화
        ]
        constraints = [
            # 같은 방에 동일 유저 1회
            models.UniqueConstraint(fields=["room", "user"], name="uniq_room_user"),
        ]

    def __str__(self):
        return f"{self.user} in {self.room} ({self.role})"
# 메세지
class Message(models.Model):
    room = models.ForeignKey('Room', on_delete=models.CASCADE, related_name='messages')  # 메시지의 방
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)         # 작성자/업로더
    content = models.TextField(blank=True)                                              # 텍스트(없어도 됨)
    image = models.ImageField(upload_to='room_images/%Y/%m/%d/', null=True, blank=True) # 이미지(없어도 됨)
    created_at = models.DateTimeField(auto_now_add=True)                                 # 생성 시각

    class Meta:
        indexes = [
            models.Index(fields=['room', 'created_at']),  # 최근 메시지 조회 최적화
        ]
        ordering = ['-created_at']  # 최신이 먼저 오도록(리스트 뽑을 때 편함)

    def clean(self):
        # 텍스트/이미지 둘 다 비면 저장 막기
        if not self.content and not self.image:
            raise ValidationError('내용 또는 이미지는 하나 이상 필요합니다.')

    def is_image(self) -> bool:
        return bool(self.image)
    