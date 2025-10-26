# forms.py
from genericpath import exists
from django.contrib.auth.decorators import login_required
import re
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
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
    avatar       = forms.ImageField(label="프로필 이미지", required=False,
                                    widget=forms.ClearableFileInput(attrs={
                                        "accept": "image/*",
                                        "data-avatar-input": "1",
                                    }))
    nickname     = forms.CharField(label="닉네임", required=True, max_length=30)
    phone_number = forms.CharField(label="전화번호", required=True, max_length=20,
                                   widget=forms.TextInput(attrs={"autocomplete": "tel"}))
    birth_date   = forms.DateField(label="생년월일", required=True,
                                   widget=forms.DateInput(attrs={"type": "date"}))

    def clean_phone_number(self):
        phone = normalize_phone(self.cleaned_data.get("phone_number", ""))
        if phone and phone_in_use(phone):
            raise ValidationError("이미 사용 중인 전화번호예요.")
        return phone
    
    def clean_nickname(self):
        nickname = (self.cleaned_data.get("nickname") or "").strip()
        if not nickname:
            raise ValidationError("닉네임을 입력해주세요.")

        qs = User.objects.filter(display_name=nickname)
        if qs.exists():
            raise ValidationError("이미 사용 중인 닉네임이에요.")
        return nickname

    def _save_extra_to_user(self, user):
        cd = self.cleaned_data
        avatar = cd.get("avatar")
        if avatar:
            user.avatar = avatar
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
        username = super().clean_username()
        if not username:
            username = self.initial.get("username") or self.fields["username"].initial
        if username and User.objects.filter(username=username).exists():
            raise ValidationError("이미 사용 중인 아이디예요.")
        return username

    def save(self, request):
        user = super().save(request)
        return self._save_extra_to_user(user)

class UsernameFindForm(forms.Form):
    display_name = forms.CharField(label="닉네임", max_length=150)
    birth_date = forms.DateField(label="생년월일", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        display = cleaned.get("display_name")
        birth = cleaned.get("birth_date")
        if display and birth:
            qs = User.objects.filter(display_name=display, birth_date=birth)
            if not qs.exists():
                raise ValidationError("일치하는 계정이 없습니다.")
            self.user = qs.first()
        return cleaned


class PasswordResetVerifyForm(forms.Form):
    username = forms.CharField(label="아이디", max_length=150)
    display_name = forms.CharField(label="닉네임", max_length=150)
    birth_date = forms.DateField(label="생년월일", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        username = cleaned.get("username")
        display = cleaned.get("display_name")
        birth = cleaned.get("birth_date")
        if username and display and birth:
            qs = User.objects.filter(
                username=username,
                display_name=display,
                birth_date=birth,
            )
            if not qs.exists():
                raise ValidationError("일치하는 계정이 없습니다.")
            self.user = qs.first()
        return cleaned


class PasswordResetSetForm(SetPasswordForm):
    """SetPasswordForm 그대로 활용 (폼 이름만 맞춤)."""
    pass

class ProfileUpdateForm(forms.ModelForm):
    phone = forms.CharField(
        label="전화번호",
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "010-1234-5678", "autocomplete": "tel"}),
    )

    class Meta:
        model = User
        fields = ["avatar", "display_name", "phone", "birth_date"]
        labels = {
            "avatar": "프로필 사진",
            "display_name": "닉네임",
            "phone": "전화번호",
            "birth_date": "생년월일",
        }
        widgets = {
            "display_name": forms.TextInput(attrs={"maxlength": 30, "placeholder": "닉네임"}),
            "birth_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_display_name(self):
        nickname = (self.cleaned_data.get("display_name") or "").strip()
        if not nickname:
            raise ValidationError("닉네임을 입력해주세요.")

        qs = User.objects.exclude(pk=self.instance.pk).filter(display_name=nickname)
        if qs.exists():
            raise ValidationError("이미 사용 중인 닉네임이에요.")
        return nickname

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data.get("phone") or "")
        if not phone:
            return ""
        if User.objects.exclude(pk=self.instance.pk).filter(phone=phone).exists():
            raise ValidationError("이미 사용 중인 전화번호예요.")
        return phone
