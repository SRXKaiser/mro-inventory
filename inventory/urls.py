from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("items/", views.item_list, name="item_list"),
    path("items/new/", views.item_create, name="item_create"),
    path("items/<int:item_id>/", views.item_detail, name="item_detail"),
    path("items/<int:item_id>/edit/", views.item_edit, name="item_edit"),

    path("movements/new/", views.movement_create, name="movement_create"),
]
