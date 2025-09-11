from django.contrib import admin                         # admin 사이트 라우팅
from django.urls import path, include                    # URL 패턴/앱 라우팅 include
from django.conf import settings                         # DEBUG / MEDIA 설정 접근
from django.conf.urls.static import static               # 개발 시 미디어 서빙
from django.views.generic import RedirectView            # 간단 리다이렉트 뷰
from django.urls import reverse_lazy                     # URL name → 실제 URL
from accounts.views import CustomSocialSignupView        # 소셜 회원가입 커스텀 뷰

urlpatterns = [
    path('admin/', admin.site.urls),                     # /admin/ → Django admin

    # allauth의 특정 경로 커스터마이징(소셜 3rdparty signup)
    path('accounts/3rdparty/signup/',                    # /accounts/3rdparty/signup/
         CustomSocialSignupView.as_view(),
         name='socialaccount_signup'),

    # allauth 기본 라우트들 (로그인/로그아웃/회원가입 등)
    path('accounts/', include('allauth.urls')),          # /accounts/... 전부 allauth에 위임

    # 메인 앱 라우팅(여기서 collab의 모든 URL을 관리)
    path('', include('collab.urls')),                    # ← API 3개도 collab.urls 안으로 이동

    # /login/ → allauth 로그인으로 임시 리다이렉트(302)
    path('login/', RedirectView.as_view(
        url=reverse_lazy('account_login'), permanent=False)),
]

# 개발환경에서만 미디어 서빙 (운영은 Nginx/S3 등 사용)
if settings.DEBUG:                                       # DEBUG일 때만 추가
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
