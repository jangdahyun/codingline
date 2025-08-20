from django.db import models                               # Django ORM 기본
from django.conf import settings                           # FK에 문자열 대신 안전하게 유저 모델을 참조하려고 이렇게 씀
from django.utils.text import slugify                      # ③ 한글/공백 → URL용 슬러그로

class Room(models.Model):                                  # ④ 방 테이블
    Romname = models.CharField(max_length=100)                # ⑤ 방 제목
    slug = models.SlugField(max_length=120, unique=True)   # ⑥ URL 식별자(고유)
    topic = models.CharField(max_length=50, default="general")  # ⑦ 주제(검색용)
    created_by = models.ForeignKey(                        # ⑧ 만든 사람(FK)
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE, # 유저가 삭제되면 그 유저가 만든 모든 방도 같이 삭제.
        related_name="rooms_created", # 역참조 이름(유저가 만든 방들)
    )
    is_private = models.BooleanField(default=False)        # ⑨ 비공개 여부(추후 접근제어에 사용)
    created_at = models.DateTimeField(auto_now_add=True)   # ⑩ 생성 시각

    def save(self, *args, **kwargs):                       # DB에 저장되기 직전에 호출되는 훅
        if not self.slug:
            base = slugify(self.Romname) or "room"
            s = base
            i = 1
            # ⑫ 같은 이름이 많아도 slug 고유 보장
            while Room.objects.filter(slug=s).exists():
                i += 1
                s = f"{base}-{i}"
            self.slug = s
        return super().save(*args, **kwargs)               # ⑬ 부모 save 호출(실제 INSERT/UPDATE)

    def __str__(self):                                     # ⑭ 관리자/쉘에서 보기 좋게
        return self.Romname
