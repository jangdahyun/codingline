import re
from django.urls import re_path
from .consumers import RoomPresenceConsumer
from accounts.consumers import AuthPresenceConsumer
from collab.consumers import LobbyConsumer

# 한글 슬러그 지원: 슬래시만 제외하고 전부 허용
websocket_urlpatterns = [
    re_path(r"^ws/rooms/(?P<slug>[^/]+)/$", RoomPresenceConsumer.as_asgi()),
    re_path(r"^ws/auth/$", AuthPresenceConsumer.as_asgi()),
    re_path(r"^ws/lobby/$", LobbyConsumer.as_asgi()),
]
