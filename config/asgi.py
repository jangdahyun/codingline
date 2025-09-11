# config/asgi.py

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# ↑ 1) settings 모듈을 가장 먼저 지정

from django.core.asgi import get_asgi_application
django_asgi_app = get_asgi_application()
# ↑ 2) 여기서 Django를 부팅(앱 로딩 완료). 그 다음에야 앱 코드 import 가능

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

import collab.routing  # ↑ 3) 이제 import! (websocket_urlpatterns 읽어오기)

application = ProtocolTypeRouter({
    "http": django_asgi_app,                    # HTTP는 기존 Django 처리
    "websocket": AuthMiddlewareStack(
        URLRouter(collab.routing.websocket_urlpatterns)  # /ws/rooms/<slug>/ → Consumer
    ),
})
