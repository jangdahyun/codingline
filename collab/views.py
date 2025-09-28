# ------------------------------------------------------------
# imports
# ------------------------------------------------------------
from __future__ import annotations
from email.mime import application
from math import log
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.forms import ValidationError
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods   # ← GET/POST 한정 데코레이터
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator                                 # ← 페이지네이션

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from requests import post

from .forms import RoomCreateForm
from .models import Room, RoomMember, Message     
from django.db import transaction

import logging
logger = logging.getLogger("collab")


# ------------------------------------------------------------
# 공통 헬퍼들 (중복 제거 + 가독성)
# ------------------------------------------------------------
def _session_key(room_id: int) -> str:
    return f"room_access:{room_id}"

def _is_owner(user, room: Room) -> bool:
    return user.is_authenticated and user.pk == room.created_by_id

def _grant_session_access(request, room: Room) -> None:
    request.session[_session_key(room.pk)] = True

def _ensure_membership(room, user, role):
    # 강퇴자는 절대 멤버십 만들지 않음 (이중 안전장치)
    if RoomMember.objects.filter(room=room, user=user, is_banned=True).exists():
        raise PermissionDenied("강퇴된 사용자입니다.")
    mem, _ = RoomMember.objects.get_or_create(
        room=room, user=user, defaults={"role": role}
    )
    # 필요 시 role 보정 등...
    return mem


def safe_group_send(group: str, message: dict) -> None:
    """채널 레이어 실패가 앱 에러로 번지지 않게 보호."""
    try:
        layer = get_channel_layer()
        if not layer:
            logger.warning("No channel layer; skip send to %s", group)
            return
        async_to_sync(layer.group_send)(group, message)
    except Exception:
        logger.exception("group_send failed (group=%s, message=%s)", group, message)


# ------------------------------------------------------------
# 기본 뷰들
# ------------------------------------------------------------
def home(request):
    """메인 화면: 검색 + 방 생성"""
    q = request.GET.get("q", "").strip()
    rooms = Room.objects.all().order_by("-created_at")
    if q:
        from django.db.models import Q
        rooms = rooms.filter(Q(Romname__icontains=q) | Q(topic__icontains=q))

    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.error(request, "로그인이 필요합니다.")
            return redirect("account_login")

        form = RoomCreateForm(request.POST)
        if form.is_valid():
            room = form.save(commit=False)
            room.created_by = request.user
            room.save()
            _ensure_membership(room, request.user, RoomMember.ROLE_OWNER)
            messages.success(request, f"방 '{room.Romname}' 이 생성되었습니다.")
            print("Messages2: ", messages.get_messages(request))  # 서버 로그에서 메시지 확인
            return redirect("room-detail", slug=room.slug)
    else:
        form = RoomCreateForm()

    return render(request, "collab/home.html", {"form": form, "rooms": rooms, "q": q})

@login_required
@require_http_methods(["GET"])
def room_can_enter_json(request, slug):
    """
    로비에서 입장 확인하는 전용 
    - ok=True  : 입장 가능
    - ok=False : 사유(reason)를 error로 내려줌 (ex. '강퇴된 사용자입니다.')
    """
    room = get_object_or_404(Room, slug=slug)
    ok, reason = room.can_enter(request.user)
    if ok:
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": False, "error": reason or "입장할 수 없습니다."}, status=403)

@require_POST
@login_required
def room_enter_json(request, slug):
    """입장 API: 비번/정원/밴 검사 → 세션 플래그 → 멤버십 보장 → next URL"""
    room = get_object_or_404(Room, slug=slug)
    ok, reason = room.can_enter(request.user)
    if not ok:
        return JsonResponse({"ok": False, "error": reason}, status=403)
    # 비번 없는 공개 방
    if not room.password:
        _grant_session_access(request, room)
        _ensure_membership(room, request.user, RoomMember.ROLE_MEMBER)
        return JsonResponse({"ok": True, "next": reverse("room-detail", kwargs={"slug": room.slug})})

    # 비번 있는 방
    pw = request.POST.get("password", "")
    if room.check_room_password(pw):
        ok, reason = room.can_enter(request.user)
        if not ok:
            return JsonResponse({"ok": False, "error": reason}, status=403)
        _grant_session_access(request, room)
        _ensure_membership(room, request.user, RoomMember.ROLE_MEMBER)
        return JsonResponse({"ok": True, "next": reverse("room-detail", kwargs={"slug": room.slug})})

    return JsonResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다."}, status=400)


