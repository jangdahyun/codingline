from django.contrib import admin
from .models import Room

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("id", "Romname", "topic", "is_private", "created_by", "created_at")  # ✅
    search_fields = ("Romname", "topic", "created_by__username")                         # ✅
    list_filter = ("is_private", "created_at")
    ordering = ("-created_at",)
