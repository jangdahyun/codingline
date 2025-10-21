# CodingLine 요약

## 1. 백엔드 기술 스택
- Python 3 · Django · Django Channels(AsyncJsonWebsocketConsumer)
- ORM/마이그레이션, ValidationError · PermissionDenied · UniqueConstraint 활용
- `AUTH_USER_MODEL` 커스텀 유저 연동, `slugify`로 고유 슬러그 자동 생성(-2, -3…)
- Form/Fetch 기반 REST-ish API: 방 수정·삭제, 이미지 업로드/삭제, 메시지 페이지네이션

## 2. 아키텍처 흐름
- **HTTP**  
  메시지 페이지네이션(`/rooms/<slug>/messages/?page=n`), 방 수정/삭제, 이미지 업로드 API → ORM 조회/저장 → JSON 응답 → 클라이언트 무한스크롤/즉시 반영.
- **WebSocket**  
  접속 시 `room.can_enter()` 검사 → `RoomMember.open_conn` 증가 → 스냅샷 전송.  
  `receive_json`으로 채팅/드로잉/이미지 승인 등 액션 처리.  
  `disconnect`에서 GRACE_SECONDS 지연 후 최종 퇴장 여부 판정, 마지막 이용자면 방 삭제 및 `room_closed` 브로드캐스트.
- **방 삭제/수정**  
  권한 확인 → 트랜잭션 → 삭제 전 식별자 백업 → `transaction.on_commit()` 후 그룹(`room_{id}`)과 `lobby`에 이벤트 발행.

## 3. 데이터 모델 핵심
- **Room**: slug·capacity·password, `can_enter`, `room_update`, `room_delete`, `transfer_ownership_to_earliest`, `kick/unban` 등 도메인 규칙 캡슐화.
- **RoomMember**: role(owner/member)·is_banned·open_conn, `(room, user)` UniqueConstraint, `(room, open_conn)` 인덱스.
- **Message**: 텍스트/이미지 중 1개 필수, `(room, created_at)` 인덱스.  
  → `open_conn` 카운터로 현재 접속자 계산(멀티탭 대응).

## 4. 동시성 & 정합성 전략
- 모든 상태 변경에 `transaction.atomic()`
- 경쟁 구간 `select_for_update()`로 행 잠금
- 카운터 증감은 `F()` 표현식으로 원자적 처리
- **항상 `transaction.on_commit()`에서만** WS 브로드캐스트 → 롤백 시 유령 이벤트 차단
- 방 삭제 시 필요한 식별자(id/slug/name)를 미리 백업해 삭제 후 레퍼런스 문제 방지

## 5. 실시간 채널 설계
- 그룹: `room_{id}`(방 내부), `room_{id}_user_{uid}`(개인), `lobby`(방 목록)
- 접속 시 스냅샷(버전 포함) 1회 제공 → 이후 `user_joined`/`user_left` 델타만 전파
- 강퇴/방 닫힘 시 close code 사용: `kicked(4403)`, `room_closed(4404)`, `leave(4000)`, 비로그인(4001)

## 6. Signals 기반 로비 동기화
- `post_save(Room)` → 생성·수정 이벤트(lobby)
- `pre_delete/post_delete(Room)` → 삭제 전 id 보관 후 `room_deleted` 1회 발행
- 시그널과 모델 메서드 간 중복 브로드캐스트 여부 점검 및 조정 수행

## 7. 권한 · 검증
- 방장·스태프만 방 수정/삭제/강퇴 가능
- 입장 정책: 로그인 필수, 밴 사용자 차단, 정원 초과 금지(방장은 예외)
- 이름·정원 범위 검증, 메시지는 텍스트/이미지 중 하나 필수
- 비밀번호 입력 시 비교(필요 시 해시로 교체 가능)

## 8. 파일 업로드 흐름
- `ImageField(upload_to='room_images/%Y/%m/%d/')`
- 업로드 성공 시 즉시 WS로 이미지 메시지 브로드캐스트
- 삭제 API 제공, 권한·소유자 검증 적용

