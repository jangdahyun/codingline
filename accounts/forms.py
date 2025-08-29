# forms.py

import re
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from allauth.account.forms import LoginForm, SignupForm as AccountSignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from allauth.account.adapter import get_adapter

# ── 로그인 폼 (그대로 유지) ───────────────────────────────────────
class MyLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # 부모 초기화
        # 아이디/이메일 입력창
        self.fields["login"].widget.attrs.update({
            "class": "input w-full",
            "placeholder": "아이디 또는 이메일",
            "autocomplete": "username",
        })
        # 비밀번호 입력창
        self.fields["password"].widget.attrs.update({
            "class": "input w-full",
            "placeholder": "비밀번호",
            "autocomplete": "current-password",
        })

# ── 공통 유틸 ────────────────────────────────────────────────────
User = get_user_model()
PHONE_FIELD = "phone" if hasattr(User, "phone") else ("phone_number" if hasattr(User, "phone_number") else None)

def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    return digits

def phone_in_use(phone: str) -> bool:
    if not PHONE_FIELD or not phone:
        return False
    return User.objects.filter(**{PHONE_FIELD: phone}).exists()

def suggest_username(sl) -> str:
    cands = []
    acc   = getattr(sl, "account", None)
    extra = getattr(acc, "extra_data", {}) or {}
    prov  = (getattr(acc, "provider", "") or "").lower()

    if prov.startswith("naver"):
        resp = extra.get("response") or extra
        if resp.get("name") or resp.get("nickname"):
            cands.append(resp.get("name") or resp.get("nickname"))
        if resp.get("email"):
            cands.append(resp["email"].split("@")[0])
    elif prov.startswith("kakao"):
        ka = extra.get("kakao_account") or {}
        prof = ka.get("profile") or {}
        props = extra.get("properties") or {}
        if prof.get("nickname") or props.get("nickname"):
            cands.append(prof.get("nickname") or props.get("nickname"))

    if not cands:
        cands = ["user"]
    return get_adapter().generate_unique_username(cands)

def social_initials(sl) -> dict:
    acc   = getattr(sl, "account", None)
    extra = getattr(acc, "extra_data", {}) or {}
    prov  = (getattr(acc, "provider", "") or "").lower()
    data  = {}
    if prov.startswith("naver"):
        resp = extra.get("response") or extra
        data["nickname"] = resp.get("nickname") or ""
        data["email"]    = resp.get("email") or ""
        phone = normalize_phone(resp.get("mobile") or resp.get("mobile_e164") or "")
        if phone:
            data["phone_number"] = phone
        by, bd = resp.get("birthyear"), resp.get("birthday")
        if by and bd:
            data["birth_date"] = f"{by}-{bd}"
    return data

# ── 공통 필드 + 저장 ─────────────────────────────────────────────
class ExtraFieldsMixin(forms.Form):
    nickname     = forms.CharField(label="닉네임", required=False, max_length=30)
    phone_number = forms.CharField(label="전화번호", required=False, max_length=20,
                                   widget=forms.TextInput(attrs={"autocomplete": "tel"}))
    birth_date   = forms.DateField(label="생년월일", required=False,
                                   widget=forms.DateInput(attrs={"type": "date"}))

    def clean_phone_number(self):
        phone = normalize_phone(self.cleaned_data.get("phone_number", ""))
        if phone and phone_in_use(phone):
            raise ValidationError("이미 사용 중인 전화번호예요.")
        return phone

    def _save_extra_to_user(self, user):
        cd = self.cleaned_data
        if cd.get("nickname"):
            if hasattr(user, "display_name"): user.display_name = cd["nickname"]
            elif hasattr(user, "nickname"):   user.nickname     = cd["nickname"]
        if cd.get("phone_number"):
            if hasattr(user, "phone"):        user.phone        = cd["phone_number"]
            elif hasattr(user, "phone_number"): user.phone_number = cd["phone_number"]
        if cd.get("birth_date") and hasattr(user, "birth_date"):
            user.birth_date = cd["birth_date"]
        user.save()
        return user

# ── 일반 회원가입 ────────────────────────────────────────────────
class MySignupForm(ExtraFieldsMixin, AccountSignupForm):
    def save(self, request):
        user = super().save(request)
        return self._save_extra_to_user(user)

# ── 소셜 회원가입 ────────────────────────────────────────────────
class MySocialSignupForm(ExtraFieldsMixin, SocialSignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sl = getattr(self, "sociallogin", None)
        if not self.is_bound and sl:
            # username 초기값(폼/필드 둘 다) — 템플릿 구현과 무관하게 표시되도록
            self.initial["username"] = suggest_username(sl)
            self.fields["username"].initial = self.initial["username"]
            # 나머지 초기값 배치
            self.initial.update(social_initials(sl))
            for k, v in self.initial.items():
                if k in self.fields and self.fields[k].initial in (None, ""):
                    self.fields[k].initial = v

    def clean_username(self):
        v = self.cleaned_data.get("username")
        if not v:
            v = self.initial.get("username") or self.fields["username"].initial
        return v

    def save(self, request):
        user = super().save(request)
        return self._save_extra_to_user(user)
