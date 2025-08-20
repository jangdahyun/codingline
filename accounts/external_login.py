import json, logging, requests
from django.dispatch import receiver
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from django.db import transaction                      # ← 커밋 후 실행용
from allauth.account.signals import user_signed_up
from allauth.socialaccount.signals import social_account_added
from allauth.socialaccount.models import SocialAccount # ← DB 폴백용

logger = logging.getLogger("accounts")
User = get_user_model()

def _extract_kakao_profile(extra: dict):
    """카카오 응답에서 닉네임/이미지 URL 유연 추출(신/구 구조 모두)."""
    extra = extra or {}
    acc = extra.get("kakao_account") or {}
    profile = acc.get("profile") or {}
    props = extra.get("properties") or {}
    nickname = profile.get("nickname") or props.get("nickname")
    img = (
        profile.get("profile_image_url")
        or profile.get("thumbnail_image_url")
        or props.get("profile_image")
        or props.get("thumbnail_image")
    )
    return nickname, img

def _save_avatar_from_url(user: User, url: str, provider: str = "kakao"):
    """이미지 URL 다운로드 → user.avatar 저장. 실패해도 앱 중단 X."""
    if not url:
        return
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        ext = "jpg"
        for cand in (".jpg", ".jpeg", ".png", ".webp"):
            if cand in url.lower():
                ext = cand.lstrip("."); break
        filename = f"{provider}_{user.pk}.{ext}"
        user.avatar.save(filename, ContentFile(resp.content), save=False)
        user.save(update_fields=["avatar"])
        logger.info("avatar saved user_id=%s file=%s", user.pk, filename)
    except Exception as e:
        logger.warning("avatar skip user_id=%s err=%s", user.pk, e)

def _load_extra_data(user: User, sociallogin):
    """
    1순위: 시그널 인자로 온 sociallogin.extra_data
    2순위: DB의 SocialAccount(provider='kakao').extra_data
    """
    if sociallogin and getattr(sociallogin, "account", None):
        src = "sociallogin"
        extra = sociallogin.account.extra_data or {}
    else:
        sa = SocialAccount.objects.filter(user=user, provider="kakao").order_by("-id").first()
        src = "db" if sa else "none"
        extra = sa.extra_data if sa else {}
    logger.debug("extra_data loaded via %s = %s", src, json.dumps(extra, ensure_ascii=False))
    return extra

def _fill_profile(user: User, sociallogin, *, set_name_if_empty=False, set_avatar_if_empty=True):
    """닉네임/아바타 채우기(비어있을 때만)."""
    extra = _load_extra_data(user, sociallogin)
    nickname, img_url = _extract_kakao_profile(extra)

    if set_name_if_empty and (not getattr(user, "display_name", None)) and nickname:
        user.display_name = nickname
        user.save(update_fields=["display_name"])
        logger.info("set display_name user_id=%s nickname=%s", user.pk, nickname)

    if set_avatar_if_empty and (not getattr(user, "avatar", None)) and img_url:
        _save_avatar_from_url(user, img_url)

@receiver(user_signed_up)
def fill_on_signup(request, user, sociallogin=None, **kwargs):
    """
    가입 '직후' 시그널.
    - 일부 플로우에선 sociallogin이 None → DB에서 SocialAccount로 폴백.
    - 트랜잭션 커밋 후 실행하여 SocialAccount 저장 타이밍 이슈 방지.
    """
    logger.info("user_signed_up uid=%s", user.pk)

    def _after_commit():
        _fill_profile(user, sociallogin, set_name_if_empty=True, set_avatar_if_empty=True)

    transaction.on_commit(_after_commit)  # ← 커밋 완료 후 실행

@receiver(social_account_added)
def fill_on_connect(request, sociallogin, **kwargs):
    """
    내 계정에 카카오 '연결' 시.
    - 여기엔 항상 sociallogin이 옴.
    """
    logger.info("social_account_added uid=%s provider=%s",
                sociallogin.user.pk, sociallogin.account.provider)
    _fill_profile(sociallogin.user, sociallogin, set_name_if_empty=True, set_avatar_if_empty=True)