## 9. 로깅/운영
- 전용 로거(`collab`, `lobby`) & `log_step`으로 이벤트 단계별 추적
- on_commit 시점, 중복 이벤트, 퇴장 지연 등의 장애 조사에 활용

## 10. 구축 로드맵(학습 순서)
1. Django 프로젝트/앱 구성, 설정(auth, static/media, DB)
2. 유저 인증/세션
3. 모델링: Room · RoomMember · Message → 마이그레이션 · Admin
4. 도메인 메서드 구현(권한/밸리데이션/입장 정책)
5. 트랜잭션·락·F()로 경쟁 상황 대비 및 테스트
6. Channels 라우팅, `RoomPresenceConsumer` 작성, 그룹 설계
7. HTTP API 작성(메시지 페이지, 이미지 업/삭제, 방 수정/삭제, 강퇴)
8. 시그널로 로비 목록 동기화 후 중복 이벤트 점검
9. 프런트 WS 연동(스냅샷·델타, 이미지 뷰어, 채팅 UX)
10. 운영 품질 점검(on_commit, 멀티탭, 재접속 등)

## 11. 포트폴리오 어필 문장
- “Django Channels 기반 실시간 협업 방을 구현, 방·로비 그룹 브로드캐스트로 채팅/이미지/프레즌스를 동기화했습니다.”
- “`transaction.atomic()` + `select_for_update()` + `F()` 조합으로 `open_conn` 기반 프레즌스를 원자적으로 관리했습니다.”
- “`transaction.on_commit()`에서만 이벤트를 발행해 롤백 시 유령 이벤트를 차단, 프런트 레이스를 최소화했습니다.”
- “방장 퇴장 시 `joined_at` 순으로 자동 소유권 이양하고 락으로 경쟁 상태를 방지했습니다.”
- “시그널과 도메인 로직 중복을 분석해 로비 이벤트 중복 전송을 제거했습니다.”
- “서버 페이지네이션 + 클라이언트 델타 병합으로 무한 스크롤 채팅 UX를 구현했습니다.”
- “방장/멤버 권한, 강퇴·해제, 정원/밴/비번 정책을 도메인 메서드로 묶어 보안과 재사용성을 높였습니다.”

## 12. 꼭 복습할 코드 패턴
- **트랜잭션 & 락**
  ```python
  with transaction.atomic():
      mem, _ = (RoomMember.objects
                .select_for_update()
                .get_or_create(...))
      mem.open_conn = F("open_conn") + 1
      mem.save(update_fields=["open_conn"])
  ```
  동시 접속에서 경쟁 없이 카운터를 업데이트.

- **커밋 후 브로드캐스트**
  ```python
  def _after_commit():
      async_to_sync(channel_layer.group_send)(group, payload)

  transaction.on_commit(_after_commit)
  ```
  DB가 확정된 뒤에만 이벤트 전송.

- **소유권 이전**
  ```python
  with transaction.atomic():
      room = Room.objects.select_for_update().get(pk=self.pk)
      nxt = (RoomMember.objects.select_for_update()
             .filter(room=room, is_banned=False)
             .exclude(user_id=room.created_by_id)
             .order_by("joined_at", "id")
             .first())
      if nxt:
          room.created_by_id = nxt.user_id
          room.save(update_fields=["created_by"])
  ```

- **입장 정책**
  ```python
  def can_enter(self, user):
      if not user.is_authenticated:
          return False, "로그인이 필요합니다."
      if RoomMember.objects.filter(room=self, user=user, is_banned=True).exists():
          return False, "강퇴된 사용자입니다."
      if user.pk != self.created_by_id:
          current = RoomMember.objects.filter(
              room=self, is_banned=False, open_conn__gt=0
          ).count()
          if current >= self.capacity:
              return False, "정원이 가득 찼습니다."
      return True, None
  ```
  도메인 규칙을 모델 메서드로 응집해 재사용.
