# accounts/views.py
import logging
from allauth.socialaccount.views import SignupView as AllauthSocialSignupView

logger = logging.getLogger("accounts")

class CustomSocialSignupView(AllauthSocialSignupView):
    # 템플릿 실제 위치에 맞춰주세요.
    # 에러 메시지에 'templates/account/3rdparty_signup.html' 로 나왔으니:
    template_name = "account/3rdparty_signup.html"

    def get_context_data(self, **kwargs):
        """
        allauth가 view 인스턴스에 넣어둔 self.sociallogin 을
        템플릿 컨텍스트로 '확실히' 전달.
        """
        ctx = super().get_context_data(**kwargs)

        sl = getattr(self, "sociallogin", None)  # ← allauth가 채워줌
        if sl:
            ctx["sociallogin"] = sl                          # 템플릿에서 그대로 사용 가능
            if getattr(sl, "account", None):
                ctx["provider_id"] = sl.account.provider     # 'kakao' / 'naver'
                ctx["extra_data"] = sl.account.extra_data    # 원본 JSON
                logger.info(f"CustomSocialSignupView: sociallogin 있음(provider={ctx['extra_data']})")
        else:
            logger.info("CustomSocialSignupView: sociallogin 없음(직접 접근/세션만료 가능성)")

        return ctx
