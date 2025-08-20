from django.db import models
from django.contrib.auth.models import AbstractUser            #장고 기본 User 확장용

# Create your models here.

class User(AbstractUser):                                      #장고 기본 User 모델 확장
     # AbstractUser 안에는 username, password(해시 저장), email, is_staff 등 기본 필드 포함

    email = models.EmailField(unique=True)                     # 이메일 고유(중복 가입 방지)
    display_name = models.CharField(max_length=50, blank=True,null= True, unique= True,db_collation='utf8mb4_0900_ai_ci', ) # 5) 닉네임(옵션)
    birth_date = models.DateField(null=True, blank=True)       # 생년월일(옵션)
    phone = models.CharField(max_length=16, null=True, blank=True, unique=True)  # 전화(옵션/고유)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)      # 프로필 이미지(옵션)

    def __str__(self):                                         
        return self.display_name or self.username              # 닉네임 없으면 username 사용
