from django.urls import path                   # ① path 함수
from . import views                            # ② 뷰 임포트

urlpatterns = [
    path("", views.home, name="home"),         # ③ 루트 = 메인 화면
    path("rooms/<str:slug>/",                 # ④ 방 상세(추후 WebSocket UI)
         views.room_detail, name="room-detail"),
    path("rooms/<str:slug>/enter/", 
        views.room_enter_json, name="room-enter"),  # ⑤ 방 입장(비번 폼)
    path("rooms/<str:slug>/leave/", 
        views.room_leave, name="room-leave"),      # ⑥ 방 나가기
    path("rooms/<str:slug>/kick/<int:user_id>/", 
        views.api_kick, name="api_kick"),          # ⑦ 방 밴
    path("rooms/<str:slug>/unban/", 
        views.api_unban, name="api_unban"),      # ⑧ 방 밴 해제

    path('rooms/<str:slug>/messages/', views.api_messages_list, name='api_messages_list'),           # GET: 최근 메시지
    path('rooms/<str:slug>/images/upload/', views.api_image_upload, name='api_image_upload'),        # POST: 이미지 업로드(다중)
    path('rooms/<str:slug>/images/<int:message_id>/delete/', views.api_image_delete, name='api_image_delete'),  # POST: 이미지 삭제
]
