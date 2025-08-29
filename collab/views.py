from django.shortcuts import render, redirect, get_object_or_404  #  렌더/리다이렉트/404
from django.contrib import messages                               #  상단 알림 메시지
from django.db.models import Q                                    #  검색용 OR 조건
from .models import Room                                          #  방 모델
from .forms import RoomCreateForm               #  방 생성/입장 폼
from django.http import JsonResponse,HttpResponseForbidden
from django.urls import reverse
from django.views.decorators.http import require_POST

def home(request):                                                #  메인 화면
    q = request.GET.get("q", "").strip()                          #  검색어(?q=)
    rooms = Room.objects.all().order_by("-created_at")            #  최신순 목록
    if q:                                                         #  검색 적용(제목/주제)
        rooms = rooms.filter(Q(Romname__icontains=q) | Q(topic__icontains=q))

    if request.method == "POST":                                  #  방 생성 요청
        if not request.user.is_authenticated:                     #  로그인 체크
            messages.error(request, "로그인이 필요합니다.")
            return redirect("account_login")                      #  allauth 로그인 URL name
        form = RoomCreateForm(request.POST)                       #  폼 바인딩
        if form.is_valid():                                       #  유효성 검사
            room = form.save(commit=False)                        #  인스턴스만 생성
            room.created_by = request.user 
            room.save()                   # ⑯ 만든 사람 세팅
            messages.success(request, f"방 '{room.Romname}' 이 생성되었습니다.")
            return redirect("room-detail", slug=room.slug)        #  상세로 이동
    else:
        form = RoomCreateForm()                                   #  GET: 빈 폼

    ctx = {"form": form, "rooms": rooms, "q": q}                  #  템플릿 컨텍스트
    return render(request, "collab/home.html", ctx)               #  렌더


@require_POST
def room_enter_json(request, slug):
    """
    홈 화면의 모달/배너에서 AJAX로 비밀번호를 보내면,
    검증 후 세션 토큰을 저장하고 JSON으로 상세 URL을 돌려준다.
    """
    room = get_object_or_404(Room, slug=slug)
    session_key = f"room_access:{room.pk}"

    # 1) 비밀번호가 없으면(=요구 안 함) 바로 통과
    if not room.password:
        request.session[session_key] = True
        next_url = reverse("room-detail", kwargs={"slug": room.slug})
        return JsonResponse({"ok": True, "next": next_url})

    # 2) 비밀번호 검증 (해시/평문 구현에 맞춰 room.check_room_password 사용)
    pw = request.POST.get("password", "")
    if room.check_room_password(pw):
        request.session[session_key] = True
        next_url = reverse("room-detail", kwargs={"slug": room.slug})
        return JsonResponse({"ok": True, "next": next_url})

    # 3) 실패
    return JsonResponse({"ok": False, "error": "비밀번호가 올바르지 않습니다."}, status=400)

def room_detail(request, slug):
    room = get_object_or_404(Room, slug=slug)
    session_key = f"room_access:{room.pk}"
    if request.user.is_authenticated and request.user.pk == room.created_by_id:
        # (선택) 이후 같은 세션에서 바로 접근 가능하도록 플래그도 켜줌
        request.session[session_key] = True
        return render(request, "collab/room_detail.html", {"room": room})

    if room.requires_password and not request.session.get(session_key):
        return HttpResponseForbidden("이 방은 비밀번호가 필요합니다.")  # 리다이렉트 금지
    
    return render(request, "collab/room_detail.html", {"room": room})