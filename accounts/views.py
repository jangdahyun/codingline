# accounts/views.py
from enum import member
import logging
from allauth.socialaccount.views import SignupView as AllauthSocialSignupView
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.auth import get_user_model
from django.shortcuts import render, redirect
from allauth.account.views import PasswordChangeView as AllauthPasswordChangeView
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import UsernameFindForm, PasswordResetVerifyForm, PasswordResetSetForm, ProfileUpdateForm

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
    
    @login_required
    def mypage(request):
        user = request.user
        if request.method == 'POST':
            form = ProfileUpdateForm(request.POST, request.FILES,instance=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, '프로필이 성공적으로 업데이트되었습니다.')
                return redirect('mypage')
        else:
            form = ProfileUpdateForm(instance=request.user)

        rooms_created = user.rooms_created.order_by('-created_at')
        memberships = (user.room_memberships
                      .select_related("room")
                      .filter(is_banned=False)
                      .order_by("-joined_at"))

        context = {
            'user': user,
            "profile_form": form,
            "rooms_created": rooms_created,
            "memberships": memberships,
            "show_profile_modal": request.method == "POST" and not form.is_valid(),
        }
        
        return render(request, 'account/mypage.html',context)

class MyPasswordChangeView(AllauthPasswordChangeView):
    template_name = "account/password_change.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        super().form_valid(form)
        logout(self.request)
        messages.info(self.request, "비밀번호가 변경되었습니다. 다시 로그인해주세요.")
        return redirect(self.get_success_url())

class UsernameFindView(FormView):
    template_name = "account/find_id.html"
    form_class = UsernameFindForm
    success_url = reverse_lazy("account_find_id")

    def form_valid(self, form):
        storage= messages.get_messages(self.request)
        for _ in storage:
            pass # 기존 메시지 제거
        user = form.user
        messages.success(self.request, f"가입된 아이디는 '{user.username}'입니다.")
        logger.info(f"UsernameFindView: 아이디 찾기 성공(username={user.username})")
        return super().form_valid(form)
    
class PasswordResetVerifyView(FormView):
    template_name = "account/reset_password_verify.html"
    form_class = PasswordResetVerifyForm
    success_url = reverse_lazy("account_reset_password_set")

    def form_valid(self, form):
        user = form.user
        self.request.session["reset_user_id"] = user.id
        return redirect(self.get_success_url())

class PasswordResetSetView(FormView):
    template_name = "account/reset_password_set.html"
    form_class = PasswordResetSetForm
    success_url = reverse_lazy("account_login")

    def dispatch(self, request, *args, **kwargs):
        user_id = request.session.get("reset_user_id")
        if not user_id:
            messages.error(request, "비밀번호 재설정을 할 수 없습니다. 다시 시도해주세요.")
            return redirect("account_login")
        User = get_user_model()
        try:
            self.user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            messages.error(request, "비밀번호 재설정을 할 수 없습니다. 다시 시도해주세요.")
            return redirect("account_login")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.user
        return kwargs

    def form_valid(self, form):
        form.save()
        self.request.session.pop("reset_user_id", None) #세션에서 사용자 ID 제거
        logout(self.request)
        messages.success(self.request, "비밀번호가 성공적으로 변경되었습니다. 다시 로그인해주세요.")
        return super().form_valid(form)
