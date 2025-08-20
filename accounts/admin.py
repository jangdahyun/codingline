from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUA
from .models import User

@admin.register(User)
class UserAdmin(BaseUA):
    # 목록 컬럼: 상속 필드 + 커스텀 필드 혼용 가능
    list_display = ("id", "username", "email", "display_name", "is_active", "date_joined")
    # 검색: username/email/display_name 부분검색(큰 데이터면 느릴 수 있음)
    search_fields = ("username", "email", "display_name")
    # 필터/정렬/날짜 네비게이션(편의)
    list_filter = ("is_active", "is_staff", "is_superuser")
    ordering = ("-date_joined",)          # 최근 가입 순
    date_hierarchy = "date_joined"        # 상단에 날짜 네비게이션 바 생성
