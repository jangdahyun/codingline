from django.shortcuts import render, redirect, get_object_or_404  # ① 렌더/리다이렉트/404
from django.contrib import messages                               # ② 상단 알림 메시지
from django.db.models import Q                                    # ③ 검색용 OR 조건
from .models import Room                                          # ④ 방 모델
from .forms import RoomCreateForm                                 # ⑤ 방 생성 폼

def home(request):                                                # ⑥ 메인 화면
    q = request.GET.get("q", "").strip()                          # ⑦ 검색어(?q=)
    rooms = Room.objects.all().order_by("-created_at")            # ⑧ 최신순 목록
    if q:                                                         # ⑨ 검색 적용(제목/주제)
        rooms = rooms.filter(Q(Romname__icontains=q) | Q(topic__icontains=q))

    if request.method == "POST":                                  # ⑩ 방 생성 요청
        if not request.user.is_authenticated:                     # ⑪ 로그인 체크
            messages.error(request, "로그인이 필요합니다.")
            return redirect("account_login")                      # ⑫ allauth 로그인 URL name
        form = RoomCreateForm(request.POST)                       # ⑬ 폼 바인딩
        if form.is_valid():                                       # ⑭ 유효성 검사
            room = form.save(commit=False)                        # ⑮ 인스턴스만 생성
            room.created_by = request.user 
            room.save()                   # ⑯ 만든 사람 세팅
            messages.success(request, f"방 '{room.Romname}' 이 생성되었습니다.")
            return redirect("room-detail", slug=room.slug)        # ⑱ 상세로 이동
    else:
        form = RoomCreateForm()                                   # ⑲ GET: 빈 폼

    ctx = {"form": form, "rooms": rooms, "q": q}                  # ⑳ 템플릿 컨텍스트
    return render(request, "collab/home.html", ctx)               # ㉑ 렌더

def room_detail(request, slug):                                   # ㉒ 상세(소켓 붙일 자리)
    room = get_object_or_404(Room, slug=slug)                     # ㉓ slug로 조회/404
    return render(request, "collab/room_detail.html", {"room": room})  # ㉔ 렌더
