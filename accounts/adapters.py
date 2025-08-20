import json, logging
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger("accounts")

def _extract_kakao_profile(extra: dict):
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

class MySocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        """
        소셜 → User 인스턴스가 저장되기 '직전'에 호출됨.
        여기서 display_name을 세팅하면 누락이 생기지 않음.
        """
        user = super().populate_user(request, sociallogin, data)
        try:
            extra = sociallogin.account.extra_data or {}
            logger.debug("populate_user extra=%s", json.dumps(extra, ensure_ascii=False))
            nickname, _ = _extract_kakao_profile(extra)
            if not getattr(user, "display_name", None) and nickname:
                user.display_name = nickname
                logger.info("populate_user: set display_name=%s", nickname)
        except Exception as e:
            logger.warning("populate_user error: %s", e)
        return user