@login_required
def room_leave(request, slug):
    """방 나가기: 방장 위임 → 내 멤버십 삭제 → '빈 방'이면 삭제"""
    room = get_object_or_404(Room, slug=slug)

    with transaction.atomic():
        if _is_owner(request.user, room):
            room.transfer_ownership_to_earliest()

        RoomMember.objects.filter(room=room, user=request.user).delete()

        has_active = (
            RoomMember.objects.select_for_update()
            .filter(room=room, is_banned=False)
            .exists()
        )
        if not has_active:
            room.delete()
            logger.info("방 삭제: (마지막 사람이 나감)")
            messages.info(request, "마지막 사람이어서 방이 삭제되었습니다.")
            return redirect("home")
    logger.info(f"방 나감: user={request.user.pk}, room={room.pk}")
    return redirect("home")


@require_POST
@login_required
@transaction.atomic
def api_kick(request, slug, user_id):
    """강퇴: DB 반영 → 커밋 후 개인 그룹에 'kicked' 이벤트 전송"""
    room = get_object_or_404(Room, slug=slug)
    User = get_user_model()
    target = get_object_or_404(User, pk=user_id)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
             "application/json" in request.headers.get("accept", "")
    
    try:
        room.kick(request.user, target)  # 강퇴 처리
        msg = f"{target.username}님을 강퇴했습니다."
        
        logger.info(f"강퇴: by={request.user.pk}, target={target.pk}, room={room.pk}")
        
        # 트랜잭션이 커밋된 후 비동기 작업 처리
        transaction.on_commit(lambda: safe_group_send(
            f"room_{room.pk}_user_{target.pk}",
            {"type": "kicked", "msg": "방장에 의해 강퇴되었습니다."}
        ))

        if is_ajax:
            # AJAX 요청 시 JSON 응답으로 메시지 반환
            return JsonResponse({"ok": True, "message": msg})
        else:
            # 일반 요청에서는 메시지 출력 후 리디렉션
            messages.success(request, msg)
            return redirect("room-detail", slug=room.slug)
    
    except PermissionDenied as e:
        if is_ajax:
            return JsonResponse({"ok": False, "error": str(e)}, status=403)
        else:
            messages.error(request, str(e))
            return redirect("room-detail", slug=room.slug)


@require_POST
@login_required
def api_unban(request, slug):
    """밴 해제(JSON)"""
    room = get_object_or_404(Room, slug=slug)
    target_id = request.POST.get("user_id")
    if not target_id:
        return JsonResponse({"ok": False, "error": "user_id가 필요합니다."}, status=400)

    User = get_user_model()
    target = get_object_or_404(User, pk=target_id)

    try:
        ok = room.unban(request.user, target)
        return JsonResponse({"ok": ok})
    except PermissionDenied as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=403)

@login_required(login_url='/accounts/login')
def room_detail(request, slug):
    room = get_object_or_404(Room, slug=slug)
    active_users = (RoomMember.objects
                    .filter(room=room, is_banned=False, open_conn__gt=1)
                    .select_related("user")
                    .values("user_id", "user__username"))
    users_with_conn_1 = RoomMember.objects.filter(room=room, is_banned=False, open_conn=1).select_related("user").values("user_id", "user__username")
    print(f"ds:",users_with_conn_1)
    

    print("활성 사용자:", list(active_users))
    # 1) 강퇴/정원 검사: 모든 사용자(방장 제외?)에게 공통 적용
    #   - 방장을 무조건 통과시킬지 여부는 정책에 따라 선택.
    #   - 일반적으론 방장도 검사 통과(당연히 통과)니까 그대로 둡니다.
    ok, reason = room.can_enter(request.user)
    if not ok:
        # 금지: 메시지 보여주고 홈으로 보내거나 403
        messages.error(request, reason or "이 방에 입장할 수 없습니다.")
        return redirect("home")  # 또는: return HttpResponseForbidden(reason)
    
    # 2) (선택) 방장이면 세션 접근 허용
    if _is_owner(request.user, room):
        _grant_session_access(request, room)
        return render(request, "collab/room_detail.html", {"room": room})

    # 3) 비번 방이면 세션 키 없을 때 차단
    if room.requires_password and not request.session.get(_session_key(room.pk)):
        return HttpResponseForbidden("이 방은 비밀번호가 필요합니다.")

    # 4) 멤버십 upsert는 '검사 통과 후'에만
    if request.user.is_authenticated:
        role = RoomMember.ROLE_OWNER if _is_owner(request.user, room) else RoomMember.ROLE_MEMBER
        mem = _ensure_membership(room, request.user, role)
        mem.last_active_at = timezone.now()
        mem.save(update_fields=["last_active_at"])

    return render(request, "collab/room_detail.html", {"room": room})


