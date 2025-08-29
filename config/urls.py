from django.contrib import admin
from django.urls import path ,include
from django.conf import settings
from django.conf.urls.static import static #미디어 서빙(개발용)
from django.views.generic import RedirectView # 어떤 URL → 다른 URL로 리다이렉트
from django.urls import reverse_lazy # URL name을 URL로 변환 reverse_lazy("account_login") → "/accounts/login/"
from accounts.views import CustomSocialSignupView


urlpatterns = [
    path('admin/', admin.site.urls),
    # /accounts/ 이하 모두를 /account/로 302 이동
    path(
        "accounts/3rdparty/signup/",        # ← 프리픽스 다시 'accounts/'
        CustomSocialSignupView.as_view(),
        name="socialaccount_signup",        # ← allauth가 reverse하는 이름(고정)
    ),

    # ✅ allauth의 기본 라우트들을 '/accounts/' 밑으로
    path("accounts/", include("allauth.urls")),
    path("", include("collab.urls")),                      # 메인 라우팅
    path("login/", RedirectView.as_view(url=reverse_lazy("account_login"), permanent=False)), #permanent=False → 302 임시 리다이렉트로 이동
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  # 8) 개발에서 프로필 이미지 접근
