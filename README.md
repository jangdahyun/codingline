# codingline
django를 이용한 프로젝트1


전체 구조 요약

프레임워크/런타임

Django 5.2 + ASGI(Channels) + Daphne

WebSocket: channels, 브로커: channels_redis(Redis)

인증/소셜: django-allauth + Kakao/Naver provider

DB: MySQL(utf8mb4), 환경변수: python-dotenv

앱

accounts: 커스텀 User, 소셜 회원가입 뷰(CustomSocialSignupView)

collab: 방/멤버/채팅(WebSocket), 이미지 업로드

config: settings/asgi/urls 등 프로젝트 설정

템플릿

login.html / account/3rdparty_signup.html / home.html / room_detail.html

실시간 흐름

GET /rooms/<slug>/ 진입

브라우저가 ws://.../ws/rooms/<slug>/로 WebSocket 연결

receive_json으로 채팅 이벤트 처리 → 그룹 브로드캐스트

이미지 업로드는 HTTP(POST) → 업로드 후 방 그룹으로 실시간 알림

왜 이 기술들을 썼나? (대안 비교 포함)
1) Django + Channels(+Daphne)

이유: Django의 ORM/템플릿/폼 + 인증/세션을 그대로 활용하면서 실시간(WebSocket) 을 붙일 수 있음.

대안

Flask/FastAPI + Starlette/WebSocket: 경량/성능 좋지만, 인증/세션/폼/어드민을 직접 조합해야 함(개발 속도↓).

Node.js + socket.io: 실시간 생태계/도구 강점, 그러나 Python 자산(ORM/ML 등)과는 분리 관리.

2) Channels + Redis

이유: 다중 프로세스/서버에서 메시지 브로커(채널 레이어) 가 필요. Redis는 표준 선택.

대안:

InMemoryChannelLayer: 개발용/단일 프로세스에만 안전.

Kafka/RabbitMQ: 대규모 확장엔 좋으나 설정·학습 비용↑, Channels 기본 통합은 Redis가 가장 수월.

3) allauth + Kakao/Naver

이유: 소셜 OAuth/OIDC를 훨씬 덜 힘들게 붙임(상태/콜백/연동 화면).

대안:

직접 OAuth: 세세한 보안·예외 처리를 모두 구현해야 함. allauth가 표준적/안전한 흐름 제공.

4) MySQL

이유: 팀/환경 선호, 운영 친숙도, 기존 인프라.

대안:

PostgreSQL: JSON/부분 유니크/락/트랜잭션 기능이 더 풍부, Django 커뮤니티에선 보편적.

추천: 장기적으로 PostgreSQL 을 고려(특히 부분 유니크, CONCURRENT 인덱스, 범용성).

핵심 흐름(엔드투엔드)

홈 home.html

방 생성 POST → Room 저장 → 방장 멤버십 보장

입장 room-enter(AJAX)

비번/정원/밴 검사(room.can_enter) → 세션 플래그 → next URL 반환

디테일 room_detail.html

좌: 채팅(WS), 우: 이미지(HTTP 업로드 + 실시간 반영)

WebSocket /ws/rooms/<slug>/

접속 시 group_add("room_{id}") → 채팅 수신/송신

이미지

POST /rooms/<slug>/images/upload/ → Message(image=...) 생성 → 그룹에 image 이벤트

데이터 모델(핵심 개념)

파일이 일부 겹쳐 업로드되어 보이지만, 현재 구조 기준으로 설명합니다.

User(accounts.User)

이메일 고유, 닉네임/아바타, __str__ = display_name or username

⚠️ 업로드된 accounts/models.py에 display_name 줄에 오타(nu...)가 섞여 있어 SyntaxError 가능 → 아래 “버그/리팩터” 참고

Room / RoomMember(collab)

Room(Romname, slug, topic, created_by, capacity, password, is_private...)

slug 자동 생성(한글 허용), 비밀번호는 지금 평문 비교

RoomMember(room,user,role,is_banned,joined_at,last_active_at...)

role ∈ {owner, member}, (room,user) 유니크, 인덱스 최적화

(권장) “방장 1명”을 애플리케이션/DB 제약으로 보장

Message(텍스트/이미지 통합)

