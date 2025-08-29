from django.urls import path                   # ① path 함수
from . import views                            # ② 뷰 임포트

urlpatterns = [
    path("", views.home, name="home"),         # ③ 루트 = 메인 화면
    path("rooms/<slug:slug>/",                 # ④ 방 상세(추후 WebSocket UI)
         views.room_detail, name="room-detail"),
    path("rooms/<slug:slug>/enter/", 
        views.room_enter_json, name="room-enter"),  # ⑤ 방 입장(비번 폼)
]
