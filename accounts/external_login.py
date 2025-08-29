import json, logging, requests
from django.dispatch import receiver
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.dateparse import parse_date

from allauth.account.signals import user_signed_up
from allauth.socialaccount.signals import social_account_added
from allauth.socialaccount.models import SocialAccount

logger = logging.getLogger("accounts")
User = get_user_model()

# =============================
# 로그 유틸
# =============================
def _jd(obj) -> str:
    """JSON 문자열로 안전 변환 (한글 그대로, 들여쓰기)."""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    except Exception as e:
        return f"<JSON 변환 실패: {e}; type={type(obj)}>"

def _keys(d) -> list[str]:
    """딕셔너리 키 리스트(안전)."""
    try:
        return list((d or {}).keys())
    except Exception:
        return []

# =============================
# 프로필 추출
# =============================
def _extract_profile(extra: dict, provider: str | None):
    """닉네임/이미지/이메일/생일/휴대폰 추출 (카카오/네이버)."""
    provider_norm = (provider or "").lower()
    extra = extra or {}

    logger.debug("▶ 프로필 추출 시작: provider=%s, extra.keys=%s", provider_norm, _keys(extra))

    data = {
        "nickname": None,
        "image_url": None,
        "email": None,
        "birthday": None,   # 'YYYY-MM-DD'
        "mobile": None,     # 숫자만
    }

    if provider_norm.startswith("kakao"):
        acc = extra.get("kakao_account") or {}
        profile = acc.get("profile") or {}
        props = extra.get("properties") or {}
        logger.debug("  - kakao_account.keys=%s, profile.keys=%s, properties.keys=%s",
                     _keys(acc), _keys(profile), _keys(props))

        data["nickname"] = profile.get("nickname") or props.get("nickname")
        data["image_url"] = (
            profile.get("profile_image_url")
            or profile.get("thumbnail_image_url")
            or props.get("profile_image")
            or props.get("thumbnail_image")
        )

    elif provider_norm.startswith("naver"):
        resp = extra.get("response") or extra
        logger.debug("  - naver.response.keys=%s", _keys(resp))

        data["nickname"]  = resp.get("nickname") or resp.get("name")
        data["image_url"] = resp.get("profile_image") or resp.get("profile_image_url")
        data["email"]     = resp.get("email")
        by = resp.get("birthyear"); bd = resp.get("birthday")
        logger.debug("  - naver.birthyear=%r, birthday=%r", by, bd)
        if by and bd:
            data["birthday"] = f"{by}-{bd}"  # 예: 2002-03-29
        mobile = resp.get("mobile")
        if mobile:
            digits = "".join(ch for ch in mobile if ch.isdigit())
            if digits.startswith("82") and len(digits) >= 11:
                digits = "0" + digits[2:]
            data["mobile"] = digits

    else:
        logger.debug("  - 알 수 없는 provider=%r (추출 스킵)", provider)

    logger.debug("◀ 프로필 추출 결과:\n%s", _jd(data))
    return data

# =============================
# 아바타 저장
# =============================
def _save_avatar_from_url(user: User, url: str, provider: str = "social"):
    """이미지 URL 다운로드 → user.avatar 저장. 실패해도 앱 중단 X."""
    if not url:
        logger.debug("아바타 저장 스킵: URL 없음")
        return
    try:
        logger.debug("아바타 다운로드 시도: url=%s", url)
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        ext = "jpg"
        for cand in (".jpg", ".jpeg", ".png", ".webp"):
            if cand in url.lower():
                ext = cand.lstrip("."); break
        filename = f"{provider}_{user.pk}.{ext}"
        user.avatar.save(filename, ContentFile(resp.content), save=False)
        user.save(update_fields=["avatar"])
        logger.info("아바타 저장 완료: user_id=%s, file=%s", user.pk, filename)
    except Exception as e:
        logger.warning("아바타 저장 스킵: user_id=%s, err=%s", user.pk, e)