room,user,content(옵션),image(옵션),created_at

하나도 없으면 ValidationError, ordering = ['-created_at']

최근 50개 페이지네이션 API

중요한 코드(파이썬, 한 줄씩 설명)
1) WebSocket Consumer(요지)
class RoomPresenceConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.slug = self.scope["url_route"]["kwargs"]["slug"]     # URL 파라미터에서 slug
        self.user = self.scope.get("user", AnonymousUser())       # ASGI 스코프의 user

        if not self.user.is_authenticated:                        # 비로그인 거절
            await self.close(code=4001); return

        room, active = await self._get_room_and_active(self.slug, self.user.id)
        if not room or not active:                                # 방 없거나 밴 상태면
            await self.close(code=4403); return

        self.room = room
        self.group = f"room_{room.pk}"                            # 방 그룹 이름
        await self.channel_layer.group_add(self.group, self.channel_name)  # 그룹 입장
        await self.accept()                                        # 핸드셰이크 완료

    async def receive_json(self, content, **kwargs):
        if content.get("action") == "chat":                       # 채팅 전송이면
            text = (content.get("message") or "").strip()
            if not text: return
            msg = await self._create_message(self.room.id, self.user.id, text)  # DB 저장
            await self.channel_layer.group_send(                  # 방 전체에 브로드캐스트
                self.group, {"type":"chat", "user": self.user.username, "message": msg["content"], "ts": msg["ts"]}
            )

    async def chat(self, event):                                  # 그룹 수신 핸들러
        await self.send_json({"event":"chat", **event})           # 브라우저로 이벤트

    @sync_to_async
    def _get_room_and_active(self, slug, user_id):
        # DB I/O는 sync_to_async로 스레드풀에서 실행
        ...

2) 입장 API(요지)
@require_POST
@login_required
def room_enter_json(request, slug):
    room = get_object_or_404(Room, slug=slug)                     # 방 로딩
    if not room.password:                                         # 공개방
        ok, reason = room.can_enter(request.user)                 # 정원/밴 검사
        if not ok: return JsonResponse({"ok": False, "error": reason}, status=403)
        _grant_session_access(request, room)                      # 세션 플래그
        _ensure_membership(room, request.user, RoomMember.ROLE_MEMBER)  # 멤버십 보장
        return JsonResponse({"ok": True, "next": reverse("room-detail", kwargs={"slug": room.slug})})

    pw = request.POST.get("password", "")                         # 비번방
    if room.check_room_password(pw):                              # 비번 OK
        ok, reason = room.can_enter(request.user)
        if not ok: return JsonResponse({"ok": False, "error": reason}, status=403)
        _grant_session_access(...); _ensure_membership(...)
        return JsonResponse({"ok": True, "next": reverse("room-detail", kwargs={"slug": room.slug})})
    return JsonResponse({"ok": False, "error":"비밀번호가 올바르지 않습니다."}, status=400)

3) 설정(Settings) — 채널 레이어(중요)
CHANNEL_LAYERS = {
  "default": {
    "BACKEND": "channels_redis.core.RedisChannelLayer",           # 운영/실서버
    "CONFIG": {"hosts": ["redis://127.0.0.1:6379/0"]},
  }
}
# 개발만 빠르게 돌릴 땐 (settings.DEBUG일 때)
# CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


Redis 미가동 시 10061 Connection refused가 납니다 → 개발 중엔 InMemory로 빠르게.

면접 예상 질문 & 답변 포인트

ASGI vs WSGI 차이

WSGI는 동기 HTTP 1요청-1응답 모델만.

ASGI는 비동기/이벤트 기반으로 WebSocket, 장기 연결, 백프레셔 등을 처리. Channels는 ASGI 위에 구현.

Channels 아키텍처

Consumer(WebSocket endpoint) ↔ Channel Layer(Redis) ↔ 다른 워커/서버.

group_add/group_send로 N:1 fan-out, 서버 수평 확장 가능.

Redis 역할

메시지 라우팅/버퍼/브로커. 프로세스 간 이벤트를 안전하게 중계.

트랜잭션/동시성

Django에서 transaction.atomic() + select_for_update()로 경쟁 상황(방장 위임, 킥) 안전하게 처리.

