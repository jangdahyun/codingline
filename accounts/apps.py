# accounts/apps.py
from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules
import logging

logger = logging.getLogger("accounts")

class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        logger.info("AccountsConfig.ready() called")        # ★ 실행 확인
        autodiscover_modules("external_login")
        logger.info("accounts.external_login imported")     # ★ 모듈 임포트 확인
