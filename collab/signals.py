import logging
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from django.db import transaction
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Room
from logui import log_step

logger = logging.getLogger("lobby")  # ✅ 앱 이름 맞게 로거 생성


def _broadcast(payload:dict):
    async_to_sync(get_channel_layer().group_send)(
        "lobby",
        {"type": "lobby.event", "payload": payload}
    )
    log_step(logger, "로비 이벤트 브로드캐스트", "_broadcast", {"payload": payload})

@receiver(post_save, sender=Room)
def on_room_save(sender, instance: Room, created:bool, **kwargs):
    def _after_commit():
        if created:
            _broadcast({"event": "room_created", "room_slug": instance.slug,"room_id": instance.id,"ceated_at": instance.created_at.isoformat(),"topic": instance.topic})
            log_step(logger, "로비 이벤트 브로드캐스트", "방생성", {"event": "room_created", "room_slug": instance.slug,"room_id": instance.id})
        else:
            _broadcast({"event": "room_updated", "room_slug": instance.slug})
            log_step(logger, "로비 이벤트 브로드캐스트", "방수정", {"event": "room_updated", "room_slug": instance.slug})

    transaction.on_commit(_after_commit)

@receiver(pre_delete, sender=Room)
def on_room_pre_delete(sender, instance: Room, **kwargs):
    instance._deleted_id = instance.id  


@receiver(post_delete, sender=Room)
def on_room_delete(sender, instance: Room, **kwargs):
    def _after_commit():
        _broadcast({"event": "room_deleted", "room_slug": instance.slug,"room_id": instance._deleted_id})
        log_step(logger, "로비 이벤트 브로드캐스트", "방삭제", {"event": "room_deleted", "room_slug": instance.slug,"room_id": instance.id})

    transaction.on_commit(_after_commit)