왜 필요한가? 동시에 두 명이 같은 멤버 변경을 시도할 때 레이스 컨디션 방지.

권한/보안

HTTP는 CSRF 방어(토큰/헤더). WebSocket은 CSRF 개념 대신 인증 스코프/허용 그룹으로 통제.

“방장만 킥/언밴 가능” → 서버에서 role 검사, 클라 신뢰 금지.

업로드/미디어

개발: Django가 /media/ 서빙. 운영: Nginx/S3·CloudFront로 오프로드.

이미지 검증(확장자/MIME/크기), 썸네일/리사이즈 고려.

MySQL vs PostgreSQL

MySQL 친숙/운영 많음. Postgres는 부분 유니크/락/함수 인덱스 등 ORM 친화 기능 풍부 → 협업/채팅 도메인에 유리할 때가 많음.

성능/스케일

WS 연결 수 증가 시 워커/인스턴스 스케일 아웃 + Redis 클러스터링.

메시지량 증가 시 백프레셔/레이트리밋(유저별 QPS 제한) / 큐 소비자 확장.

테스트

consumer 테스트: channels.testing.WebsocketCommunicator로 연결/이벤트 송수신 검증.

view 테스트: Django TestCase + 클라 시뮬레이션.

지금 코드에서 바로 손보면 좋은 것들(액션 아이템)

accounts/models.py—오타/문법 오류 수정

display_name = models.CharField(...,nu..., unique=True, db_collation='utf8mb4_0900_ai_ci')
→ nu... 부분을 삭제하고 올바른 인자만 남기세요.

display_name = models.CharField(
    max_length=50, blank=True, unique=True,
    db_collation='utf8mb4_0900_ai_ci',
)


AUTH_USER_MODEL = "accounts.User" 가 settings.py에 있는지 확인.

운영/개발 분리된 채널 레이어

개발에선 InMemory로 간단히:

if DEBUG:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


운영은 Redis. (6379 에러 방지)

api_unban URL 통일

이미 고치셨듯이 slug만 경로로 받고, user_id는 POST 바디로 통일.

Room 비밀번호

현재 평문 비교 → 해시 저장(make_password/check_password)로 개선 권장.

from django.contrib.auth.hashers import make_password, check_password
# set_password: self.password = make_password(raw) if raw else ""
# check_room_password: return not self.password or check_password(raw_pw, self.password)


이미지 검증/스토리지

Message.clean()에 파일 크기/MIME/확장자 검사.

운영은 S3(+django-storages)로 오프로드.

로그/모니터링

logger = logging.getLogger(...) 이미 있음 → 파일핸들러/JSON 로깅, 400/500 알람.

N+1 방지

메시지 목록 select_related("user")(이미 있음) + 필요 시 only()/defer().

실행 체크리스트

.env에 필수 키

DJANGO_SECRET_KEY, DB_NAME/USER/PASSWORD/HOST/PORT, SITE_ID

마이그레이션

python manage.py makemigrations
python manage.py migrate


개발 서버

# Redis 없이 빠르게 확인
# settings.DEBUG=True + InMemoryChannelLayer
python manage.py runserver --noreload

마지막으로: 압축 요약(면접 스피치용)

한 줄 소개: Django 기반의 협업 앱으로, HTTP(입장/업로드) + ASGI/Channels(실시간 채팅/이벤트)를 결합했습니다. Redis 채널 레이어로 다중 워커 간 메시지를 중계하고, Kakao/Naver 소셜 로그인은 allauth를 사용해 안전한 OAuth 흐름 위에 얹었습니다.

강점: Django의 생산성 + Channels 실시간성 + Redis 확장성. 트랜잭션/락으로 경쟁상황을 방지하고, 권한/밴/정원 등 도메인 규칙을 모델 메서드로 응집했습니다.

개선 포인트: 방 비밀번호 해시, 업로드 검증/스토리지 분리, 레이트리밋, Postgres 전환 검토.

원하면 각 파일별로 더 세세한 라인단 주석(예: consumers.py 전체, views.py의 각 뷰, settings.py 섹션별)을 문서 형식으로 쭉 달아줄게요. 지금 당장 필요한 파일명을 말해주면 그 파일은 한 줄 한 줄 풀어서 설명해드릴게요.