# ------------------------------------------------------------
# 메시지/이미지 API (디테일 페이지용)
# ------------------------------------------------------------

@login_required
@require_http_methods(["GET"])
def api_messages_list(request, slug):
    """
    최근 메시지(텍스트+이미지)를 페이지네이션으로 반환.
    응답 예:
    {
      "ok": true,
      "page": 1,
      "num_pages": 3,
      "results": [{"id":1,"user":"alice","content":"hi","image_url":null,"ts":"..."}]
    }
    """
    room = get_object_or_404(Room, slug=slug)

    ok, reason = room.can_enter(request.user)
    if not ok:
        return JsonResponse({"ok": False, "error": reason}, status=403)

    page = int(request.GET.get("page", 1))
    qs = Message.objects.filter(room=room).select_related("user")
    p = Paginator(qs, 50)                         # 페이지당 50개
    page_obj = p.get_page(page)

    def _s(m: Message):
        return {
            "id": m.id,
            "user": getattr(m.user, "username", str(m.user_id)),
            "content": m.content,
            "image_url": (m.image.url if m.image else None),
            "ts": m.created_at.isoformat(),
        }

    return JsonResponse({
        "ok": True,
        "page": page_obj.number,
        "num_pages": p.num_pages,
        "results": [_s(m) for m in page_obj.object_list],
    })


@require_POST
@login_required
def api_image_upload(request, slug):
    """
    다중 이미지 업로드 → Message 생성 → 방 그룹에 image 이벤트 브로드캐스트.
    요청: form-data로 images 여러 개
    응답: {"ok":true,"count":N,"ids":[...]}
    """
    room = get_object_or_404(Room, slug=slug)

    ok, reason = room.can_enter(request.user)
    if not ok:
        return JsonResponse({"ok": False, "error": reason}, status=403)

    files = request.FILES.getlist("images")
    if not files:
        return JsonResponse({"ok": False, "error": "업로드할 파일이 없습니다."}, status=400)

    created = []
    for f in files:
        m = Message.objects.create(room=room, user=request.user, image=f)
        created.append(m)
        # 실시간 브로드캐스트
        safe_group_send(
            f"room_{room.pk}",
            {
                "type": "image",
                "user": getattr(request.user, "username", str(request.user.pk)),
                "image_url": m.image.url,
                "message_id": m.id,
                "ts": m.created_at.isoformat(),
            }
        )
    return JsonResponse({"ok": True, "count": len(created), "ids": [m.id for m in created]})


@require_POST
@login_required
def api_image_delete(request, slug, message_id: int):
    """
    이미지 메시지 삭제(업로더 또는 방장만).
    성공: {"ok": true}
    실패: {"ok": false, "error": "..."}
    """
    room = get_object_or_404(Room, slug=slug)

    ok, reason = room.can_enter(request.user)
    if not ok:
        return JsonResponse({"ok": False, "error": reason}, status=403)

    msg = get_object_or_404(Message, id=message_id, room=room)
    is_owner = (request.user.pk == room.created_by_id)
    if not (msg.user_id == request.user.pk or is_owner):
        return JsonResponse({"ok": False, "error": "삭제 권한이 없습니다."}, status=403)

    image_path = msg.image.path if msg.image else None
    msg.delete()

    # 실제 파일 삭제(선택)
    if image_path:
        try:
            import os
            if os.path.exists(image_path):
                os.remove(image_path)
        except Exception:
            logger.warning("파일 삭제 실패: %s", image_path)

    return JsonResponse({"ok": True})

@login_required
@require_POST
def api_room_update(request, slug):
    """POST /api/rooms/<slug>/update/  Body: JSON{name,topic,is_private,capacity,password}"""
    room = get_object_or_404(Room, slug=slug)
    import json
    try:
        data = json.loads(request.body.decode() or "{}")
    except Exception:
        data = {}
    try:
        updated = room.room_update(
            actor=request.user,
            name=data.get("name"),
            topic=data.get("topic"),
            is_private=data.get("is_private"),
            capacity=data.get("capacity"),
            password=data.get("password"),
            broadcast=True,
        )
        return JsonResponse({
            "ok": True,
            "slug": updated.slug,
            "name": updated.Romname,
            "topic": updated.topic,
            "is_private": updated.is_private,
            "capacity": updated.capacity,
            "requires_password": updated.requires_password,
        })
    except PermissionDenied as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=403)
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@login_required
@require_POST
def api_room_delete(request, slug):
    """POST /api/rooms/<slug>/delete/"""
    room = get_object_or_404(Room, slug=slug)
    try:
        room.room_delete(actor=request.user, broadcast=True)
        return JsonResponse({"ok": True})
    except PermissionDenied as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=403)