# =============================
# extra_data 로딩
# =============================
def _load_extra_data(user: User, sociallogin, provider: str | None = None):
    """
    1순위: 시그널 인자로 온 sociallogin.extra_data
    2순위: DB의 SocialAccount(provider로 필터링)에서 extra_data
    """
    logger.debug("▶ 소셜 추가데이터 로드: provider_hint=%r", provider)

    if sociallogin and getattr(sociallogin, "account", None):
        prov = provider or sociallogin.account.provider
        extra = sociallogin.account.extra_data or {}
        logger.debug("  - sociallogin 로드 성공: provider=%s, extra.keys=%s", prov, _keys(extra))
        if (prov or "").lower().startswith("kakao"):
            acc = extra.get("kakao_account") or {}
            profile = acc.get("profile") or {}
            props = extra.get("properties") or {}
            logger.debug("    · kakao_account.keys=%s, profile.keys=%s, properties.keys=%s",
                         _keys(acc), _keys(profile), _keys(props))
        elif (prov or "").lower().startswith("naver"):
            resp = extra.get("response") or extra
            logger.debug("    · naver.response.keys=%s", _keys(resp))
        return prov, extra

    qs = SocialAccount.objects.filter(user=user)
    if provider:
        qs = qs.filter(provider=provider)
    sa = qs.order_by("-id").first()
    prov = sa.provider if sa else (provider or "")
    extra = sa.extra_data if sa else {}
    logger.debug("  - DB 폴백: source=%s, provider=%s, extra.keys=%s",
                 "db" if sa else "none", prov, _keys(extra))
    return prov, extra

