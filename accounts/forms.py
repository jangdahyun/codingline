from allauth.account.forms import LoginForm  # allauth 기본 로그인 폼 상속
from django import forms

class MyLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)                     # 부모 초기화
        self.fields["login"].widget.attrs.update({            # 아이디/이메일 입력창 속성
            "class": "input w-full",
            "placeholder": "아이디 또는 이메일",
            "autocomplete": "username",
        })
        self.fields["password"].widget.attrs.update({         # 비번 입력창 속성
            "class": "input w-full",
            "placeholder": "비밀번호",
            "autocomplete": "current-password",
        })
