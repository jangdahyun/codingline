from django.db import models,IntegrityError, transaction                               # Django ORM 기본
from django.conf import settings                           # FK에 문자열 대신 안전하게 유저 모델을 참조하려고 이렇게 씀
from django.utils.text import slugify                      # 한글/공백 → URL용 슬러그로
from django.utils import timezone                             # 시간 기록


def make_unique_slug(model, base_text: str, max_len: int) -> str:
    base = slugify(base_text, allow_unicode=True)[:max_len] or "room"   # 기본 슬러그
    slug, i = base, 1
    # 존재하면 -2, -3... 뒤에 붙이면서 찾기
    while model.objects.filter(slug=slug).exists():
        i += 1
        suffix = f"-{i}"
        slug = f"{base[:max_len - len(suffix)]}{suffix}"
    return slug

class Room(models.Model):                                  #  방 테이블
    Romname = models.CharField(max_length=100)                # 방 제목
    slug = models.SlugField(max_length=120, unique=True)   # URL 식별자(고유)
    topic = models.CharField(max_length=50, default="general")  # 주제(검색용)
    created_by = models.ForeignKey(                        # 만든 사람(FK)
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE, # 유저가 삭제되면 그 유저가 만든 모든 방도 같이 삭제.
        related_name="rooms_created", # 역참조 이름(유저가 만든 방들)
    )
    is_private = models.BooleanField(default=False)        # 비공개 여부(추후 접근제어에 사용)
    created_at = models.DateTimeField(auto_now_add=True)   # 생성 시각
    password = models.CharField(max_length=128, blank=True) # 빈 문자열 허용(비공개가 아니면 비밀번호 없음)
    capacity    = models.PositiveSmallIntegerField(default=20)         # 정원(인원 제한)
    updated_at  = models.DateTimeField(auto_now=True)               # 최종 갱신 시각(채팅방 관리용)

    @property
    def name(self) -> str:
        return self.Romname

    @property
    def requires_password(self) -> bool:
        # ✅ 비공개와 무관하게, “비밀번호가 있는 방”만 비번을 요구
        return bool(self.password)
    
    #비밀번호 설정/해제
    def set_password(self, raw: str | None):
        self.password = (raw or "").strip()
    
    def check_room_password(self, raw_pw: str) -> bool:
        if not self.password:        
            return True
        return raw_pw == self.password
    
        # 저장 시 슬러그 자동 생성
    def save(self, *args, **kwargs):
        # 슬러그 자동 생성(없을 때만)
        if not self.slug:
            ml = self._meta.get_field("slug").max_length
            self.slug = make_unique_slug(self.__class__, self.Romname, ml)
        return super().save(*args, **kwargs)

    def __str__(self):                                     # ⑭ 관리자/쉘에서 보기 좋게
        return self.Romname
    

class RoomMember(models.Model):                            #  방 참여자 테이블
    ROLE_OWNER = "owner"                                 # 방장
    ROLE_MEMBER = "member"                               # 일반 참여자
    ROLE_CHOICES = [                                     # 역할 선택지
        (ROLE_OWNER, "방장"),
        (ROLE_MEMBER, "참여자"),
    ]

    room = models.ForeignKey(Room, on_delete=models.CASCADE,related_name="memberships")  # 참여 방(FK)
    user   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="room_memberships")# 참여 유저(FK)
    role   = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)  # 역할
    joined_at = models.DateTimeField(auto_now_add=True)   # 참여 시각
    updated_at = models.DateTimeField(auto_now=True)       # 최종 갱신 시각(채팅방 관리용)
    last_active_at = models.DateTimeField(default=timezone.now, db_index=True)  # 마지막 활동 시각(오프라인 판정용, 인덱스 추가)
    is_banned = models.BooleanField(default=False)  # 강퇴 여부

    class Meta:
        unique_together = ("room", "user")               # 같은 방에 같은 유저 중복 참여 금지
        indexes = [models.Index(fields=["room","joined_at"])]  # 방별 참여자 조회용 복합 인덱스

    def __str__(self):
        return f"{self.user} in {self.room} ({self.role})"
    

@transaction.atomic
def transfer_ownership_to_earliest(room: Room):
    # 방장 제외 & 밴 제외 & 가장 먼저 들어온 멤버
    nxt = (RoomMember.objects
           .select_for_update()
           .filter(room=room, is_banned=False)
           .exclude(user_id=room.created_by_id)
           .order_by("joined_at")
           .first())
    if not nxt:
        return None
    # 방장 교체
    RoomMember.objects.select_for_update().filter(room=room, user_id=room.created_by_id) \
        .update(role=RoomMember.ROLE_MEMBER)
    RoomMember.objects.select_for_update().filter(pk=nxt.pk) \
        .update(role=RoomMember.ROLE_OWNER)
    room.created_by_id = nxt.user_id
    room.save(update_fields=["created_by"])
    return nxt.user

def can_enter_room(user, room: Room) -> tuple[bool, str | None]:
    # 밴 체크
    if RoomMember.objects.filter(room=room, user=user, is_banned=True).exists():
        return False, "강퇴된 사용자입니다."
    # 정원(간단 버전: 전체 멤버 수 기준)
    if user.pk != room.created_by_id:
        current = RoomMember.objects.filter(room=room, is_banned=False).count()
        if current >= room.capacity:
            return False, "정원이 가득 찼습니다."
    return True, None

@transaction.atomic
def kick_member(room: Room, actor, target_user):
    # 1) 실행자(actor)의 멤버십을 잠금 상태로 가져옵니다. 없으면 404 비슷한 예외가 납니다.
    actor_mem = (
        RoomMember.objects.select_for_update()
        .get(room=room, user=actor)
    )

    # 2) 권한 확인: 방장만 강퇴 가능(정책에 따라 MOD도 허용 가능)
    if actor_mem.role != RoomMember.ROLE_OWNER:
        raise PermissionError("권한이 없습니다.")

    # 3) 방장 강퇴 금지: 가장 명확하게 '대상 유저의 pk'로 비교 (IDE 경고도 사라짐)
    if target_user.pk == room.created_by_id:
        raise PermissionError("방장은 강퇴할 수 없습니다.")

    # 4) 대상 멤버십 확보: 없으면 생성하고, 곧바로 밴 상태로 바꿉니다.
    target_mem, _ = (
        RoomMember.objects.select_for_update()
        .get_or_create(
            room=room,
            user=target_user,
            defaults={"role": RoomMember.ROLE_MEMBER},
        )
    )

    # 5) 강퇴 플래그 ON (추가로 퇴장 처리/웹소켓 close는 컨슈머에서)
    target_mem.is_banned = True
    target_mem.save(update_fields=["is_banned"])

    return True                                 # 7) 호출측에서 OK 처리

@transaction.atomic
def unban_member(room: Room, actor, target_user):
    mem_actor = (                                         # 1) 실행자 권한 확인
        RoomMember.objects.select_for_update()
        .get(room=room, user=actor)
    )
    if mem_actor.role != RoomMember.ROLE_OWNER:           # 2) 방장만 해제(정책에 맞게 조정)
        raise PermissionError("권한이 없습니다.")

    updated = (                                           # 3) 대상의 밴 해제
        RoomMember.objects.filter(room=room, user=target_user, is_banned=True)
        .update(is_banned=False)
    )
    return bool(updated)      