# =============================
# 프로필 채우기 (birth_date / phone만)
# =============================
def _fill_profile(user: User, sociallogin, *, set_name_if_empty=False, set_avatar_if_empty=True):
    """닉네임/아바타 + (네이버면 이메일/생일/전화) 저장.
       저장 대상 필드는 오직 User.birth_date/phone 또는 user.profile.birth_date/phone 입니다.
    """
    provider_hint = getattr(getattr(sociallogin, "account", None), "provider", None)
    logger.info("프로필 채우기 시작: user_id=%s, provider_hint=%r", user.pk, provider_hint)

    provider, extra = _load_extra_data(user, sociallogin, provider_hint)
    provider_norm = (provider or "").lower()
    logger.debug("  - 로드 완료: provider=%s, extra.keys=%s", provider_norm, _keys(extra))

    prof = _extract_profile(extra, provider)
    nickname  = prof.get("nickname")
    img_url   = prof.get("image_url")
    email     = prof.get("email")
    birthday  = prof.get("birthday")
    phone_val = prof.get("mobile")   # 네이버 'mobile'을 phone으로 저장

    logger.debug("  - 추출 요약: nickname=%r, image_url=%r, email=%r, birth=%r, phone=%r",
                 nickname, img_url, email, birthday, phone_val)

    fields_to_update: list[str] = []

    # 1) 표시 이름(비어있을 때만)
    if set_name_if_empty and (not getattr(user, "display_name", None)) and nickname:
        user.display_name = nickname
        fields_to_update.append("display_name")
        logger.info("  - display_name 채움: %r", nickname)

    # 2) 이메일(비어있을 때만; 폼 입력 우선)
    if email and not (getattr(user, "email", None) or "").strip():
        user.email = email
        fields_to_update.append("email")
        logger.info("  - email 채움(소셜): %r", email)

    # 3) 생일 — 오직 birth_date 필드만 (User → Profile 순)
    if birthday:
        parsed_bday = parse_date(birthday) or birthday
        if hasattr(user, "birth_date") and not getattr(user, "birth_date", None):
            user.birth_date = parsed_bday
            fields_to_update.append("birth_date")
            logger.info("  - birth_date 채움(User): %r", parsed_bday)
        elif hasattr(user, "profile") and hasattr(user.profile, "birth_date") \
                and not getattr(user.profile, "birth_date", None):
            user.profile.birth_date = parsed_bday
            try:
                user.profile.save(update_fields=["birth_date"])
            except Exception:
                user.profile.save()
            logger.info("  - birth_date 채움(Profile): %r", parsed_bday)
        else:
            logger.debug("  - birth_date 스킵(필드 없음/이미 값 있음)")
    else:
        logger.debug("  - birth_date 없음(소셜 미제공)")

    # 4) 전화 — 오직 phone 필드만 (User → Profile 순)
    if phone_val:
        if hasattr(user, "phone") and not getattr(user, "phone", None):
            user.phone = phone_val
            fields_to_update.append("phone")
            logger.info("  - phone 채움(User): %r", phone_val)
        elif hasattr(user, "profile") and hasattr(user.profile, "phone") \
                and not getattr(user.profile, "phone", None):
            user.profile.phone = phone_val
            try:
                user.profile.save(update_fields=["phone"])
            except Exception:
                user.profile.save()
            logger.info("  - phone 채움(Profile): %r", phone_val)
        else:
            logger.debug("  - phone 스킵(필드 없음/이미 값 있음)")
    else:
        logger.debug("  - phone 없음(소셜 미제공)")

    # 5) 아바타(비어있을 때만 저장) — 기본 이미지면 덮어쓰기 허용
    current_name = getattr(getattr(user, "avatar", None), "name", "") or ""
    DEFAULT_AVATAR_NAMES = {"default.jpg", "default.png", "avatars/default.png"}
    has_real_avatar = bool(current_name) and current_name not in DEFAULT_AVATAR_NAMES
    logger.debug("  - 아바타 현재파일=%r, 실이미지여부=%s", current_name, has_real_avatar)

    if set_avatar_if_empty and (not has_real_avatar) and img_url:
        if hasattr(user, "avatar"):
            _save_avatar_from_url(user, img_url, provider=provider_norm or "social")
        else:
            logger.debug("  - 아바타 저장 스킵: User에 avatar 필드 없음")

    # 최종 저장 (User)
    if fields_to_update:
        try:
            user.save(update_fields=fields_to_update)
        except Exception:
            user.save()
        logger.info("프로필 저장 완료: 업데이트 필드=%s", fields_to_update)
    else:
        logger.debug("프로필 저장 스킵: 업데이트할 User 필드 없음")

    # 저장 결과 확인 로그
    logger.debug(
        "저장 확인(User): email=%r, birth_date=%r, phone=%r, display_name=%r, avatar=%r",
        getattr(user, "email", None),
        getattr(user, "birth_date", None),
        getattr(user, "phone", None),
        getattr(user, "display_name", None),
        getattr(user, "avatar", None),
    )
    if hasattr(user, "profile"):
        logger.debug(
            "저장 확인(Profile): birth_date=%r, phone=%r",
            getattr(user.profile, "birth_date", None) if hasattr(user.profile, "birth_date") else None,
            getattr(user.profile, "phone", None) if hasattr(user.profile, "phone") else None,
        )

# =============================
# 시그널
# =============================
@receiver(user_signed_up)
def fill_on_signup(request, user, sociallogin=None, **kwargs):
    """가입 직후(커밋 후) 프로필 채우기 — 카카오/네이버 공통."""
    logger.info("user_signed_up: user_id=%s", user.pk)

    def _after_commit():
        logger.debug("트랜잭션 커밋 완료 → 프로필 채우기 호출")
        _fill_profile(user, sociallogin, set_name_if_empty=True, set_avatar_if_empty=True)

    transaction.on_commit(_after_commit)

@receiver(social_account_added)
def fill_on_connect(request, sociallogin, **kwargs):
    """계정에 소셜 '연결' 시 — 카카오/네이버 공통."""
    logger.info("social_account_added: user_id=%s, provider=%s",
                sociallogin.user.pk, sociallogin.account.provider)
    _fill_profile(sociallogin.user, sociallogin, set_name_if_empty=True, set_avatar_if_empty=True)
